# Example: phage T4

Scripts for the [phage T4 page](https://tggr-lab.github.io/pellaeon/phage-t4.html). MIT license, like the rest of Pellaeon. They run in UCSF ChimeraX (1.9 or newer) with or without Pellaeon; through Pellaeon, a request such as "run the script t4_parts.py" shows the command on a card and waits for your OK.

| File | What it does | Needs |
|---|---|---|
| `t4_parts.py` | Names the parts of the virion (`head`, `soc`, `neck`, `sheath`, `tube`, `inner`, `outer`, `socket`, `stf`, `hub`, `other`, plus `portal`, `fibritin`, `hoc`, `spike`, `gp27`, `tip`, `capsid9mkb`) so commands can use them: `color sheath #89B4FA`, `view hub`. | 9MKB, optionally 7VS5 |
| `t4_wave.py` | Fits the contracted sheath (9X3P) on the open tail and adds the command `t4wave`: `t4wave 0.5` sets a state, `t4wave play 240` plays the contraction from the baseplate to the neck. | 9MKB, optionally 7VS5 |
| `t4_trigger.py` | Adds the baseplate trigger (hexagon to star, 9X3Q and 9X3R placed through 5IV7) before the wave, and the command `t4motion`: 0 to 1 trigger, 1 to 2 contraction. `t4motion play 480`. | 9MKB, `t4_wave.py` in the same folder |

Both end states are deposited coordinates for every atom that has a partner in the other structure. In between, the motion is modeled. The gp12 short fibers keep their folded shape (no extended structure exists).

Measured on one PC (headless ChimeraX 1.12, files already downloaded): `t4_wave.py` 2 s, `t4_trigger.py` 7 s, peak memory 3.5 GB with 9MKB and the instanced 7VS5 head open.

The contraction-wave approach follows T. Goddard's 2022 ChimeraX phage T4 animation; this is separate code with T4 values.
