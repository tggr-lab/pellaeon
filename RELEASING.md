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

## Website (GitHub Pages)

The site lives in `docs/` and is generated from Markdown: `docs/install.md`, `docs/tutorial.md`, `classic/README.md`
plus the hand-written landing page template in `tools/build_site.py`. After editing any of them run
`python tools/build_site.py` (needs `pip install markdown` once) and commit the regenerated HTML.
Enable it once on GitHub: repository **Settings ▸ Pages ▸ Source: Deploy from a branch ▸ main / docs**.
The page is then served at `https://<user>.github.io/<repo>/`. Preview locally with `python -m http.server -d docs 8000`.

## Classic screenshot

`docs/img/classic.png` is composed by `python tools/classic_shot.py` from three real captures in `~/pellaeon_shots/`: a headless Chromium screenshot of the classic panel page, and X11 window grabs of the launcher and of Chimera made with `python tools/xgrab_window.py "<window title>" out.png` (works under GNOME Wayland, where ordinary screenshots of XWayland windows are blocked). Start Chimera with `--start RESTServer`, run `python -m pellaeon_classic --chimera-port N --no-browser` from `classic/`, send a request, then capture.
