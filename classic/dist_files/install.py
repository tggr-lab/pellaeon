#!/usr/bin/env python3
"""Put a 'Pellaeon Classic' shortcut on the desktop and in the Start menu / applications list.   python install.py"""
import os
import subprocess
import sys

here = os.path.dirname(os.path.abspath(__file__))
if sys.platform == "win32":
    target = os.path.join(here, "run.pyw")
    pyw = os.path.join(os.path.dirname(sys.executable), "pythonw.exe")
    if not os.path.exists(pyw):
        pyw = sys.executable
    for folder in (os.path.join(os.path.expanduser("~"), "Desktop"),
                   os.path.join(os.environ.get("APPDATA", ""), "Microsoft", "Windows", "Start Menu", "Programs")):
        if not os.path.isdir(folder):
            continue
        lnk = os.path.join(folder, "Pellaeon Classic.lnk")
        ps = ("$s=(New-Object -ComObject WScript.Shell).CreateShortcut('%s');$s.TargetPath='%s';$s.Arguments='\"%s\"';"
              "$s.WorkingDirectory='%s';$s.Description='Talk to UCSF Chimera in plain English';$s.Save()") % (lnk, pyw, target, here)
        subprocess.run(["powershell", "-NoProfile", "-Command", ps], check=False)
        print("created", lnk)
elif sys.platform == "darwin":
    app = os.path.join(os.path.expanduser("~"), "Desktop", "Pellaeon Classic.command")
    with open(app, "w") as f:
        f.write('#!/bin/bash\ncd "%s"\npython3 run.py\n' % here)
    os.chmod(app, 0o755)
    print("created", app, "(double-click it; allow it in System Settings > Privacy if macOS complains)")
else:
    apps = os.path.join(os.path.expanduser("~"), ".local", "share", "applications")
    os.makedirs(apps, exist_ok=True)
    desk = os.path.join(apps, "pellaeon-classic.desktop")
    with open(desk, "w") as f:
        f.write("[Desktop Entry]\nType=Application\nName=Pellaeon Classic\nComment=Talk to UCSF Chimera in plain English\n"
                "Exec=python3 \"%s\"\nPath=%s\nTerminal=false\nCategories=Science;\n" % (os.path.join(here, "run.py"), here))
    print("created", desk, "(it appears in your applications menu)")
