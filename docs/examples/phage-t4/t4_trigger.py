# Phage T4 baseplate trigger (hexagon to star) followed by the contraction wave, for UCSF ChimeraX.
# SPDX-License-Identifier: MIT. Copyright (c) 2026 Yam Amir, TGGR Lab. Part of the Pellaeon examples.
#
# Needs: 9MKB open; t4_wave.py in the same folder (run first or built here). The 7VS5 head is optional.
# Run:   runscript t4_trigger.py     (fetches 9X3Q 0.5 MB, 9X3R 1 MB, 5IV7 7 MB; about 10 s once downloaded)
# Then:  t4motion 0.5                (T: 0 to 1 trigger, 1 to 2 contraction)
#        t4motion play 480           (0 to 2 over 480 frames)
#
# Per chain: x(s) = T^s [ (1-s) X0 + s T^-1 X1 ], X0 = 9MKB (pre-trigger), X1 = post-trigger coordinates
# (9X3Q inner baseplate + 9X3R peripheral wedge), T = pruned CA fit of the chain, T^s a screw motion.
# Exact at s=0 and s=1 for matched atoms. Atoms with no post partner follow their nearest mapped CA.
# Placement: 9X3Q on the placed 9X3P through the shared tube rings; 9X3R on 9X3Q through the complete 5IV7
# star baseplate (both fitted onto the same 5IV7 wedge; 5IV7 is closed afterwards).
# Not modelled: no extended or post-trigger model of the gp12 short fibres exists. Here they keep their folded
# shape and swing rigidly with their gp11 trimer (schematic). gp25, the sheath initiator, changes with sheath
# ring 1 instead of the trigger clock (fewer clashes). Unassigned 9MKB peptides B-E are hidden after mid-trigger.
import os
import sys
from collections import defaultdict
import numpy as np
from chimerax.geometry import Place, Places

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import t4_wave as W  # noqa: E402

BP = ('gp6', 'gp7', 'gp8', 'gp9', 'gp10', 'gp11', 'gp25', 'gp53')


def post_name(ch):
    w = ch.description.split()[-1]
    return w if w.startswith('gp') else None


def build(session, wave, log=True):
    from chimerax.core.commands import run
    from scipy.spatial import cKDTree
    from scipy.optimize import linear_sum_assignment
    ca, paired, kabsch, kp = W.ca, W.paired, W.kabsch, W.kabsch_pruned
    tail, D = wave.tail, wave.D
    TP = tail.position
    sh = D['sheath_model']
    shp = [TP.inverse() * p for p in sh.positions]          # 9X3P instances in tail coordinates
    q = run(session, 'open 9x3q', log=log)[0]; q.name = '9x3q post-trigger inner baseplate (reference)'
    r = run(session, 'open 9x3r', log=log)[0]; r.name = '9x3r post-trigger wedge (reference)'
    run(session, 'sym #%s assembly 1 copies false newModel false' % q.id_string, log=log)
    Q0 = list(q.positions)

    # 9X3Q onto the placed 9X3P through the tube rings; the register with fewest clashes against the sheath
    p19 = [ch for ch in sh.chains if 'tube protein' in ch.description.lower()]
    psh = [ch for ch in sh.chains if ch.description.startswith('Tail sheath')]
    q19 = [ch for ch in q.chains if 'gp19' in ch.description]
    pinst = [(j, ch) for ch in p19 for j in range(len(shp))]
    pcen = np.array([shp[j].transform_points(ch.existing_residues.atoms.coords.mean(0)[None])[0] for j, ch in pinst])
    px_tree = cKDTree(np.concatenate([P.transform_points(np.concatenate([ch.existing_residues.atoms.coords for ch in psh])) for P in shp]))
    qa_no19 = np.concatenate([ch.existing_residues.atoms.coords for ch in q.chains if 'gp19' not in ch.description])
    best = None
    for jp, pc in pinst:
        X, Y, _ = paired(ca(q19[0]), ca(pc))
        T = kabsch(X, shp[jp].transform_points(Y))
        Ps, Qs = [], []
        for j, ch in [(j, ch) for ch in q19 for j in range(6)]:
            cq = (T * Q0[j]).transform_points(ch.existing_residues.atoms.coords.mean(0)[None])[0]
            i = int(np.argmin(np.linalg.norm(pcen - cq, axis=1)))
            if np.linalg.norm(pcen[i] - cq) < 15:
                X, Y, _ = paired(ca(ch), ca(pinst[i][1]))
                Ps.append((T * Q0[j]).transform_points(X)); Qs.append(shp[pinst[i][0]].transform_points(Y))
        if len(Ps) < 12:
            continue
        Tq = kp(np.concatenate(Ps), np.concatenate(Qs)) * T
        d, _ = px_tree.query(np.concatenate([(Tq * P).transform_points(qa_no19) for P in Q0]), distance_upper_bound=6)
        score = int((d < 2).sum())
        if best is None or score < best[0]:
            best = (score, Tq)
    Tq = best[1]

    # 9X3R onto 9X3Q through 5IV7: each fitted on the same 5IV7 wedge
    s5 = run(session, 'open 5iv7', log=log)[0]
    s5ch = defaultdict(list)
    for ch in s5.chains:
        s5ch[ch.description.split()[-1]].append(ch)
    s5cen = {ch.chain_id: ch.existing_residues.atoms.coords.mean(0) for ch in s5.chains}
    s5id = {ch.chain_id: ch for ch in s5.chains}

    def fit_onto_5iv7(model, seed_chain, seed_target, skip):
        chs = [ch for ch in model.chains if post_name(ch) not in skip]
        X, Y, _ = paired(ca(seed_chain), ca(seed_target))
        T = kabsch(X, Y)
        for _ in range(3):
            Ps, Qs, pairs = [], [], {}
            for ch in chs:
                cc = T.transform_points(ch.existing_residues.atoms.coords.mean(0)[None])[0]
                tgt = min(s5ch[post_name(ch)], key=lambda t: np.linalg.norm(s5cen[t.chain_id] - cc))
                X, Y, _ = paired(ca(ch), ca(tgt)); Ps.append(X); Qs.append(Y); pairs[ch.chain_id] = tgt.chain_id
            T = kp(np.concatenate(Ps), np.concatenate(Qs), cutoff=3.0)
        return T, pairs

    q7 = [ch for ch in q.chains if 'gp7' in ch.description][0]
    r7 = [ch for ch in r.chains if 'gp7' in ch.description][0]
    q53 = [ch for ch in q.chains if post_name(ch) == 'gp53'][0]
    Tq5, pq5 = fit_onto_5iv7(q, q53, s5ch['gp53'][0], skip=('gp19', None))
    Tr5, _ = fit_onto_5iv7(r, r7, s5id[pq5[q7.chain_id]], skip=('gp19', None))
    Xr = Tq5.inverse() * Tr5
    s5.delete()
    PQ = [Tq * P for P in Q0]
    PR = [Tq * P * Xr for P in Q0]
    q.positions = Places([TP * p for p in PQ]); r.positions = Places([TP * p for p in PR])
    q.display = False; r.display = False

    # post-trigger chains (tail coordinates); gp7 is split between 9X3Q and 9X3R and merged here
    post = []
    for j in range(6):
        merged = {}
        for m, P in ((q, PQ[j]), (r, PR[j])):
            for ch in m.chains:
                nm = post_name(ch)
                if nm is None or nm == 'gp19':
                    continue
                a = ch.existing_residues.atoms
                amap = {(int(rn), str(an)): x for rn, an, x in zip(a.residues.numbers, a.names, P.transform_points(a.coords))}
                if nm == 'gp7':
                    merged.update(amap)
                else:
                    post.append({'prot': nm, 'inst': j, 'atoms': amap})
        post.append({'prot': 'gp7', 'inst': j, 'atoms': merged})
    for p_ in post:
        p_['ca'] = {k[0]: v for k, v in p_['atoms'].items() if k[1] == 'CA'}

    # pre to post chain mapping per protein: least mean-square CA displacement
    tok = W.token
    pre = defaultdict(list)
    for ch in tail.chains:
        if tok(ch) in BP:
            pre[tok(ch)].append(ch)
    mapping = {}
    for prot in BP:
        A_, B_ = pre[prot], [p_ for p_ in post if p_['prot'] == prot]
        C = np.zeros((len(A_), len(B_)))
        for i, a in enumerate(A_):
            da = ca(a)
            for k, b in enumerate(B_):
                X, Y, _ = paired(da, b['ca'])
                C[i, k] = np.mean(((X - Y) ** 2).sum(1))
        for i, k in zip(*linear_sum_assignment(C)):
            mapping[A_[i].chain_id] = B_[k]

    # per-chain transforms and atom targets
    chain_ids, prots, bounds, X0s, L1s, Ts, follow = [], [], [], [], [], [], []
    n0 = 0
    for prot in BP:
        for ch in pre[prot]:
            b = mapping[ch.chain_id]
            a = ch.existing_residues.atoms
            x0 = a.coords
            Pp, Qp, keys = paired(ca(ch), b['ca'])
            T = kp(Pp, Qp)
            resn, names = a.residues.numbers.tolist(), a.names.tolist()
            ica = {rn: i for i, (rn, nm) in enumerate(zip(resn, names)) if nm == 'CA'}
            keys = np.array(keys)
            x1 = np.empty_like(x0)
            for i, (rn, nm) in enumerate(zip(resn, names)):
                v = b['atoms'].get((rn, nm))
                if v is not None:
                    x1[i] = v
                else:
                    rr = rn if rn in b['ca'] and rn in ica else int(keys[np.argmin(np.abs(keys - rn))])
                    x1[i] = T.transform_points(x0[i][None])[0] + (b['ca'][rr] - T.transform_points(x0[ica[rr]][None])[0])
            chain_ids.append(ch.chain_id); prots.append(prot); bounds.append((n0, n0 + len(x0))); n0 += len(x0)
            X0s.append(x0); L1s.append(T.inverse().transform_points(x1)); Ts.append(T); follow.append(np.full(len(x0), -1))
    nmap = len(chain_ids)

    # gp12 short fibres: trimers by contact, each carried rigidly with the gp11 trimer of the wedge it touches most
    byid = {ch.chain_id: ch for ch in tail.chains}
    gp12 = [ch for ch in tail.chains if tok(ch) == 'gp12']
    g12x = {ch.chain_id: ch.existing_residues.atoms.coords for ch in gp12}
    cnt = {}
    for a in gp12:
        ta = cKDTree(g12x[a.chain_id])
        for b in gp12:
            if a.chain_id < b.chain_id:
                n = int((ta.query(g12x[b.chain_id], distance_upper_bound=4)[0] < 4).sum())
                if n:
                    cnt[(a.chain_id, b.chain_id)] = n
    groups, seen = [], set()
    for ch in gp12:
        if ch.chain_id in seen:
            continue
        part = sorted([(n, b if a == ch.chain_id else a) for (a, b), n in cnt.items() if ch.chain_id in (a, b)], reverse=True)[:2]
        grp = sorted({ch.chain_id} | {x for _, x in part})
        seen |= set(grp); groups.append(grp)
    pool = pre['gp10'] + pre['gp11']
    pool_tree = cKDTree(np.concatenate([ch.existing_residues.atoms.coords for ch in pool]))
    pool_lab = np.concatenate([[ch.chain_id] * len(ch.existing_residues.atoms) for ch in pool])
    stf_ids = []
    for grp in groups:
        xyz = np.concatenate([g12x[c] for c in grp])
        d, i = pool_tree.query(xyz, distance_upper_bound=4.5)
        by_w = defaultdict(int)
        for c in pool_lab[i[d < 4.5]]:
            by_w[mapping[c]['inst']] += 1
        jw = max(by_w, key=by_w.get)
        anchor = [x.chain_id for x in pre['gp11'] if mapping[x.chain_id]['inst'] == jw]
        Pp, Qp = [], []
        for c in anchor:
            X, Y, _ = paired(ca(byid[c]), mapping[c]['ca']); Pp.append(X); Qp.append(Y)
        Ta = kp(np.concatenate(Pp), np.concatenate(Qp), cutoff=3.0)
        for c in grp:
            x0 = g12x[c]
            chain_ids.append(c); prots.append('gp12'); bounds.append((n0, n0 + len(x0))); n0 += len(x0)
            X0s.append(x0); L1s.append(x0.copy()); Ts.append(Ta); follow.append(np.full(len(x0), -1))
            stf_ids.append(c)

    # other baseplate chains (unassigned peptides) and non-polymer residues: follow the nearest mapped CA
    done = set(D['moving_chains']) | set(D['sheath_chains']) | set(chain_ids)
    names_all = np.concatenate([byid[c].existing_residues.atoms.names for c in chain_ids[:nmap]])
    ca_idx = np.where(names_all == 'CA')[0]
    ca_tree = cKDTree(np.concatenate(X0s[:nmap])[ca_idx])
    pep = []
    for ch in tail.chains:
        if ch.chain_id in done:
            continue
        x0 = ch.existing_residues.atoms.coords
        chain_ids.append(ch.chain_id); prots.append('pep'); bounds.append((n0, n0 + len(x0))); n0 += len(x0)
        X0s.append(x0); L1s.append(x0.copy()); Ts.append(Place()); follow.append(ca_idx[ca_tree.query(x0)[1]])
        pep.append(ch.chain_id)
    nonf = nmap + len(stf_ids)
    names_nf = np.concatenate([byid[c].existing_residues.atoms.names for c in chain_ids[:nonf]])
    ca_nf = np.where(names_nf == 'CA')[0]
    tree_nf = cKDTree(np.concatenate(X0s[:nonf])[ca_nf])
    inchain = {(r_.chain_id, r_.number) for ch in tail.chains for r_ in ch.existing_residues}
    ligs = []
    for r_ in tail.residues:
        if (r_.chain_id, r_.number) not in inchain and r_.chain_id in chain_ids[:nonf]:
            x0 = r_.atoms.coords
            bounds.append((n0, n0 + len(x0))); n0 += len(x0); prots.append('lig')
            X0s.append(x0); L1s.append(x0.copy()); Ts.append(Place()); follow.append(ca_nf[tree_nf.query(x0)[1]])
            ligs.append(r_)
    from chimerax.atomic import concatenate
    atoms = concatenate([byid[c].existing_residues.atoms for c in chain_ids] + [r_.atoms for r_ in ligs])
    stf_atoms = concatenate([byid[c].existing_residues.atoms for c in stf_ids])
    pep_atoms = concatenate([byid[c].existing_residues.atoms for c in pep]) if pep else None
    return Trigger(atoms, bounds, prots, Ts, np.concatenate(X0s), np.concatenate(L1s), np.concatenate(follow), stf_atoms, pep_atoms)


class Trigger:
    def __init__(self, atoms, bounds, prots, Ts, X0, L1, follow, stf_atoms, pep_atoms, coupled=('gp25',)):
        self.atoms, self.X0, self.L1, self.follow = atoms, X0, L1, follow
        self.bounds = [slice(int(a), int(b)) for a, b in bounds]
        self.Ts, self.prots = Ts, prots
        self.stf_atoms, self.pep_atoms = stf_atoms, pep_atoms
        self.coupled = [p in coupled for p in prots]

    def set(self, s, s_ring1=0.0):
        xyz = np.empty_like(self.X0)
        for T, sl, cpl in zip(self.Ts, self.bounds, self.coupled):
            sc = s_ring1 if cpl else s
            xyz[sl] = W.screw_power(T, sc).transform_points((1 - sc) * self.X0[sl] + sc * self.L1[sl])
        f = self.follow >= 0
        j = self.follow[f]
        xyz[f] = self.X0[f] + (xyz[j] - self.X0[j])
        self.atoms.coords = xyz
        if self.pep_atoms is not None:
            self.pep_atoms.displays = s < 0.5


class Timeline:
    """T in [0, 1]: trigger (eased); T in [1, 2]: contraction wave."""

    def __init__(self, trigger, wave):
        self.trigger, self.wave, self.T = trigger, wave, 0.0

    def set(self, T, wavelength=0.5):
        t = float(np.clip(T - 1.0, 0.0, 1.0))
        self.trigger.set(float(W.smoothstep(T)), float(self.wave.ring_progress(t, wavelength)[0]))
        self.wave.set(t, wavelength)
        self.T = T


def register_commands(session):
    from chimerax.core.commands import register, CmdDesc, FloatArg, IntArg

    def tl(session):
        t = getattr(session, 'phage_t4', {}).get('timeline')
        if t is None:
            from chimerax.core.errors import UserError
            raise UserError('Run t4_trigger.py first.')
        return t

    def t4motion(session, T):
        tl(session).set(float(np.clip(T, 0, 2)))

    def t4motion_play(session, frames=480):
        t = tl(session)
        W.play(session, t.set, t.T, 2.0 if t.T < 2.0 else 0.0, frames)

    register('t4motion', CmdDesc(required=[('T', FloatArg)], synopsis='T4 trigger (0-1) then contraction (1-2)'), t4motion, logger=session.logger)
    register('t4motion play', CmdDesc(optional=[('frames', IntArg)], synopsis='play the T4 trigger and contraction'), t4motion_play, logger=session.logger)


def main(session):
    import time
    t0 = time.time()
    if not hasattr(session, 'phage_t4'):
        session.phage_t4 = {}
    wave = session.phage_t4.get('wave')
    if wave is None or wave.tail.deleted:
        wave = W.build(session)
        session.phage_t4['wave'] = wave
        W.register_commands(session)
    wave.set(0.0)
    trig = build(session, wave)
    session.phage_t4['timeline'] = Timeline(trig, wave)
    register_commands(session)
    session.logger.info('T4 trigger ready in %.0f s: %d baseplate atoms. Try: t4motion play 480'
                        % (time.time() - t0, len(trig.atoms)))


if __name__ != 't4_trigger':
    main(session)  # noqa: F821
