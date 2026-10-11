#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Wavethings
"""
Wavethings Kit Creator - desktop app (macOS and Windows).

The same interface as kit_creator_gui.py, but in its own window instead of a browser tab: it
starts the local server in the background and shows it with the system's web view (pywebview).
Folder and file pickers are the operating system's native dialogs. Each launch starts from
scratch (no folders from the last session); choose a preset or add folders.

Run from source:   pip install -r requirements-desktop.txt && python3 desktop/app.py
Built apps:        see .github/workflows/build-apps.yml (attached to every release).
"""
import os, sys, threading
from pathlib import Path

if getattr(sys, "frozen", False):      # a windowed app has no console: keep stray prints harmless
    for _name in ("stdout", "stderr"):
        if getattr(sys, _name) is None:
            setattr(sys, _name, open(os.devnull, "w"))
else:
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import mpc_kit_creator as kc          # noqa: E402
import kit_creator_gui as gui         # noqa: E402

TITLE = "Wavethings Kit Creator"
XPM_TYPES = ("XPM files (*.xpm)", "All files (*.*)")


def make_picker(webview, get_window):
    """Returns the function the interface uses to pick folders/files (see kit_creator_gui.pick):
    (kind, lang) -> (path | list of paths | None, error). `kind` is "folders" (several folders),
    "folder", "out" (a folder) or "xpm" (a file). On Windows the system's folder dialog allows a
    single folder at a time; on macOS several can be selected."""
    dialogs = getattr(webview, "FileDialog", None)      # pywebview 5+; older versions use constants
    folder = dialogs.FOLDER if dialogs else webview.FOLDER_DIALOG
    open_file = dialogs.OPEN if dialogs else webview.OPEN_DIALOG

    def clean(p):
        p = str(p)
        return p.rstrip("/\\") or p

    def picker(kind, lang="en"):
        window = get_window()
        if kind == "folders":
            res = window.create_file_dialog(folder, allow_multiple=True)
            return [clean(p) for p in (res or [])], None
        if kind == "xpm":
            res = window.create_file_dialog(open_file, allow_multiple=False, file_types=XPM_TYPES)
        else:
            res = window.create_file_dialog(folder, allow_multiple=False)
        return (clean(res[0]) if res else None), None

    return picker


def selftest():
    """Used by the build to check the packaged app: imports everything, serves the page."""
    import json, urllib.request
    import webview                     # noqa: F401 - must be bundled
    gui.fresh_session()
    server, url = gui.start_server()
    threading.Thread(target=server.serve_forever, daemon=True).start()
    page = urllib.request.urlopen(url, timeout=10).read().decode("utf-8")
    req = urllib.request.Request(url + "api/state", headers={"X-Token": gui.TOKEN})
    state = json.loads(urllib.request.urlopen(req, timeout=10).read())
    ok = "Wavethings" in page and len(state["palette"]) >= 25 and not (state["state"] or {}).get("folders")
    server.shutdown()
    return 0 if ok else 1


def main():
    if "--selftest" in sys.argv:
        try:
            sys.exit(selftest())
        except Exception:
            sys.exit(2)
    import webview
    gui.fresh_session()                # always start from scratch
    server, url = gui.start_server()
    threading.Thread(target=server.serve_forever, daemon=True).start()
    window = webview.create_window(f"{TITLE} {kc.__version__}", url, width=1280, height=900,
                                   min_size=(980, 640))
    gui.PICKER = make_picker(webview, lambda: window)
    webview.start()                    # returns when the window is closed
    server.shutdown()


if __name__ == "__main__":
    main()
