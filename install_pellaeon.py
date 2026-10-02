# Pellaeon one-line installer. Run it from inside ChimeraX:
#
#     open https://github.com/tggr-lab/pellaeon/releases/latest/download/install_pellaeon.py
#
# It downloads the latest Pellaeon wheel from the same GitHub release, installs
# it with ChimeraX's own "toolshed install", and opens the panel.
# ChimeraX runs this file with the variable `session` defined.

RELEASE_API = "https://api.github.com/repos/tggr-lab/pellaeon/releases/latest"


def _install(session):
    import json
    import os
    import tempfile
    import urllib.request
    from chimerax.core.commands import run

    log = session.logger
    log.info("Pellaeon installer: looking up the latest release…")
    req = urllib.request.Request(RELEASE_API, headers={"User-Agent": "Pellaeon-installer", "Accept": "application/vnd.github+json"})
    with urllib.request.urlopen(req, timeout=30) as r:
        release = json.loads(r.read().decode("utf-8"))
    wheels = [a for a in release.get("assets", []) if a.get("name", "").endswith(".whl")]
    if not wheels:
        raise RuntimeError("No wheel found in release %s" % release.get("tag_name"))
    asset = wheels[0]
    url = asset["browser_download_url"]
    log.info("Pellaeon installer: downloading %s (%.1f MB)…" % (asset["name"], asset.get("size", 0) / 1e6))
    tmpdir = tempfile.mkdtemp(prefix="pellaeon-")
    path = os.path.join(tmpdir, asset["name"])
    req = urllib.request.Request(url, headers={"User-Agent": "Pellaeon-installer"})
    with urllib.request.urlopen(req, timeout=120) as r, open(path, "wb") as f:
        f.write(r.read())
    log.info("Pellaeon installer: installing…")
    run(session, 'toolshed install "%s"' % path.replace('"', '\\"'))
    log.info("Pellaeon %s installed. Opening the panel (also under Tools > General > Pellaeon)." % release.get("tag_name", ""))
    try:
        run(session, "ui tool show Pellaeon")
    except Exception:
        log.info("Restart ChimeraX, then open Tools > General > Pellaeon.")


try:
    _install(session)  # noqa: F821  (ChimeraX provides `session`)
except Exception as e:  # noqa: BLE001
    session.logger.error("Pellaeon installer failed: %s\nYou can install manually: download the .whl from "  # noqa: F821
                         "https://github.com/tggr-lab/pellaeon/releases and run  toolshed install /path/to/file.whl" % e)
