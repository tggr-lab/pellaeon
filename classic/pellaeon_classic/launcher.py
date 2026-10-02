"""Small native launcher window (Tk, no dependencies) for Pellaeon Classic."""
from __future__ import annotations

import os
import queue
import threading
import webbrowser

import tkinter as tk
from tkinter import filedialog, ttk


def run_launcher(port: int = 8765, chimera_port: int = 0, open_browser: bool = True):
    from pellaeon_classic.app import start_server, find_chimera

    root = tk.Tk()
    root.title("Pellaeon Classic")
    root.minsize(460, 360)
    try:
        root.tk.call("tk", "scaling", 1.25)
    except Exception:
        pass
    events: "queue.Queue" = queue.Queue()

    srv, panel, url = start_server(port, chimera_port)
    panel.listeners.append(events.put)

    # ---- layout
    frm = ttk.Frame(root, padding=14)
    frm.pack(fill="both", expand=True)
    ttk.Label(frm, text="Pellaeon Classic", font=("TkDefaultFont", 15, "bold")).grid(row=0, column=0, columnspan=3, sticky="w")
    ttk.Label(frm, text="Talk to UCSF Chimera in plain English", foreground="#666").grid(row=1, column=0, columnspan=3, sticky="w", pady=(0, 10))

    ttk.Label(frm, text="Panel:").grid(row=2, column=0, sticky="w")
    link = ttk.Label(frm, text=url, foreground="#2f6fdd", cursor="hand2")
    link.grid(row=2, column=1, sticky="w")
    link.bind("<Button-1>", lambda e: webbrowser.open(url))
    ttk.Button(frm, text="Open panel", command=lambda: webbrowser.open(url)).grid(row=2, column=2, sticky="e")

    ttk.Label(frm, text="Chimera:").grid(row=3, column=0, sticky="w", pady=(8, 0))
    chim_var = tk.StringVar(value="not connected")
    ttk.Label(frm, textvariable=chim_var, wraplength=300).grid(row=3, column=1, sticky="w", pady=(8, 0))
    btns = ttk.Frame(frm)
    btns.grid(row=3, column=2, sticky="e", pady=(8, 0))

    path_var = tk.StringVar(value=panel.settings.chimera_path or find_chimera())
    port_var = tk.StringVar(value=str(panel.settings.chimera_port or ""))

    def launch():
        chim_var.set("starting Chimera…")
        panel._act_chimera_launch({}, {"chimera_path": path_var.get().strip()})

    def test():
        panel._act_chimera_test({"port": port_var.get().strip()}, None)

    ttk.Button(btns, text="Launch Chimera", command=launch).pack(side="left")
    ttk.Button(btns, text="Test", command=test).pack(side="left", padx=(6, 0))

    ttk.Label(frm, text="Chimera program:").grid(row=4, column=0, sticky="w", pady=(8, 0))
    pe = ttk.Entry(frm, textvariable=path_var)
    pe.grid(row=4, column=1, sticky="ew", pady=(8, 0))

    def browse():
        f = filedialog.askopenfilename(title="Locate the chimera executable")
        if f:
            path_var.set(f)
            panel.settings.chimera_path = f
            panel.settings.save()
    ttk.Button(frm, text="Browse…", command=browse).grid(row=4, column=2, sticky="e", pady=(8, 0))

    ttk.Label(frm, text="REST port:").grid(row=5, column=0, sticky="w", pady=(4, 0))
    ttk.Entry(frm, textvariable=port_var, width=10).grid(row=5, column=1, sticky="w", pady=(4, 0))
    ttk.Label(frm, text="(Tools ▸ Utilities ▸ RESTServer in Chimera, or use Launch)", foreground="#666").grid(row=6, column=1, columnspan=2, sticky="w")

    ttk.Label(frm, text="Activity:").grid(row=7, column=0, sticky="nw", pady=(10, 0))
    log = tk.Text(frm, height=8, width=50, wrap="word", state="disabled", relief="flat", background="#f4f4f6")
    log.grid(row=7, column=1, columnspan=2, sticky="nsew", pady=(10, 0))
    frm.columnconfigure(1, weight=1)
    frm.rowconfigure(7, weight=1)

    status = tk.StringVar(value="Ready. The panel opens in your browser; keep this window open while you work.")
    ttk.Label(frm, textvariable=status, foreground="#666", wraplength=430).grid(row=8, column=0, columnspan=3, sticky="w", pady=(8, 0))
    ttk.Button(frm, text="Quit", command=root.destroy).grid(row=9, column=2, sticky="e", pady=(8, 0))

    def add_log(text):
        log.configure(state="normal")
        log.insert("end", text.rstrip() + "\n")
        log.see("end")
        log.configure(state="disabled")

    def poll():
        try:
            while True:
                ev = events.get_nowait()
                t = ev.get("type")
                if t == "chimera_status":
                    chim_var.set(("connected, port %s" % ev["port"]) if ev.get("ok") else ev.get("text", ""))
                    if ev.get("port"):
                        port_var.set(str(ev["port"]))
                    add_log(ev.get("text", ""))
                elif t == "log":
                    add_log(ev["text"])
                elif t == "toast":
                    add_log(ev.get("text", ""))
                elif t == "user_message":
                    add_log("You: " + ev.get("text", ""))
                elif t == "tool_result" and ev.get("name") == "run_commands":
                    for r in ev.get("results", []):
                        add_log(("  ok   " if r.get("ok") else "  FAIL ") + r.get("command", ""))
                elif t == "busy":
                    status.set("Working…" if ev.get("busy") else "Ready.")
        except queue.Empty:
            pass
        root.after(200, poll)

    root.after(200, poll)
    if open_browser:
        root.after(600, lambda: webbrowser.open(url))
    if panel.settings.chimera_port:
        root.after(800, test)
    root.mainloop()
    srv.shutdown()
