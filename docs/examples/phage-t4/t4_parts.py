# Named parts of the phage T4 virion for UCSF ChimeraX.
# SPDX-License-Identifier: MIT. Copyright (c) 2026 Yam Amir, TGGR Lab. Part of the Pellaeon examples.
#
# ChimeraX selects chains by ID, and 9MKB has 541 chains, so this script names the parts by protein:
#   head  soc  neck  sheath  tube  inner  outer  socket  stf  hub  other   (the film's colour groups)
#   portal  fibritin  hoc  spike  gp27  tip  capsid9mkb                  (finer names)
# capsid9mkb is the 30 gp23 and 10 Soc chains of 9MKB that the 7VS5 head also contains.
# Run:  runscript t4_parts.py        then use the names in any command:  color sheath #89B4FA ; view hub
from chimerax.atomic import AtomicStructure, concatenate
from chimerax.core.commands import register_selector

GROUP = {
    'gp23': 'head', 'Major capsid protein': 'head', 'Capsid vertex protein': 'head', 'Hoc': 'head',
    'Soc': 'soc', 'Small outer capsid protein': 'soc',
    'gp20': 'neck', 'gp13': 'neck', 'gp14': 'neck', 'gp15': 'neck', 'gpwac': 'neck',
    'gp18': 'sheath', 'gp19': 'tube', 'gp3': 'tube',
    'gp6': 'inner', 'gp25': 'inner', 'gp53': 'inner',
    'gp7': 'outer', 'gp8': 'outer', 'gp10': 'outer', 'gp11': 'outer',
    'gp9': 'socket', 'gp12': 'stf',
    'gp27': 'hub', 'gp5': 'hub', 'gp5.4': 'hub', 'gp48': 'hub', 'gp54': 'hub', 'gp29': 'hub',
}
FINE = {'portal': ('gp20',), 'fibritin': ('gpwac',), 'hoc': ('Hoc',), 'spike': ('gp5', 'gp5.4'), 'gp27': ('gp27',),
        'tip': ('gp5.4',)}


def token(ch):
    d = ch.description or ''
    return d if d in GROUP else d.split(',')[0]


def main(session):
    models = [m for m in session.models.list() if isinstance(m, AtomicStructure)]
    has_head = any(c.description.startswith('Major capsid') for m in models for c in m.chains)
    names = {}
    for m in models:
        for ch in m.chains:
            t = token(ch)
            g = GROUP.get(t)
            if g is None and ch.description.startswith('unassigned'):
                g = 'other'
            if g is None:
                continue
            keys = [g] + [n for n, toks in FINE.items() if t in toks]
            if has_head and t in ('gp23', 'Soc') and not ch.description.startswith(('Major', 'Small')):
                keys = ['capsid9mkb']                        # the 9MKB copies of head chains
            for k in keys:
                names.setdefault(k, []).append(ch)
    rows = []
    for k, chains in names.items():
        register_selector(k, selector(chains), session.logger, desc='T4 part %s' % k)
        rows.append('%s (%d chains)' % (k, len(chains)))
    session.logger.info('Named parts: ' + ', '.join(rows))


def selector(chains):
    def select(session, models, results):
        live = [ch for ch in chains if not ch.deleted and ch.structure in models]
        if live:
            results.add_atoms(concatenate([ch.existing_residues.atoms for ch in live]))
    return select


if __name__ != 't4_parts':
    main(session)  # noqa: F821
