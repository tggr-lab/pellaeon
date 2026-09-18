# Releasing Pellaeon

The bundle is pure Python, so one `py3-none-any` wheel serves Windows, macOS and Linux.
Building it needs a real ChimeraX (the bundle builder lives inside ChimeraX), so releases are built by hand:

1. Bump `__version__` in `src/__init__.py` and commit.
2. Build the wheel from a terminal:

   ```
   chimerax --nogui --exit --cmd "devel build \"$(pwd)\""
   ```
   On Windows use `ChimeraX-console.exe` and the full path. The wheel lands in `dist/`.
3. Sanity-check it in a fresh ChimeraX: `toolshed install dist/ChimeraX_Pellaeon-<version>-py3-none-any.whl`,
   open Tools > General > Pellaeon, run the checklist in README ("Try it").
4. Tag and create a GitHub release, attaching **both** `dist/*.whl` and `install_pellaeon.py`.
   The one-line installer always fetches the newest release's wheel.
5. (Optional) Submit the wheel to the ChimeraX Toolshed at https://cxtoolshed.rbvi.ucsf.edu so users can
   `toolshed install Pellaeon`.

Developer loop: `devel install .` (installs into your ChimeraX), `devel clean .` to tidy.
Unit tests (no ChimeraX needed): `python -m pytest`. In-ChimeraX smoke test:
`chimerax --nogui --exit --script tests_chimerax/smoke.py`.
