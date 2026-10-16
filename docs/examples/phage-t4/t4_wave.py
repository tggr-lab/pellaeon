# Phage T4 tail contraction wave for UCSF ChimeraX.
# SPDX-License-Identifier: MIT. Copyright (c) 2026 Yam Amir, TGGR Lab. Part of the Pellaeon examples.
# The idea of a wave running up a helical sheath follows T. Goddard's 2022 ChimeraX phage T4 animation;
# this is separate code with T4 values measured from 9MKB and 9X3P.
#
# Needs: 9MKB open (tail, any model number). The 7VS5 head is optional; if open, it is carried with the neck.
# Run:   runscript t4_wave.py        (fetches 9X3P, 2.7 MB; about 2 s once downloaded, under 0.5 GB extra memory)
# Then:  t4wave 0.5                  (state: 0 extended, 1 contracted)
#        t4wave play 240             (0 to 1 over 240 frames; works inside movie record)
#
# Each of the 23 gp18 rings has a progress s in [0, 1]. A smooth front runs from the baseplate ring to the neck
# ring. Ring frames are stacked per-step screws whose rise and twist go from the extended values (9MKB) to the
# contracted ones (9X3P), so the rings above a contracting ring are carried. Subunit shapes are interpolated in
# the ring frame. Tube, neck, needle, fibritin, Hoc and the head move rigidly with the top ring. The baseplate
# stays. Exact at both ends: t=0 is 9MKB, t=1 is 9X3P for every atom with a partner.
import os
import sys
import numpy as np
from chimerax.geometry import Place, Places, rotation, translation

# chains that move with the top ring (gp23/Soc/Hoc: capsid chains of 9MKB, if they were not deleted)
MOVE = {'gp19', 'gp3', 'gp15', 'gp14', 'gp13', 'gp20', 'gpwac', 'Hoc', 'gp27', 'gp5', 'gp5.4', 'gp48', 'gp54',
        'gp29', 'gp23', 'Soc'}
FIXED = {'gp6', 'gp7', 'gp8', 'gp9', 'gp10', 'gp11', 'gp12', 'gp25', 'gp53'}


# ---------------------------------------------------------------- geometry
def token(ch):
    return ch.description.split(',')[0]


def ca(ch):
    a = ch.existing_residues.atoms
    a = a.filter(a.names == 'CA')
    return dict(zip(a.residues.numbers.tolist(), a.coords))


def paired(d1, d2):
    k = sorted(set(d1) & set(d2))
    return np.array([d1[i] for i in k]), np.array([d2[i] for i in k]), k


def kabsch(P, Q):
    pc, qc = P.mean(0), Q.mean(0)
    U, S, Vt = np.linalg.svd((P - pc).T @ (Q - qc))
    D = np.diag([1, 1, np.sign(np.linalg.det(Vt.T @ U.T))])
    R = Vt.T @ D @ U.T
    M = np.zeros((3, 4)); M[:, :3] = R; M[:, 3] = qc - R @ pc
    return Place(matrix=M)


def kabsch_pruned(P, Q, cutoff=2.0, iters=10):
    """Fit, drop pairs farther than cutoff, refit (as matchmaker does). Returns T with T*P ~ Q."""
    T = kabsch(P, Q)
    keep = np.ones(len(P), bool)
    for _ in range(iters):
        new = np.linalg.norm(T.transform_points(P) - Q, axis=1) < cutoff
        if new.sum() < 0.2 * len(P) or (new == keep).all():
            break
        keep = new
        T = kabsch(P[keep], Q[keep])
    return T


def line_fit(points):
    c = points.mean(0)
    u = np.linalg.svd(points - c)[2][0]
    return c, u / np.linalg.norm(u)


def frame(origin, zdir, xhint):
    z = zdir / np.linalg.norm(zdir)
    x = xhint - (xhint @ z) * z
    x /= np.linalg.norm(x)
    M = np.zeros((3, 4)); M[:, 0] = x; M[:, 1] = np.cross(z, x); M[:, 2] = z; M[:, 3] = origin
    return Place(matrix=M)


def screw(T):
    """Rigid T as (axis u, angle deg, shift along u, point on axis)."""
    R = T.matrix[:, :3]; t = T.matrix[:, 3]
    ang = np.degrees(np.arccos(np.clip((np.trace(R) - 1) / 2, -1, 1)))
    w = np.array([R[2, 1] - R[1, 2], R[0, 2] - R[2, 0], R[1, 0] - R[0, 1]])
    if np.linalg.norm(w) < 1e-9:
        n = np.linalg.norm(t)
        return (t / n if n > 1e-9 else np.array([0, 0, 1.])), 0.0, float(n), np.zeros(3)
    u = w / np.linalg.norm(w)
    d = float(t @ u)
    p = np.linalg.lstsq(np.eye(3) - R, t - d * u, rcond=None)[0]
    return u, float(ang), d, p - (p @ u) * u


def screw_power(T, s):
    """T to the power s (0..1) as a screw motion."""
    if s <= 0:
        return Place()
    if s >= 1:
        return T
    u, ang, d, p = screw(T)
    return translation(s * d * u) * rotation(u, s * ang, p)


def screw_about(T, u):
    au, ang, d, p = screw(T)
    s = 1.0 if au @ u >= 0 else -1.0
    return s * ang, s * d


def smoothstep(x):
    x = np.clip(x, 0.0, 1.0)
    return x * x * (3 - 2 * x)


# ---------------------------------------------------------------- models
def find_models(session):
    from chimerax.atomic import AtomicStructure
    ms = [m for m in session.models.list() if isinstance(m, AtomicStructure)]
    tails = [m for m in ms if sum(token(c) == 'gp18' for c in m.chains) == 138]
    heads = [m for m in ms if any(c.description.startswith('Major capsid') for c in m.chains)]
    if not tails:
        from chimerax.core.errors import UserError
        raise UserError('Open 9MKB first (the tail with 138 gp18 chains).')
    return tails[0], (heads[0] if heads else None)


# ---------------------------------------------------------------- build
def build(session, log=True):
    """Fit 9X3P on the open 9MKB tail and return a Wave. All geometry is in the tail's own coordinates."""
    from chimerax.core.commands import run
    tail, head = find_models(session)
    TP = tail.position
    chains = list(tail.chains)
    gp18 = [ch for ch in chains if token(ch) == 'gp18']
    cen = lambda ch: ch.existing_residues.atoms.coords.mean(0)
    c18 = np.array([cen(ch) for ch in gp18])
    tc, tu = line_fit(c18)
    portal = np.concatenate([ch.existing_residues.atoms.coords for ch in chains if token(ch) == 'gp20']).mean(0)
    if (c18.mean(0) - portal) @ tu < 0:
        tu = -tu                                            # tu points from the portal to the baseplate
    order = np.argsort((c18 - tc) @ tu)[::-1]               # baseplate ring first
    rings_e = [[gp18[i] for i in order[6 * k:6 * k + 6]] for k in range(23)]
    ringc = np.array([c18[order[6 * k:6 * k + 6]].mean(0) for k in range(23)])
    tc, u2 = line_fit(ringc)
    tu = u2 if u2 @ tu > 0 else -u2
    Pt = tc + ((portal - tc) @ tu) * tu
    e1 = np.cross(tu, [0, 0, 1.0]); e1 /= np.linalg.norm(e1); e2 = np.cross(tu, e1)

    def azz(x):
        v = np.atleast_2d(x) - Pt
        return np.degrees(np.arctan2(v @ e2, v @ e1)), v @ tu

    # contracted sheath 9X3P as six graphical instances (no copies), hidden, used as the reference
    sh = run(session, 'open 9x3p', log=log)[0]
    sh.name = '9x3p contracted sheath (reference)'
    run(session, 'sym #%s assembly 1 copies false newModel false' % sh.id_string, log=log)
    Q0 = list(sh.positions)
    qu, _, _, qp = screw(Q0[1] * Q0[0].inverse())
    sheath_asu = [ch for ch in sh.chains if ch.description.startswith('Tail sheath')]
    gp3c = np.concatenate([ch.existing_residues.atoms.coords for ch in sh.chains
                           if ch.description.startswith('Tail tube terminator')]).mean(0)
    sc = np.array([cen(ch) for ch in sheath_asu])
    down = -qu if (gp3c - qp) @ qu > (sc.mean(0) - qp) @ qu else qu
    sheath_asu = [sheath_asu[i] for i in np.argsort((sc - qp) @ down)[::-1]]
    xt = np.cross(tu, [1, 0, 0])
    R0 = frame(Pt, tu, xt) * frame(qp, down, np.cross(down, [0.3, 0.5, 0.8])).inverse()

    # baseplate end fixed: rotation about and shift along the tail axis fitted on the ring-1 residues
    # that touch the baseplate in 9MKB (CA within 8 A)
    eb = [ca(ch) for ch in rings_e[0]]
    cb = ca(sheath_asu[0])
    cbc = np.array(list(cb.values())).mean(0)
    ae_b, _ = azz(np.array([np.array(list(d.values())).mean(0) for d in eb]))
    from scipy.spatial import cKDTree
    bp_tok = ('gp6', 'gp7', 'gp8', 'gp9', 'gp10', 'gp11', 'gp12', 'gp25', 'gp53', 'gp48', 'gp54', 'gp27')
    bptree = cKDTree(np.concatenate([ch.existing_residues.atoms.coords for ch in chains if token(ch) in bp_tok]))
    keys0 = paired(cb, eb[0])[2]
    contact = sorted({r for e in eb for r in keys0 if bptree.query(e[r], distance_upper_bound=8.0)[0] < 8.0})
    keep = np.tile(np.isin(keys0, contact), 6)

    def bottom_pairs(A):
        ac, _ = azz(np.array([(A * Q).transform_points(cbc[None])[0] for Q in Q0]))
        P_, Q_ = [], []
        for i in range(6):
            j = int(np.argmin(np.abs((ac - ae_b[i] + 180) % 360 - 180)))
            P, Q, _ = paired(cb, eb[i])
            P_.append((A * Q0[j]).transform_points(P)); Q_.append(Q)
        return np.concatenate(P_), np.concatenate(Q_)

    def bottom_fit(theta):
        P, Q = bottom_pairs(rotation(tu, theta, Pt) * R0)
        dz = float(((Q[keep] - P[keep]) @ tu).mean())
        d = np.linalg.norm(P + dz * tu - Q, axis=1)
        return float(np.sqrt((d[keep] ** 2).mean())), dz

    th0 = min((bottom_fit(t)[0], t) for t in np.arange(0, 60, 0.5))[1]
    thb = min((bottom_fit(t)[0], t) for t in np.arange(th0 - 0.5, th0 + 0.5, 0.01))[1]
    A = translation(bottom_fit(thb)[1] * tu) * rotation(tu, thb, Pt) * R0
    shp = [A * Q for Q in Q0]                               # 9X3P instances in tail coordinates
    sh.positions = Places([TP * p for p in shp])
    sh.display = False

    # ring helix: phase, twist and height of each ring, extended and contracted
    def ring_phase(phis):
        return float(np.degrees(np.angle(np.exp(1j * np.radians(6 * np.asarray(phis))).sum())) / 6)

    ext_cent = [np.array([cen(ch) for ch in r]) for r in rings_e]
    ext_phi = [azz(x)[0] for x in ext_cent]
    ext_z = np.array([azz(x)[1].mean() for x in ext_cent])
    con_cent = [np.array([shp[j].transform_points(cen(ch)[None])[0] for j in range(6)]) for ch in sheath_asu]
    con_phi = [azz(x)[0] for x in con_cent]
    con_z = np.array([azz(x)[1].mean() for x in con_cent])
    ae_ = np.array([ring_phase(p) for p in ext_phi]); ac_ = np.array([ring_phase(p) for p in con_phi])
    de = np.diff(ae_) % 60; dc = np.diff(ac_) % 60
    sign = 1 if np.median(de) < 30 else -1
    tw_e = de if sign > 0 else de - 60
    tw_c = dc if sign > 0 else dc - 60
    delta1 = ((ac_[0] - ae_[0]) + 30) % 60 - 30
    phi_e = ae_[0] + np.concatenate([[0], np.cumsum(tw_e)])
    phi_c = ae_[0] + delta1 + np.concatenate([[0], np.cumsum(tw_c)])
    dphi = phi_c - phi_e
    corr = []
    for k in range(23):
        used = set()
        for i, ch in enumerate(rings_e[k]):
            dd = np.abs((con_phi[k] - (ext_phi[k][i] + dphi[k]) + 180) % 360 - 180)
            j = int(np.argmin(dd)); used.add(j)
            corr.append((k, ch, j))
        assert len(used) == 6, 'ring %d: subunit correspondence is not one to one' % k

    # tube and neck: one rigid fit of 9MKB gp3 + gp19 onto 9X3P gp3 + gp19
    Fe = lambda k: translation(ext_z[k] * tu) * rotation(tu, phi_e[k], Pt)
    Fc = lambda k: translation(con_z[k] * tu) * rotation(tu, phi_c[k], Pt)
    Mraw = Fc(22) * Fe(22).inverse()
    con_rc = [((j, ch), shp[j].transform_points(cen(ch)[None])[0])
              for ch in sh.chains if not ch.description.startswith('Tail sheath') for j in range(6)]
    Ps, Qs = [], []
    for ch in [ch for ch in chains if token(ch) in ('gp3', 'gp19')]:
        pred = Mraw.transform_points(cen(ch)[None])[0]
        dd = [np.linalg.norm(x - pred) for _, x in con_rc]
        i = int(np.argmin(dd))
        if dd[i] < 15:
            j, cch = con_rc[i][0]
            P, Q, _ = paired(ca(ch), ca(cch))
            Ps.append(P); Qs.append(shp[j].transform_points(Q))
    Ttube = kabsch_pruned(np.concatenate(Ps), np.concatenate(Qs))

    # moving and fixed parts
    moving = []
    for ch in chains:
        t_ = token(ch)
        if t_ == 'gp18' or t_ in FIXED:
            continue
        if t_ in MOVE:
            moving.append(ch)
        else:                                   # unassigned peptides: inside the tube radius they move
            v = cen(ch) - Pt
            if np.linalg.norm(v - (v @ tu) * tu) < 50:
                moving.append(ch)

    # sheath atoms in their ring frame, extended and contracted
    ring_bounds, sheath_ids, Le, Lc = [], [], [], []
    n0 = 0
    for k in range(23):
        start = n0
        cat = sheath_asu[k].existing_residues.atoms
        cnum, cname = cat.residues.numbers.tolist(), cat.names.tolist()
        key = {(r, n): i for i, (r, n) in enumerate(zip(cnum, cname))}
        cak = {r: i for i, (r, n) in enumerate(zip(cnum, cname)) if n == 'CA'}
        for kk, ch, j in [x for x in corr if x[0] == k]:
            atoms = ch.existing_residues.atoms
            xe = atoms.coords
            cx = shp[j].transform_points(cat.coords)
            enum_, ename = atoms.residues.numbers.tolist(), atoms.names.tolist()
            ecak = {r: i for i, (r, n) in enumerate(zip(enum_, ename)) if n == 'CA'}
            common = np.array(sorted(set(cak) & set(ecak)))
            xc = np.empty_like(xe)
            for i, (r, n) in enumerate(zip(enum_, ename)):
                m = key.get((r, n))
                if m is not None:
                    xc[i] = cx[m]
                else:                           # no partner atom in 9X3P: follow the residue's (or nearest) CA
                    rr = r if (r in cak and r in ecak) else int(common[np.argmin(np.abs(common - r))])
                    xc[i] = xe[i] + cx[cak[rr]] - xe[ecak[rr]]
            Le.append(Fe(k).inverse().transform_points(xe)); Lc.append(Fc(k).inverse().transform_points(xc))
            sheath_ids.append(ch.chain_id); n0 += len(xe)
        ring_bounds.append((start, n0))
    from chimerax.atomic import concatenate
    mov_atoms = concatenate([ch.existing_residues.atoms for ch in moving])
    D = dict(Le=np.concatenate(Le), Lc=np.concatenate(Lc), Xm=mov_atoms.coords,
             sheath_chains=sheath_ids, moving_chains=[ch.chain_id for ch in moving], ring_bounds=ring_bounds,
             axis_point=Pt, axis_u=tu, phi_e=phi_e, z_e=ext_z, phi_c=phi_c, z_c=con_z,
             top_correction=Ttube * Mraw.inverse(), sheath_model=sh)
    return Wave(session, tail, head, D)


class Wave:
    def __init__(self, session, tail, head, D):
        self.session, self.tail, self.head, self.D = session, tail, head, D
        byid = {ch.chain_id: ch for ch in tail.chains}
        from chimerax.atomic import concatenate
        self.sheath = concatenate([byid[c].existing_residues.atoms for c in D['sheath_chains']])
        self.moving = concatenate([byid[c].existing_residues.atoms for c in D['moving_chains']])
        self.ring_slices = [slice(a, b) for a, b in D['ring_bounds']]
        self.P, self.u = D['axis_point'], D['axis_u']
        TPi = tail.position.inverse()
        self.head_rel = [TPi * p for p in head.positions] if head is not None else []
        self.t = 0.0

    def frame(self, phi, z):
        return translation(z * self.u) * rotation(self.u, phi, self.P)

    def ring_progress(self, t, wavelength=0.5):
        n = len(self.ring_slices)
        x = np.arange(n) / (n - 1)                       # 0 at the baseplate ring, 1 at the neck ring
        return smoothstep((t * (1 + wavelength) - x) / wavelength)

    def set(self, t, wavelength=0.5):
        D = self.D
        s = self.ring_progress(t, wavelength)
        pe, ze, pc, zc = D['phi_e'], D['z_e'], D['phi_c'], D['z_c']
        n = len(s)
        phi = np.empty(n); z = np.empty(n)
        phi[0] = (1 - s[0]) * pe[0] + s[0] * pc[0]
        z[0] = (1 - s[0]) * ze[0] + s[0] * zc[0]
        for k in range(1, n):                            # stack the per-step screws from the baseplate up
            g = 0.5 * (s[k - 1] + s[k])
            phi[k] = phi[k - 1] + (1 - g) * (pe[k] - pe[k - 1]) + g * (pc[k] - pc[k - 1])
            z[k] = z[k - 1] + (1 - g) * (ze[k] - ze[k - 1]) + g * (zc[k] - zc[k - 1])
        Le, Lc = D['Le'], D['Lc']
        xyz = np.empty_like(Le)
        for k, sl in enumerate(self.ring_slices):
            xyz[sl] = self.frame(phi[k], z[k]).transform_points((1 - s[k]) * Le[sl] + s[k] * Lc[sl])
        self.sheath.coords = xyz
        top = n - 1
        M = screw_power(D['top_correction'], s[top]) * self.frame(phi[top], z[top]) * self.frame(pe[top], ze[top]).inverse()
        self.moving.coords = M.transform_points(D['Xm'])
        if self.head is not None:
            TP = self.tail.position
            self.head.positions = Places([TP * M * p for p in self.head_rel])
        self.t = t
        return s, M


# ---------------------------------------------------------------- commands
def play(session, setter, t0, t1, frames):
    """Step a setter from t0 to t1, one step per drawn frame (so movie record captures every step)."""
    state = {'i': 0}

    def step(*_):
        state['i'] += 1
        f = min(1.0, state['i'] / float(max(1, frames)))
        setter(t0 + (t1 - t0) * f)
        if f >= 1.0:
            from chimerax.core.triggerset import DEREGISTER
            return DEREGISTER
    session.triggers.add_handler('new frame', step)


def wave_of(session):
    w = getattr(session, 'phage_t4', {}).get('wave')
    if w is None or w.tail.deleted:
        from chimerax.core.errors import UserError
        raise UserError('Run t4_wave.py first.')
    return w


def register_commands(session):
    from chimerax.core.commands import register, CmdDesc, FloatArg, IntArg

    def t4wave(session, t):
        wave_of(session).set(float(np.clip(t, 0, 1)))

    def t4wave_play(session, frames=240):
        w = wave_of(session)
        play(session, w.set, w.t, 1.0 if w.t < 1.0 else 0.0, frames)

    register('t4wave', CmdDesc(required=[('t', FloatArg)], synopsis='T4 sheath state, 0 extended to 1 contracted'), t4wave, logger=session.logger)
    register('t4wave play', CmdDesc(optional=[('frames', IntArg)], synopsis='play the T4 contraction wave'), t4wave_play, logger=session.logger)


def main(session):
    import time
    t0 = time.time()
    w = build(session)
    if not hasattr(session, 'phage_t4'):
        session.phage_t4 = {}
    session.phage_t4['wave'] = w
    register_commands(session)
    session.logger.info('T4 wave ready in %.0f s: %d sheath atoms, %d moving atoms%s. Try: t4wave play 240'
                        % (time.time() - t0, len(w.sheath), len(w.moving), ', head carried' if w.head is not None else ''))


if __name__ != 't4_wave':                 # run as a script (runscript or open), not imported by t4_trigger.py
    main(session)                         # noqa: F821  (session is defined by ChimeraX)
