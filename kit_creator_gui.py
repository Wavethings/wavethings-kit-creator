#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Wavethings
"""
Wavethings Kit Creator - local (browser) interface for mpc_kit_creator.py
Usage:  python3 kit_creator_gui.py     (keep mpc_kit_creator.py in the same folder)
Quit with Ctrl+C in the Terminal. The interface language (English / Español) can be
switched inside the app; it starts in your browser's language.
"""
import json, os, secrets, shutil, subprocess, sys, threading, time, webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import mpc_kit_creator as kc

HERE = Path(__file__).resolve().parent


def default_data_dir():
    """Per-user folder for settings and presets (never inside the project folder)."""
    env = os.environ.get("KITCREATOR_DATA")
    if env:
        return Path(env)
    home = Path.home()
    if sys.platform == "darwin":
        base = home / "Library" / "Application Support"
    elif sys.platform.startswith("win"):
        base = Path(os.environ.get("APPDATA") or home / "AppData" / "Roaming")
    else:
        base = Path(os.environ.get("XDG_CONFIG_HOME") or home / ".config")
    return base / "Wavethings Kit Creator"


DATA = default_data_dir()
DATA.mkdir(parents=True, exist_ok=True)
STATE_FILE = DATA / "kit_creator_state.json"
PAL_FILE = DATA / "mpc_palette.json"
PRESETS_FILE = DATA / "mpc_presets.json"
GROUPS_FILE = DATA / "keyword_groups.txt"
GROUP_COLORS_FILE = DATA / "group_colors.json"
for _name in ("kit_creator_state.json", "mpc_palette.json"):   # v1.0/1.1 kept them next to the script
    _old = HERE / _name
    if DATA != HERE and _old.is_file() and not (DATA / _name).exists():
        try:
            shutil.copy2(_old, DATA / _name)
        except OSError:
            pass
AUTOQUIT = bool(os.environ.get("KITCREATOR_AUTOQUIT")) or "--auto-quit" in sys.argv
SEEN = {"last": time.time(), "bye": 0.0}
TOKEN = secrets.token_hex(16)

# MPC pad colors: (R<<16)|(G<<8)|B with 7-bit channels (0-127), as the classic .xpm format stores
# them. Names are keys translated in the page. The 25 colors below are the MPC's own pad colors
# as saved by the MPC 3.x software (8-bit values, halved by kc.mpc3_color_to_classic), in the
# order the MPC offers them; "petrol" was seen in a classic MPC 2.x kit.
_MPC3_COLORS = [
    ("red", 0xFF0000), ("orange", 0xFF6D17), ("red_orange", 0xFF4400), ("amber", 0xFF8800),
    ("mustard", 0xECEC24), ("gold", 0xFFD500), ("chartreuse", 0xE6FF00), ("lime", 0xA2FF00),
    ("light_green", 0x55FF00), ("green", 0x11FF00), ("spring", 0x00FF33), ("mint", 0x00FF80),
    ("turquoise", 0x00FFC4), ("cyan", 0x00F7FF), ("sky", 0x00AAFF), ("azure", 0x0066FF),
    ("grey", 0xA2A9AD), ("blue", 0x0022FF), ("indigo", 0x5200FF), ("violet", 0x6F00FF),
    ("purple", 0xB200FF), ("magenta", 0xFF00FF), ("pink", 0xFF00BB), ("rose", 0xFF0077),
    ("jade", 0x29B574),
]
DEFAULT_PALETTE = [(n, kc.mpc3_color_to_classic(v), True) for n, v in _MPC3_COLORS] + [
    ("petrol", 0x114F68, True),
]
JOB = {"log": [], "done": True, "error": None}

PROMPTS = {  # native macOS dialog titles
    "en": {"folders": "Choose one or several folders (Cmd-click for several)",
           "folder": "Choose a samples folder", "out": "Choose the output folder",
           "xpm": "Choose an .xpm file"},
    "es": {"folders": "Elige una o varias carpetas (Cmd-clic para varias)",
           "folder": "Elige una carpeta de samples", "out": "Elige la carpeta de salida",
           "xpm": "Elige un archivo .xpm"},
}
SRV = {  # Terminal messages
    "en": {"up": "Wavethings Kit Creator at {url}  (Ctrl+C to quit)", "bye": "\nBye", "closing": "Closing Wavethings Kit Creator",
           "ports": "No free ports"},
    "es": {"up": "Wavethings Kit Creator en {url}  (Ctrl+C para salir)", "bye": "\nAdiós", "closing": "Cerrando Wavethings Kit Creator",
           "ports": "No hay puertos libres"},
}


def srv(key, **kw):
    return SRV.get(kc.LANG, SRV["en"])[key].format(**kw)


def load_presets():
    try:
        d = json.loads(PRESETS_FILE.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return d if isinstance(d, dict) else {}


def store_presets(d):
    tmp = PRESETS_FILE.with_suffix(".tmp")      # write then swap: never leaves a half-written file
    tmp.write_text(json.dumps(d, indent=1, ensure_ascii=False), encoding="utf-8")
    os.replace(tmp, PRESETS_FILE)


THEMES = ("dark", "light", "mpc")


def groups_header():
    """The comment lines at the top of the default groups file (they document the syntax)."""
    out = []
    for line in kc.DEFAULT_KEYWORD_GROUPS_TEXT.splitlines():
        if line.startswith("#") or not line.strip():
            out.append(line)
        else:
            break
    return "\n".join(out).rstrip() + "\n\n"


def save_groups(items):
    """Writes the keyword-groups file from the in-app editor. `items` = [{"name", "keywords"}].
    Returns (groups dict, error key or None). Names must be unique and cannot contain ':'."""
    groups = {}
    for it in items:
        name = str(it.get("name", "")).strip()
        kws = it.get("keywords", [])
        if isinstance(kws, str):
            kws = kws.split(",")
        kws = [str(k).strip() for k in kws if str(k).strip()]
        if not name and not kws:
            continue                      # an empty row is just dropped
        if not name or ":" in name or name.startswith("#"):
            return None, "bad_name"
        if name.lower() in {n.lower() for n in groups}:
            return None, "dup_name"
        if not kws:
            return None, "no_keywords"
        groups[name] = kws
    if not groups:
        return None, "no_groups"
    text = groups_header() + "\n".join(f"{n}: {', '.join(k)}" for n, k in groups.items()) + "\n"
    tmp = GROUPS_FILE.with_suffix(".tmp")
    tmp.write_text(text, encoding="utf-8")
    os.replace(tmp, GROUPS_FILE)
    return groups, None


def load_groups():
    """Reads the person's keyword-groups file (an ordered {name: [keywords]} dict), creating
    it with the built-in starting groups the first time it's needed. Re-read fresh on every
    call (cheap, small file) so an edit just needs a page reload to take effect."""
    if not GROUPS_FILE.is_file():
        GROUPS_FILE.write_text(kc.DEFAULT_KEYWORD_GROUPS_TEXT, encoding="utf-8")
    else:       # an untouched file from versions 1.7-1.9.1 is upgraded to the current defaults
        try:
            if kc.load_keyword_groups(GROUPS_FILE.read_text(encoding="utf-8")) in (
                    kc.LEGACY_DEFAULT_GROUPS, kc.LEGACY_DEFAULT_GROUPS_192):
                shutil.copy2(GROUPS_FILE, GROUPS_FILE.with_name(GROUPS_FILE.name + ".bak"))
                GROUPS_FILE.write_text(kc.DEFAULT_KEYWORD_GROUPS_TEXT, encoding="utf-8")
        except OSError:
            pass
    try:
        return kc.load_keyword_groups(GROUPS_FILE.read_text(encoding="utf-8"))
    except OSError:
        return kc.load_keyword_groups(kc.DEFAULT_KEYWORD_GROUPS_TEXT)


def load_group_colors():
    """{group name: MPC color int} for groups the person has given their own color, overriding
    the color of whichever folder supplies that pad. Unset groups just aren't in this dict (no
    entry = use the folder's own color)."""
    try:
        d = json.loads(GROUP_COLORS_FILE.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return {str(k): int(v) for k, v in d.items() if isinstance(d, dict)} if isinstance(d, dict) else {}


def store_group_colors(d):
    tmp = GROUP_COLORS_FILE.with_suffix(".tmp")
    tmp.write_text(json.dumps(d, indent=1), encoding="utf-8")
    os.replace(tmp, GROUP_COLORS_FILE)


def open_in_editor(path):
    """Opens a text file in the person's editor: TextEdit on macOS (as asked), the system
    default association on Windows, and xdg-open (the desktop's default) on Linux."""
    if sys.platform == "darwin":
        subprocess.run(["open", "-a", "TextEdit", str(path)], check=False)
    elif sys.platform.startswith("win"):
        os.startfile(str(path))  # noqa: S606 - Windows-only API, this branch is Windows-only
    else:
        subprocess.run(["xdg-open", str(path)], check=False)


def clean_padmap_entry(e):
    """A padmap entry is either a folder id (string) or, in pack/keyword mode, {"id":..,
    "group": name}, where name refers to a group in the person's keyword-groups file (resolved
    to actual keywords at generation time, so editing that file updates existing assignments
    automatically). Keeps only that shape, dropping anything a tampered or old state file
    might contain."""
    if isinstance(e, dict) and e.get("id"):
        group = str(e.get("group") or "").strip()
        return {"id": str(e["id"]), "group": group} if group else str(e["id"])
    return str(e)


def clean_pad_chokes(d):
    """{pad number (str): choke 0-32} from the page state / a preset; anything else is dropped.
    0 means "no choke" (overriding the folder's own), a missing pad means "use the folder's"."""
    out = {}
    if isinstance(d, dict):
        for k, v in d.items():
            try:
                n = int(v)
                if str(int(k)) == str(k).strip() and int(k) >= 1 and 0 <= n <= 32:
                    out[str(int(k))] = n
            except (TypeError, ValueError):
                pass
    return out


def clean_preset(p):
    """Keeps only what a preset stores: folders (path, color, mute group, pack flag) and pad
    assignments (including each pad's keywords, in pack/keyword mode)."""
    folders = [{"id": str(f["id"]), "path": str(f["path"]), "color": int(f.get("color") or 0),
                "mute": int(f.get("mute") or 0), "pack": bool(f.get("pack"))} for f in p["folders"]]
    padmap = {str(k): [clean_padmap_entry(e) for e in (v if isinstance(v, list) else [v])]
             for k, v in p["padmap"].items()}
    return {"folders": folders, "padmap": padmap, "padChoke": clean_pad_chokes(p.get("padChoke"))}


def palette():
    pal = [{"name": n, "value": v, "ok": ok} for n, v, ok in DEFAULT_PALETTE]
    try:
        for e in json.loads(PAL_FILE.read_text()):
            if all(p["value"] != e["value"] for p in pal):
                pal.append(e)
    except (OSError, ValueError):
        pass
    return pal


PICKER = None   # set by the desktop app: a function (kind, lang) -> (path | list | None, error)


def fresh_session():
    """Forget the previous session: the page starts empty (no folders, no pad assignments), so the
    person picks a preset or adds folders from scratch. Only the interface language is kept.
    Presets, keyword groups and colors are untouched. Reloading the page during a session keeps
    that session's own state, since this only runs when the app starts."""
    keep = {}
    try:
        old = json.loads(STATE_FILE.read_text())
        if isinstance(old, dict) and old.get("lang") in kc.MSG:
            keep["lang"] = old["lang"]
        if isinstance(old, dict) and old.get("theme") in THEMES:
            keep["theme"] = old["theme"]
    except (OSError, ValueError):
        pass
    try:
        STATE_FILE.write_text(json.dumps(keep, indent=1))
    except OSError:
        pass


def pick(kind, lang="en"):
    """Native dialog. Returns (path or list of paths, error)."""
    if PICKER is not None:
        return PICKER(kind, lang)
    if sys.platform != "darwin":
        return None, "nodialog"
    prompts = PROMPTS.get(lang, PROMPTS["en"])
    if kind == "folders":  # multiple selection (Cmd-click)
        lines = [f'set fs to choose folder with prompt "{prompts["folders"]}" with multiple selections allowed',
                 'set out to ""', 'repeat with f in fs',
                 'set out to out & POSIX path of f & linefeed', 'end repeat', 'return out']
        cmd = ["osascript"] + [x for l in lines for x in ("-e", l)]
        r = subprocess.run(cmd, capture_output=True, text=True)
        if r.returncode != 0:
            return [], None
        return [x.rstrip("/") or "/" for x in r.stdout.splitlines() if x.strip()], None
    verb = "choose file" if kind == "xpm" else "choose folder"
    r = subprocess.run(["osascript", "-e", f'POSIX path of ({verb} with prompt "{prompts[kind]}")'],
                       capture_output=True, text=True)
    if r.returncode != 0:
        return None, None  # cancelled
    p = r.stdout.strip()
    return (p.rstrip("/") or "/"), None


def run_job(st):
    JOB.update(log=[], done=False, error=None)
    log = JOB["log"].append
    if st.get("lang") in kc.MSG:
        kc.LANG = st["lang"]          # log lines and errors follow the interface language
    try:
        tpl = (st.get("template") or "").strip()
        template = Path(tpl).expanduser().read_text(encoding="utf-8") if tpl else kc.default_template()
        folders = {f["id"]: f for f in st["folders"]}
        groups = load_groups()
        group_colors = load_group_colors()
        pads, colors, mutes = {}, {}, {}
        pad_chokes = clean_pad_chokes(st.get("padChoke"))
        ctx = tuple(tuple(v) for v in groups.values())     # all groups, so overlaps are resolved
        for pad, entries in st["padmap"].items():
            entries = entries if isinstance(entries, list) else [entries]
            items, used_folders, used_groups = [], [], []
            for e in entries:
                fid = e["id"] if isinstance(e, dict) else e
                f = folders.get(fid)
                if not f:
                    continue
                path = Path(f["path"]).expanduser()
                group = e.get("group") if isinstance(e, dict) else None
                kw = groups.get(group) if group else None
                if group and not kw:        # never fall back to "the whole pack" for a typo/rename
                    log(kc.msg("group_missing", pad=pad, group=group))
                    continue
                items.append((path, kw, ctx) if kw else path)
                used_folders.append(f)
                used_groups.append(group)
            if items:   # the pad's color and mute group are those of its first folder, unless
                        # that entry's keyword group has its own color (see load_group_colors)
                pads[int(pad)] = items
                override = group_colors.get(used_groups[0]) if used_groups[0] else None
                colors[int(pad)] = int(override if override else (used_folders[0].get("color") or 0))
                mutes[int(pad)] = int(used_folders[0].get("mute") or 0)
                pc = pad_chokes.get(str(pad))       # a choke set on the pad itself wins over its folder's
                if pc is not None:
                    mutes[int(pad)] = pc
        if not pads:
            raise ValueError(kc.msg("no_pads"))
        seed = int(st["seed"]) if str(st.get("seed", "")).strip() else None
        match_key = st.get("matchKey") or None      # "" (off), "random", or a canonical key
        kc.generate_kits(template, st["output"], int(st["kits"]), st["name"] or "Kit {n:03d}", pads,
                         colors=colors, clear_others=bool(st["clear"]), recursive=bool(st["recursive"]),
                         hardlink=bool(st["hardlink"]), seed=seed, log=log, mutegroups=mutes,
                         match_key=match_key, pad_numbering=bool(st.get("padNumbering")),
                         flat_output=bool(st.get("flatOutput")),
                         warp_bpm=bool(st.get("warpBpm", True)), transpose_key=bool(st.get("transposeKey")))
    except Exception as e:  # show any error in the interface
        JOB["error"] = f"{type(e).__name__}: {e}"
    JOB["done"] = True


class H(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def send(self, obj, code=200, ctype="application/json"):
        body = obj.encode() if isinstance(obj, str) else json.dumps(obj).encode()
        self.send_response(code)
        self.send_header("Content-Type", ctype + "; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def authed(self):
        SEEN["last"] = time.time()
        q = parse_qs(urlparse(self.path).query)
        if self.headers.get("X-Token") != TOKEN and q.get("t", [""])[0] != TOKEN:
            self.send({"error": "token"}, 403)
            return False
        return True

    def do_GET(self):
        self.path = urlparse(self.path).path
        if self.path == "/":
            return self.send(PAGE.replace("__TOKEN__", TOKEN).replace("__VERSION__", kc.__version__),
                             ctype="text/html")
        if not self.authed():
            return
        if self.path == "/api/state":
            try:
                st = json.loads(STATE_FILE.read_text())
            except (OSError, ValueError):
                st = None
            return self.send({"state": st, "palette": palette(), "presets": load_presets(),
                              "keywordGroups": load_groups(), "groupColors": load_group_colors(),
                              "mac": sys.platform == "darwin"})
        if self.path == "/api/job":
            return self.send(JOB)
        if self.path == "/api/ping":
            return self.send({"ok": True})
        self.send({"error": "404"}, 404)

    def do_POST(self):
        if not self.authed():
            return
        self.path = urlparse(self.path).path
        data = json.loads(self.rfile.read(int(self.headers.get("Content-Length", 0))) or b"{}")
        if self.path == "/api/bye":      # the tab was closed
            SEEN["bye"] = time.time()
            return self.send({"ok": True})
        if self.path == "/api/state":
            STATE_FILE.write_text(json.dumps(data, indent=1))
            return self.send({"ok": True})
        if self.path == "/api/pick":
            path, err = pick(data["kind"], data.get("lang", "en"))
            return self.send({"path": path, "error": err})
        if self.path == "/api/presets":
            presets = load_presets()
            name = str(data.get("name", "")).strip()[:60]
            try:
                if data.get("action") == "save":
                    if not name:
                        raise ValueError
                    presets[name] = clean_preset(data["preset"])
                elif data.get("action") == "delete":
                    presets.pop(name, None)
                else:
                    raise ValueError
            except (KeyError, TypeError, ValueError, AttributeError):
                return self.send({"error": "invalid"})
            store_presets(presets)
            return self.send({"presets": presets})
        if self.path == "/api/group_color":
            name = str(data.get("name", "")).strip()
            if not name:
                return self.send({"error": "invalid"})
            try:
                color = int(data.get("color") or 0)
            except (TypeError, ValueError):
                return self.send({"error": "invalid"})
            colors = load_group_colors()
            if color:
                colors[name] = color
            else:
                colors.pop(name, None)   # 0 / missing = no override, use the folder's color
            store_group_colors(colors)
            return self.send({"groupColors": colors})
        if self.path == "/api/groups":
            action = data.get("action")
            if action == "reset":
                if GROUPS_FILE.is_file():
                    shutil.copy2(GROUPS_FILE, GROUPS_FILE.with_name(GROUPS_FILE.name + ".bak"))
                GROUPS_FILE.write_text(kc.DEFAULT_KEYWORD_GROUPS_TEXT, encoding="utf-8")
            elif action == "save":
                groups, err = save_groups(data.get("groups") or [])
                if err:
                    return self.send({"error": err})
                colors = load_group_colors()      # a renamed group keeps its color
                renames = data.get("renames") or {}
                changed = False
                for old, new in renames.items():
                    if old in colors and new in groups and old != new:
                        colors[new] = colors.pop(old)
                        changed = True
                for name in [n for n in colors if n not in groups]:
                    colors.pop(name)              # a deleted group's color is dropped
                    changed = True
                if changed:
                    store_group_colors(colors)
            elif action != "get":
                return self.send({"error": "invalid"})
            return self.send({"groups": load_groups(), "groupColors": load_group_colors()})
        if self.path == "/api/edit_groups":
            load_groups()      # make sure the file exists with real content before opening it
            try:
                open_in_editor(GROUPS_FILE)
            except OSError as e:
                return self.send({"error": str(e)})
            return self.send({"ok": True})
        if self.path == "/api/subfolders":
            d = Path(data["path"]).expanduser()
            subs = sorted((str(x) for x in d.iterdir() if x.is_dir() and not x.name.startswith(".")),
                          key=str.lower) if d.is_dir() else []
            return self.send({"paths": subs})
        if self.path == "/api/scan":
            d = Path(data["path"]).expanduser()
            rec = True if data.get("pack") else data.get("recursive", True)
            files = []
            for attempt in range(3):     # a drive that is still waking up can list empty at first
                try:
                    files = kc.scan_folder(d, rec) if d.is_dir() else []
                except OSError:
                    files = []
                if files or not d.is_dir():
                    break
                time.sleep(0.4)
            keys = {k: len(v) for k, v in kc.index_by_key(files).items()} if files else {}
            counts = {}
            if data.get("pack") and files:     # how many samples each keyword group would find
                draft = data.get("groups")     # the in-app editor asks about groups it hasn't saved yet
                if isinstance(draft, dict) and draft:
                    draft = {str(k): [str(w).strip() for w in v if str(w).strip()]
                             for k, v in draft.items() if isinstance(v, list)}
                counts = kc.group_match_counts(d, files, draft or load_groups())
            return self.send({"count": len(files) if d.is_dir() else -1, "keys": keys,
                              "groupCounts": counts})
        if self.path == "/api/import_colors":
            try:
                vals = kc.read_color_file(Path(data["path"]).expanduser())
            except (OSError, ValueError, EOFError) as e:
                return self.send({"error": str(e)})
            try:
                cur = json.loads(PAL_FILE.read_text())
            except (OSError, ValueError):
                cur = []
            known = {p["value"] for p in palette()}
            for v in vals:
                if v not in known:
                    cur.append({"name": f"Imported {len(cur) + 1}", "value": v, "ok": True})
            PAL_FILE.write_text(json.dumps(cur))
            return self.send({"palette": palette(), "found": len(vals)})
        if self.path == "/api/generate":
            if not JOB["done"]:
                return self.send({"error": "busy"})
            threading.Thread(target=run_job, args=(data,), daemon=True).start()
            return self.send({"ok": True})
        self.send({"error": "404"}, 404)


PAGE = r'''<!doctype html><html lang="en"><meta charset="utf-8"><title>Wavethings Kit Creator</title>
<meta name="viewport" content="width=device-width,initial-scale=1">
<style>
:root,html[data-theme=dark]{color-scheme:dark;--bg:#14161a;--pn:#202328;--ln:#31353c;--tx:#e8e9ec;--mu:#9a9fa8;--ac:#4c8dff;--acink:#fff;
 --btn:#2d3036;--btnh:#383c43;--bbd:#3b4048;--bsh:0 2px 0 #0007;--inp:#121316;--inptx:#e8e9ec;--card:#1a1c20;--cardon:#1c2a44;
 --pre:#0e0f11;--pretx:#c9ccd2;--hdbg:linear-gradient(#262a30,#1b1e23);--hdtx:#e8e9ec;--hdln:#31353c;--psh:0 1px 0 #ffffff0d inset,0 6px 18px #0005;
 --pad0:linear-gradient(#3a3d44,#26282d);--padtx:#aeb3bb;--mono:ui-monospace,SFMono-Regular,Menlo,Consolas,monospace}
html[data-theme=light]{color-scheme:light;--bg:#e6e9ed;--pn:#fff;--ln:#ccd1d8;--tx:#1d2127;--mu:#5d6672;--ac:#2563eb;--acink:#fff;
 --btn:#f3f5f7;--btnh:#e6e9ed;--bbd:#c5cad2;--bsh:0 2px 0 #0000001a;--inp:#fff;--inptx:#1d2127;--card:#f5f7f9;--cardon:#dfeaff;
 --pre:#f1f3f5;--pretx:#2b3138;--hdbg:linear-gradient(#ffffff,#eef1f4);--hdtx:#1d2127;--hdln:#ccd1d8;--psh:0 1px 0 #fff inset,0 6px 16px #2b3a5016;
 --pad0:linear-gradient(#dde1e6,#c3c9d1);--padtx:#59616c}
html[data-theme=mpc]{color-scheme:light;--bg:#b9b5a8;--pn:#d6d2c6;--ln:#8d897d;--tx:#25241f;--mu:#5a584f;--ac:#d6342a;--acink:#fff;
 --btn:#ebe7da;--btnh:#f6f3e8;--bbd:#7f7b6f;--bsh:0 3px 0 #6f6b60;--inp:#b3c6a0;--inptx:#18260f;--card:#c9c5b8;--cardon:#c2d4ae;
 --pre:#a8bd95;--pretx:#18260f;--hdbg:linear-gradient(#4a4a4c,#2f2f31);--hdtx:#ece8da;--hdln:#d6342a;--psh:0 1px 0 #fffb inset,0 -1px 0 #0003 inset,0 3px 0 #7f7b6f,0 8px 14px #0003;
 --pad0:linear-gradient(#4b4b4d,#2b2b2d);--padtx:#bdbab0}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--tx);font:14px -apple-system,system-ui,sans-serif}
html[data-theme=mpc] body{background:var(--bg) repeating-linear-gradient(90deg,#0000 0 3px,#0000000a 3px 4px)}
header{padding:12px 20px;background:var(--hdbg);color:var(--hdtx);border-bottom:3px solid var(--hdln);font-weight:600;font-size:16px;display:flex;justify-content:space-between;align-items:center;gap:12px}
html[data-theme=dark] header,html[data-theme=light] header{border-bottom-width:1px}
header .hd{display:flex;gap:8px;align-items:center}
header select{font-weight:400}
.brand{letter-spacing:.02em}.sub{opacity:.65;font-weight:400;font-size:13px;margin-left:6px}
footer{max-width:1200px;margin:0 auto;padding:4px 20px 22px;color:var(--mu);font-size:12px}
main{display:grid;grid-template-columns:minmax(320px,1fr) 460px;gap:16px;padding:16px 20px;max-width:1220px;margin:auto}
@media(max-width:900px){main{grid-template-columns:1fr}}
section{background:var(--pn);border:1px solid var(--ln);border-radius:12px;padding:14px;margin-bottom:16px;box-shadow:var(--psh)}
h2{margin:0 0 10px;font-size:12px;letter-spacing:.06em;text-transform:uppercase;color:var(--mu)}
button{background:var(--btn);color:var(--tx);border:1px solid var(--bbd);border-radius:7px;padding:6px 10px;cursor:pointer;font:inherit;box-shadow:var(--bsh);transition:transform .05s,box-shadow .05s}
button:hover{background:var(--btnh)}button:active{transform:translateY(2px);box-shadow:none}
button.pri{background:var(--ac);border-color:#0004;color:var(--acink);font-weight:600;padding:9px 16px}button.pri:hover{filter:brightness(1.1)}
button:disabled{opacity:.5}input[type=text],input[type=number]{background:var(--inp);color:var(--inptx);border:1px solid var(--bbd);border-radius:6px;padding:6px 8px;font:inherit;width:100%}
html[data-theme=mpc] input[type=text],html[data-theme=mpc] input[type=number],html[data-theme=mpc] select.big,html[data-theme=mpc] .card select{box-shadow:inset 0 2px 4px #0004;font-family:var(--mono);font-size:13px}
input[type=checkbox]{accent-color:var(--ac)}
.row{display:flex;gap:8px;align-items:center;margin-bottom:8px}.row label{width:90px;color:var(--mu);flex:none}
.path{flex:1;min-width:0;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;color:var(--mu);direction:rtl;text-align:left}
.card{display:flex;gap:10px;align-items:center;border:2px solid var(--ln);border-radius:9px;padding:8px 10px;margin-bottom:8px;cursor:pointer;background:var(--card);min-width:0}
.card.on{border-color:var(--ac);background:var(--cardon)}
.fi{flex:1;min-width:0}.fi b{display:block}
.fi small{color:var(--mu);display:block;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.dot{width:26px;height:26px;border-radius:50%;border:2px solid #fff6;flex:none;cursor:pointer;box-shadow:0 2px 3px #0006,inset 0 -3px 5px #0004,inset 0 2px 3px #fff5}
.tabs{display:flex;gap:4px;margin-bottom:10px}.tabs button{flex:1;padding:5px 0}.tabs .sel{background:var(--ac);border-color:#0004;color:var(--acink)}
.grid{display:grid;grid-template-columns:repeat(4,1fr);gap:12px;padding:4px 2px 8px}
.pad{position:relative;aspect-ratio:1;min-width:0;min-height:0;overflow:hidden;border-radius:12px;border:1px solid #0008;
 display:flex;flex-direction:column;align-items:center;justify-content:center;background:var(--pad0);cursor:pointer;color:var(--padtx);font-weight:600;user-select:none;
 box-shadow:0 5px 0 #0008,0 10px 14px #0006,inset 0 1px 1px #fff6,inset 0 -14px 18px #0000004d;text-shadow:0 1px 2px #0008;transition:transform .06s,box-shadow .06s,filter .1s}
.pad::before{content:"";position:absolute;inset:0;border-radius:inherit;pointer-events:none;background:radial-gradient(120% 80% at 30% 0%,#ffffff55,#fff0 55%),linear-gradient(#fff0 55%,#0003)}
.pad>*{position:relative}
.pad .padchokewrap{position:absolute}
.pad .n{position:absolute;top:5px;left:8px;font-size:11px;opacity:.85}
.pad:hover{filter:brightness(1.12)}
.pad:active{transform:translateY(4px);box-shadow:0 1px 0 #0008,0 3px 6px #0006,inset 0 1px 1px #fff4,inset 0 -8px 12px #00000040}
.pad.on{color:#fff}
.pad.lt{color:#1b1b1b;text-shadow:0 1px 0 #fff6}
.pad small{width:100%;box-sizing:border-box;padding:0 4px;font-weight:600;font-size:10px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;text-align:center}
.pad select{width:92%;max-width:92%;font-size:10px;padding:1px 2px;border-radius:4px;border:1px solid #0005;background:#000a;color:#fff;cursor:pointer;text-shadow:none}
.padkw{display:flex;align-items:center;justify-content:center;gap:3px;width:100%}
.padkw select{flex:1;min-width:0}
.padtools{position:absolute;top:3px;right:3px;display:flex;gap:3px}
.padcolor{width:14px;height:14px;border-radius:50%;flex:none;cursor:pointer;box-sizing:border-box;border:2px solid #fff;box-shadow:0 0 0 1px #000c}
.ovl{position:fixed;inset:0;background:#000a;display:none;align-items:center;justify-content:center;z-index:30}
.ovl>div{background:var(--pn);border:1px solid var(--ln);border-radius:12px;padding:16px;box-shadow:0 20px 50px #0008}
#dlg>div{width:min(420px,90vw)}
#dlgMsg{margin:0 0 12px;white-space:pre-wrap;line-height:1.4}#dlg input{margin-bottom:12px}
.packchk{display:flex;align-items:center;gap:4px;font-size:12px;color:var(--mu);flex:none;white-space:nowrap}
.hint{color:var(--mu);font-size:12px;margin-top:8px}
.chk{display:flex;gap:6px;align-items:center;margin:4px 0;color:var(--mu)}
#pal{z-index:9}#pal>div{width:340px}
.sw{display:grid;grid-template-columns:repeat(5,1fr);gap:8px;margin:12px 0}
.sw span{aspect-ratio:1;border-radius:9px;cursor:pointer;border:2px solid #fff3;position:relative;box-shadow:0 3px 0 #0006,inset 0 1px 1px #fff5,inset 0 -6px 8px #0003}
.sw span.est::after{content:"?";position:absolute;right:3px;top:0;font-size:11px;color:#fffb}
pre{background:var(--pre);color:var(--pretx);border-radius:6px;padding:10px;max-height:220px;overflow:auto;margin:10px 0 0;font:12px var(--mono);white-space:pre-wrap}
.err{color:#e5493d}
.card select{width:auto;background:var(--inp);color:var(--inptx);border:1px solid var(--bbd);border-radius:6px;padding:3px 4px;font:inherit;font-size:12px}
select.big,header select{background:var(--inp);color:var(--inptx);border:1px solid var(--bbd);border-radius:6px;padding:5px;font:inherit}
html[data-theme=mpc] header select{background:#3d3d40;color:#ece8da;border-color:#666;font-family:inherit}
.chkgrid{display:grid;grid-template-columns:repeat(8,1fr);gap:6px;margin-top:12px}.chkgrid button{padding:6px 0}.chkgrid button.wide{grid-column:span 8;text-align:left;padding:7px 10px}.chkgrid button.sel{background:var(--ac);color:var(--acink)}
.padchokewrap{position:absolute;bottom:4px;right:5px}
.padchoke{font-size:9px;font-weight:700;line-height:1;padding:2px 4px;border-radius:7px;background:#000b;color:#fff;border:1px solid #fff8;cursor:pointer;text-shadow:none;box-shadow:none}
.padchoke:hover{background:#000}.padchoke.dim{opacity:.7}
#opt>div{width:min(640px,94vw);max-height:90vh;display:flex;flex-direction:column;padding:18px}
#opt h3{margin:0 0 6px;font-size:17px}#optBody{overflow:auto;flex:1;padding-right:6px}
#opt h4{margin:16px 0 4px;font-size:12px;letter-spacing:.06em;text-transform:uppercase;color:var(--mu);border-bottom:1px solid var(--ln);padding-bottom:4px}
#opt h4:first-child{margin-top:6px}
#kw>div{width:min(940px,96vw);max-height:92vh;display:flex;flex-direction:column;padding:18px}
#kw h3{margin:0 0 4px;font-size:17px}
#kwRows{overflow:auto;flex:1;min-height:120px;margin:10px 0;padding-right:4px}
.kwr{display:grid;grid-template-columns:30px 170px 1fr 54px 30px;gap:6px;align-items:center;margin-bottom:6px}
.kwr input{font-family:var(--mono);font-size:13px}
.kwr .cnt{font-size:12px;color:var(--mu);text-align:right}.kwr .cnt.zero{color:#e5493d;font-weight:600}
.kwr .grip{cursor:grab;text-align:center;font-size:20px;line-height:1;color:var(--mu);user-select:none;touch-action:none;padding:6px 0;border-radius:6px}.kwr .grip:hover,.kwr .grip:focus-visible{background:var(--btn);color:var(--tx);outline:none}
.kwr.dragging{opacity:.9;background:var(--cardon);border-radius:8px;box-shadow:0 6px 18px #0006;position:relative;z-index:2}.kwr.dragging .grip{cursor:grabbing}
.kwr.hd{font-size:11px;text-transform:uppercase;letter-spacing:.05em;color:var(--mu);margin:0}
.kwr input.bad{border-color:#e5493d}
.kwbar{display:flex;gap:8px;align-items:center;flex-wrap:wrap}.kwbar .sp{flex:1}
#kwHelp{font-size:12px;color:var(--mu);line-height:1.5;white-space:pre-wrap;background:var(--card);border:1px solid var(--ln);border-radius:8px;padding:8px 10px;margin:6px 0 0}
#kwMsg{font-size:13px;min-height:18px}
@media(max-width:640px){.kwr{grid-template-columns:26px 1fr 40px 28px;margin-bottom:12px}.kwr input:nth-of-type(2){grid-column:2/5;grid-row:2}.kwr button:last-child{grid-row:1;grid-column:4}.kwr .cnt{grid-row:1;grid-column:3}.kwr.hd{display:none}}
</style>
<header><span><b class="brand">Wavethings</b> Kit Creator <span class="sub" data-i18n="hdr_sub"></span></span><span class="hd"><select id="theme" title="Theme"><option value="dark"></option><option value="light"></option><option value="mpc"></option></select><select id="lang"><option value="en">English</option><option value="es">Español</option></select></span></header>
<main>
<div>
 <section><h2 data-i18n="sec_presets"></h2>
  <div class="row" style="margin:0"><select class="big" id="presetSel" style="flex:1;min-width:0"></select><button id="btnPload" onclick="loadPreset()" data-i18n="btn_pload"></button><button id="btnPsave" onclick="savePreset()" data-i18n="btn_psave"></button><button id="btnPdel" onclick="deletePreset()" data-i18n="btn_pdel"></button></div>
  <div class="hint" data-i18n="hint_presets"></div>
 </section>
 <section><h2 data-i18n="sec_folders"></h2>
  <div id="folders"></div>
  <div class="row" style="margin:0"><button onclick="addFolder()" data-i18n="btn_add"></button><button onclick="addSubfolders()" data-i18n="btn_addsub" data-title="title_addsub"></button><button id="btnRescan" onclick="rescanAll(true)" style="margin-left:auto" data-i18n="btn_rescan" data-title="title_rescan"></button><button id="btnClear" onclick="clearFolders()" data-i18n="btn_clear" data-title="title_clear"></button></div>
  <div class="hint" data-i18n="hint_folders"></div>
  <div class="row" style="margin-top:4px"><span id="groupsInfo" class="hint" style="margin:0"></span><button onclick="kwOpen()" data-title="title_editgroups" data-i18n="btn_editgroups"></button></div>
 </section>
 <section><h2 data-i18n="sec_output"></h2>
  <div class="row"><label data-i18n="lbl_template"></label><button onclick="pickTo('template','xpm')" data-i18n="btn_othertpl"></button><button onclick="S.template='';render();save()" data-i18n="btn_builtin" data-title="title_builtin"></button><span class="path" id="tpl"></span></div>
  <div class="row"><label data-i18n="lbl_folder"></label><button onclick="pickTo('output','out')" data-i18n="btn_choose"></button><span class="path" id="out"></span></div>
  <div class="row"><label data-i18n="lbl_kits"></label><input type="number" min="1" id="kits" style="width:90px">
   <label style="width:auto;margin-left:10px" data-i18n="lbl_name"></label><input type="text" id="name"></div>
  <div class="row"><label data-i18n="lbl_seed"></label><input type="text" id="seed" data-ph="ph_seed"></div>
  <div class="row" style="margin-top:4px"><button onclick="openOpts()" data-i18n="btn_options"></button><span id="optSum" class="hint" style="margin:0"></span></div>
  <div class="row" style="margin-top:12px"><button class="pri" id="go" onclick="generate()" data-i18n="btn_go"></button><span id="msg"></span></div>
  <pre id="log" style="display:none"></pre>
 </section>
</div>
<div>
 <section><h2><span data-i18n="sec_pads"></span> · <span id="bname"></span></h2>
  <div class="tabs" id="tabs"></div>
  <label class="chk" style="margin:0 0 10px"><input type="checkbox" id="mix"><span data-i18n="chk_mix"></span></label>
  <div class="grid" id="pads"></div>
  <div class="row" style="margin-top:10px"><button onclick="fillBank()" data-i18n="btn_fill"></button><button onclick="clearBank()" data-i18n="btn_clearbank"></button></div>
  <div class="row"><span style="color:var(--mu)" data-i18n="lbl_copy"></span><select class="big" id="copyTo" onchange="copyTarget=this.value"></select><button onclick="copyBank()" data-i18n="btn_copy"></button></div>
  <div class="hint" data-i18n="hint_pads"></div>
 </section>
</div>
</main>
<footer><span data-i18n="footer"></span> · v__VERSION__</footer>
<div id="dlg" class="ovl"><div><p id="dlgMsg"></p><input type="text" id="dlgIn"><div class="row" style="justify-content:flex-end;margin:0"><button id="dlgNo"></button><button class="pri" id="dlgOk"></button></div></div></div>
<div id="pal" class="ovl" onclick="if(event.target===this)this.style.display='none'"><div>
 <b id="palTitle"></b><div class="sw" id="sw"></div>
 <div class="row"><button id="btnNocolor" onclick="setColor(0)"></button><button id="btnImport" onclick="importColors()" data-i18n="btn_import"></button></div>
 <div class="hint" id="palHint"></div>
</div></div>
<div id="opt" class="ovl" onclick="if(event.target===this)closeOpts()"><div>
 <h3 data-i18n="opt_title"></h3>
 <div id="optBody">
  <h4 data-i18n="opt_sec_samples"></h4>
  <label class="chk"><input type="checkbox" id="recursive"><span data-i18n="chk_recursive"></span></label>
  <label class="chk"><input type="checkbox" id="warpBpm"><span data-i18n="chk_warp"></span></label>
  <div class="hint" data-i18n="hint_warp"></div>
  <label class="chk"><input type="checkbox" id="clear"><span data-i18n="chk_clear"></span></label>
  <h4 data-i18n="opt_sec_key"></h4>
  <label class="chk"><input type="checkbox" id="matchKeyOn"><span data-i18n="chk_matchkey"></span></label>
  <div class="row" id="matchKeyRow" style="display:none;margin:0 0 8px"><label data-i18n="lbl_matchkey"></label><select class="big" id="matchKeySel" style="flex:1"></select></div>
  <label class="chk" id="transposeRow"><input type="checkbox" id="transposeKey"><span data-i18n="chk_transpose"></span></label>
  <div class="hint" id="matchKeyHint" data-i18n="hint_matchkey"></div>
  <h4 data-i18n="opt_sec_output"></h4>
  <label class="chk"><input type="checkbox" id="flatOutput"><span data-i18n="chk_flat"></span></label>
  <div class="hint" data-i18n="hint_flat"></div>
  <label class="chk"><input type="checkbox" id="hardlink"><span data-i18n="chk_hardlink"></span></label>
  <label class="chk"><input type="checkbox" id="padNumbering"><span data-i18n="chk_padnum"></span></label>
  <div class="hint" data-i18n="hint_padnum"></div>
 </div>
 <div class="row" style="justify-content:flex-end;margin:12px 0 0"><button class="pri" onclick="closeOpts()" data-i18n="opt_close"></button></div>
</div></div>
<div id="chk" class="ovl" onclick="if(event.target===this)this.style.display='none'"><div style="width:min(380px,92vw)">
 <b id="chkTitle"></b><div class="hint" data-i18n="hint_choke_pad"></div>
 <div class="chkgrid" id="chkGrid"></div>
</div></div>
<div id="kw" class="ovl"><div>
 <h3 data-i18n="kw_title"></h3><div class="hint" style="margin:0" data-i18n="kw_sub"></div>
 <div class="kwbar" style="margin-top:8px"><span id="kwTestLbl" style="color:var(--mu)" data-i18n="kw_test"></span><select class="big" id="kwTest" style="min-width:200px;max-width:360px"></select><span class="sp"></span><button onclick="$('kwHelp').style.display=$('kwHelp').style.display==='none'?'block':'none'" data-i18n="kw_help_btn"></button></div>
 <div id="kwHelp" style="display:none" data-i18n="kw_help"></div>
 <div id="kwRows"></div>
 <div id="kwMsg" class="err"></div>
 <div class="kwbar"><button onclick="kwAdd()" data-i18n="kw_add"></button><button onclick="kwReset()" data-i18n="kw_reset"></button><button onclick="editGroupsFile()" data-title="title_editgroups" data-i18n="kw_openfile"></button><span class="sp"></span><button onclick="kwClose()" data-i18n="dlg_cancel"></button><button class="pri" onclick="kwSave()" data-i18n="kw_save"></button></div>
</div></div>
<script>
const TOKEN="__TOKEN__";
const I18N={
en:{sec_presets:"Presets",btn_pload:"Load",btn_psave:"Save…",btn_pdel:"Delete",preset_none:"(no presets yet)",preset_choose:"Choose a preset…",
 hint_presets:"A preset remembers your folders, pad assignments, choke groups and colors. Folder paths are saved as they are on this computer.",
 preset_name:"Preset name:",preset_overwrite:"A preset called “{0}” already exists. Overwrite it?",preset_saved:"Preset “{0}” saved",
 preset_load_confirm:"Replace the current folders and pad assignments with preset “{0}”?",preset_loaded:"Preset “{0}” loaded",
 preset_missing:" · {0} folder(s) not found (is a drive disconnected?)",preset_delete_confirm:"Delete preset “{0}”?",preset_need_folders:"Add at least one folder first",
 hdr_sub:"· .xpm drum kit generator for MPC",footer:"© 2026 Wavethings · Open source (MIT License)",sec_folders:"Sample folders",btn_rescan:"↻ Rescan",title_rescan:"Read the folders again (also happens automatically when you come back to this window)",rescanned:"{0} folder(s) rescanned",dlg_ok:"OK",dlg_cancel:"Cancel",btn_add:"＋ Add folders",btn_addsub:"＋ Add subfolders of…",btn_clear:"Clear all",title_clear:"Remove every folder and its pad assignments",confirm_clear:"Remove all {0} folders and their pad assignments?",cleared:"All folders removed",
 title_addsub:"Adds each subfolder of a parent folder as a separate folder",
 hint_folders:"1) Add folders (⌘-click to pick several, or a parent folder to add all its subfolders) · 2) select one · 3) click the pads on the right to assign it. The folder's color is applied to its pads on the MPC. “Choke” = mute group: pads of folders with the same number cut each other off (e.g. closed and open hi-hat).",
 sec_output:"Output",lbl_template:"Template",btn_othertpl:"Use another .xpm…",btn_builtin:"Built-in",title_builtin:"Back to the built-in template",
 lbl_folder:"Folder",btn_choose:"Choose…",lbl_kits:"No. of kits",lbl_name:"Name",lbl_seed:"Seed",ph_seed:"(optional) same seed = same kits",
 chk_recursive:"Include subfolders",chk_clear:"Empty and uncolor unassigned pads",btn_options:"⚙ Options…",opt_title:"Options",opt_close:"Done",opt_sec_samples:"Samples",opt_sec_key:"Key",opt_sec_output:"Output",
 opt_sum_none:"Default options",opt_warp:"Warp loops",opt_key:"key {0}",opt_transpose:"transposing",opt_flat:"one folder",opt_link:"linking",opt_padnum:"pad numbering",opt_norec:"no subfolders",opt_keep:"keep other pads",
 chk_warp:"Warp loops (follow the project's tempo)",
 hint_warp:"Every loop gets Warp switched on, so it follows the project's tempo. A sample is a loop when its name or folder says “loop”, or when its pad uses a Loop keyword group. The tempo is taken from the name (“128bpm”, or a bare number like “DPT_A#_138_Bass_Loop” if it fits the loop's length); if there is none, it is estimated from the loop's length (whole 4/4 bars, 90–180 BPM). One-shots are never warped. Samples longer than about 6.8 s also get their end set to their real end, so long loops are no longer cut.",
 chk_transpose:"Transpose samples in other keys to the kit's key instead of skipping them",
 chk_hardlink:"Link samples instead of copying (same drive only)",
 chk_flat:"All kits and samples in one folder",
 hint_flat:"Instead of one folder per kit, every .xpm and all samples go straight into the output folder. Easier to browse on the MPC, whose file browser can filter to show only kits. A sample that is already there with identical content is shared by the kits; a different file with the same name gets a numeric suffix. Nothing existing is overwritten.",
 chk_padnum:"Number samples by pad (for Maschine and others)",
 hint_padnum:"Prefixes every output sample with its pad number (e.g. \"001_Kick.wav\"), so sorting the kit folder by name also sorts it by pad. Lets samplers with no .xpm support of their own import the kit too: select a bank's 16 files together in Finder/Explorer (already in order) and drop them onto the sampler's first pad — Maschine, Battery and most hardware samplers auto-assign one file per pad from there.",
 chk_matchkey:"Give each kit one key",lbl_matchkey:"Key",key_random:"Random per kit",
 hint_matchkey:"Reads the root note from file names (e.g. \"Bass_C1\", \"Synth_Dm\", \"Lead - F#3\") and, when a folder has that information, keeps every kit's samples in one key. Folders without a detected key (drums, for example) are never filtered. Detection is best-effort and may miss or misread some names; add {key} to the name pattern above to show each kit's key. Filterable folders show a 🎵 badge. With the option below, samples in other keys are not skipped but pitched to the kit's key (relative keys count as the same: Am = C), and the log lists each transposed pad. Drums are not touched.",
 hint_matchkey_off:"No folder has a detected key yet — add \"C1\", \"Dm\", \"F#\" and the like to your file names to use this filter.",
 key_badge:"🎵 {0}/{1} tagged · {2}",
 pack_toggle:"Pack",pack_toggle_title:"Unsorted pack: pads linked to it show a dropdown to pick a keyword group instead of using the whole folder",
 kw_whole:"— whole pack —",
 btn_go:"Generate kits",sec_pads:"Pads",bank:"Bank {0}",
 chk_mix:"Mix: clicking a pad adds the active folder (instead of replacing). Shift+click does the opposite.",
 btn_fill:"Whole bank → active folder",btn_clearbank:"Clear bank",lbl_copy:"Copy this bank to",btn_copy:"Copy",opt_all:"all the others",
 hint_pads:"Layout matches the MPC: pad 1 is bottom-left. Pads with several folders show diagonal stripes: for each kit one folder is picked at random (equal probability for all) and then a sample from it. The pad's color and choke come from its first folder. A pad linked to a Pack folder shows a dropdown to pick a keyword group for it — leave it on “— whole pack —” to use every sample in the pack instead. Edit the groups with “Edit keyword groups”.",
 pal_title:"Folder color",btn_nocolor:"No color",btn_import:"Import colors from an .xpm",
 pal_title_group:"Color for group “{0}”",btn_usefolder:"Use the folder's color",
 hint_pal_group:"This color is used on every pad set to this group, wherever it's assigned, instead of its folder's color.",
 group_color_title:"Set a color for every pad using “{0}”",
 btn_editgroups:"✎ Edit keyword groups",title_editgroups:"Edit the keyword groups here, or open keyword_groups.txt in a text editor",
 theme_dark:"Dark",theme_light:"Light",theme_mpc:"Classic MPC",
 kw_title:"Keyword groups",kw_sub:"Each group is a pad type for Pack mode. Separate keywords with commas. Drag ⠿ to reorder.",
 kw_help_btn:"Syntax help",kw_help:"word  → the sample's folder or file name contains it (case-insensitive).\n-word  → exclusion: a sample containing it never goes in this group (“-loop” keeps loops out of one-shot groups).\n+word  → requirement: it must contain it (“Loop Bass: +loop, bass, sub” = loops that also say bass or sub).\nShort words (up to 3-4 letters, like sd, cp, hh) only match as whole words. The order here is the order of the pad dropdown.",
 kw_test:"Count samples in:",kw_test_none:"(no Pack folder)",kw_add:"＋ Add group",kw_reset:"Restore defaults",kw_openfile:"Open as text file",kw_save:"Save groups",
 kw_col_name:"Group",kw_col_words:"Keywords",kw_name_ph:"Group name",kw_words_ph:"kick, kik, bd, -loop",kw_del:"Delete group",
 kw_drag:"Drag to reorder (or Alt+↑/↓)",
 choke_title:"Choke (mute group) of pad {0}",choke_chip:"Choke: {0}",choke_follow:"Follow the folder ({0})",choke_none:"No choke",choke_n:"Choke {0}",
 hint_choke_pad:"Pads with the same choke number cut each other off (e.g. closed and open hi-hat). Set it here for a single pad — useful when several pads come from one Pack folder.",
 kw_reset_confirm:"Replace all your groups with the built-in defaults? Your current file is kept as keyword_groups.txt.bak.",
 kw_used_confirm:"{0} pad assignment(s) use groups you deleted or renamed to something unknown. They will go back to “whole pack”. Continue?",
 kw_saved:"{0} keyword group(s) saved",
 err_bad_name:"Every group needs a name (it cannot contain “:”).",err_dup_name:"Two groups have the same name.",err_no_keywords:"Every group needs at least one keyword.",err_no_groups:"Add at least one group.",
 groups_count:"{0} keyword group(s) loaded",
 hint_pal:"These are the MPC's own pad colors. If you need one that isn't here, import an .xpm where you painted pads with it (classic format or the one saved by MPC 3.x) and it will be added.",
 not_found:"⚠ folder not found",n_samples:"{0} samples",pads_lbl:"pads",none:"none",chg_color:"Change color",mute_grp:"Mute group",
 nochoke:"No choke",choke:"Choke {0}",no_folders:"No folders yet.",tpl_builtin:"Built-in (128 empty pads)",not_chosen:"(not chosen)",
 pick_first:"Select a folder first",added:"{0} folder(s) added",added_sub:"{0} subfolder(s) added",skipped:" ({0} skipped: empty or already added)",
 copied:"Bank {0} copied to {1}",need_out:"Missing output folder",need_pads:"Assign a folder to at least one pad",generating:"Generating…",done:"✔ Done",
 busy:"A generation is already running",found:"{0} colors found in the file.",ask_path:"Full path:",ask_paths:"Folder paths separated by ;",
 "col.red_orange":"Red-orange","col.amber":"Amber","col.mustard":"Mustard","col.gold":"Gold","col.chartreuse":"Chartreuse","col.light_green":"Light green","col.spring":"Spring green","col.mint":"Mint","col.sky":"Sky blue","col.azure":"Azure","col.indigo":"Indigo","col.purple":"Purple","col.rose":"Rose","col.jade":"Jade","col.red":"Red","col.orange":"Orange","col.yellow":"Yellow","col.green":"Green","col.petrol":"Petrol blue","col.lime":"Lime","col.turquoise":"Turquoise","col.cyan":"Cyan","col.blue":"Blue","col.violet":"Violet","col.magenta":"Magenta","col.pink":"Pink","col.white":"White","col.grey":"Grey","col.imported":"Imported"},
es:{sec_presets:"Presets",btn_pload:"Cargar",btn_psave:"Guardar…",btn_pdel:"Borrar",preset_none:"(aún no hay presets)",preset_choose:"Elige un preset…",
 hint_presets:"Un preset recuerda tus carpetas, asignaciones de pads, grupos de choque y colores. Las rutas se guardan tal como están en este equipo.",
 preset_name:"Nombre del preset:",preset_overwrite:"Ya existe un preset llamado «{0}». ¿Sobrescribirlo?",preset_saved:"Preset «{0}» guardado",
 preset_load_confirm:"¿Reemplazar las carpetas y asignaciones actuales por el preset «{0}»?",preset_loaded:"Preset «{0}» cargado",
 preset_missing:" · {0} carpeta(s) no encontrada(s) (¿hay un disco desconectado?)",preset_delete_confirm:"¿Borrar el preset «{0}»?",preset_need_folders:"Añade primero alguna carpeta",
 hdr_sub:"· generador de kits .xpm para MPC",footer:"© 2026 Wavethings · Código abierto (Licencia MIT)",sec_folders:"Carpetas de samples",btn_rescan:"↻ Reescanear",title_rescan:"Vuelve a leer las carpetas (también ocurre solo al volver a esta ventana)",rescanned:"{0} carpeta(s) reescaneadas",dlg_ok:"Aceptar",dlg_cancel:"Cancelar",btn_add:"＋ Añadir carpetas",btn_addsub:"＋ Añadir subcarpetas de…",btn_clear:"Quitar todas",title_clear:"Quita todas las carpetas y sus asignaciones de pads",confirm_clear:"¿Quitar las {0} carpetas y sus asignaciones de pads?",cleared:"Carpetas quitadas",
 title_addsub:"Añade cada subcarpeta de una carpeta madre como carpeta independiente",
 hint_folders:"1) Añade carpetas (⌘-clic para elegir varias, o una carpeta madre para añadir todas sus subcarpetas) · 2) selecciona una · 3) pulsa los pads de la derecha para asignársela. El color de la carpeta se aplica a sus pads en la MPC. “Choke” = grupo de choque: los pads de carpetas con el mismo número se cortan entre sí (p. ej. hi-hat cerrado y abierto).",
 sec_output:"Salida",lbl_template:"Plantilla",btn_othertpl:"Usar otro .xpm…",btn_builtin:"Integrada",title_builtin:"Volver a la plantilla integrada",
 lbl_folder:"Carpeta",btn_choose:"Elegir…",lbl_kits:"Nº de kits",lbl_name:"Nombre",lbl_seed:"Semilla",ph_seed:"(opcional) misma semilla = mismos kits",
 chk_recursive:"Incluir subcarpetas",chk_clear:"Vaciar y sin color en pads sin asignar",btn_options:"⚙ Opciones…",opt_title:"Opciones",opt_close:"Hecho",opt_sec_samples:"Samples",opt_sec_key:"Tonalidad",opt_sec_output:"Salida",
 opt_sum_none:"Opciones por defecto",opt_warp:"Warp en loops",opt_key:"tonalidad {0}",opt_transpose:"transponiendo",opt_flat:"una carpeta",opt_link:"enlazando",opt_padnum:"numeración de pads",opt_norec:"sin subcarpetas",opt_keep:"conservar otros pads",
 chk_warp:"Warp en los loops (siguen el tempo del proyecto)",
 hint_warp:"Todos los loops reciben Warp activado, así siguen el tempo del proyecto. Un sample es un loop si su nombre o su carpeta dice «loop», o si su pad usa un grupo de palabras clave de Loop. El tempo se toma del nombre («128bpm», o un número suelto como «DPT_A#_138_Bass_Loop» si encaja con la duración del loop); si no hay, se estima por la duración del loop (compases de 4/4 enteros, 90–180 BPM). Los one-shots nunca llevan Warp. Los samples de más de unos 6,8 s también tienen su final en su final real, así los loops largos ya no se cortan.",
 chk_transpose:"Transponer a la tonalidad del kit los samples de otras tonalidades en vez de descartarlos",
 chk_hardlink:"Enlazar samples en vez de copiar (solo en el mismo disco)",
 chk_flat:"Todos los kits y samples en una sola carpeta",
 hint_flat:"En lugar de una carpeta por kit, todos los .xpm y los samples van directamente a la carpeta de salida. Más cómodo en la MPC, cuyo navegador puede filtrar para mostrar solo kits. Un sample que ya esté allí con el mismo contenido se comparte entre kits; un archivo distinto con el mismo nombre recibe un sufijo numérico. No se sobrescribe nada existente.",
 chk_padnum:"Numerar samples por pad (para Maschine y otros)",
 hint_padnum:"Añade el número de pad delante de cada muestra de salida (p. ej. \"001_Kick.wav\"), para que ordenar la carpeta del kit por nombre también la ordene por pad. Así, un sampler sin soporte propio de .xpm puede importar el kit igualmente: selecciona juntos los 16 archivos de un banco en el Finder/Explorador (ya están en orden) y suéltalos sobre el primer pad del sampler — Maschine, Battery y la mayoría de samplers hardware asignan uno por pad a partir de ahí.",
 chk_matchkey:"Dar una tonalidad a cada kit",lbl_matchkey:"Tonalidad",key_random:"Aleatoria por kit",
 hint_matchkey:"Lee la nota raíz en el nombre del archivo (p. ej. \"Bass_C1\", \"Synth_Dm\", \"Lead - F#3\") y, cuando una carpeta tiene esa información, usa siempre la misma tonalidad dentro de cada kit. Las carpetas sin tonalidad detectada (batería, por ejemplo) nunca se filtran. La detección es aproximada y puede fallar con algunos nombres; añade {key} al patrón de nombre de arriba para mostrar la tonalidad de cada kit. Las carpetas filtrables llevan una insignia 🎵. Con la opción de abajo, los samples de otras tonalidades no se descartan sino que se suben o bajan de tono a la del kit (las relativas cuentan como la misma: Am = C), y el registro lista cada pad transpuesto. Los drums no se tocan.",
 hint_matchkey_off:"Ninguna carpeta tiene tonalidad detectada todavía — añade \"C1\", \"Dm\", \"F#\" o similar a los nombres de archivo para usar este filtro.",
 key_badge:"🎵 {0}/{1} con tonalidad · {2}",
 pack_toggle:"Pack",pack_toggle_title:"Pack sin ordenar: los pads vinculados a él muestran un desplegable para elegir un grupo de palabras clave en vez de usar la carpeta entera",
 kw_whole:"— todo el pack —",
 btn_go:"Generar kits",sec_pads:"Pads",bank:"Banco {0}",
 chk_mix:"Mezclar: al pulsar un pad, añadir la carpeta activa (en vez de reemplazar). Mayús+clic hace lo contrario.",
 btn_fill:"Banco completo → carpeta activa",btn_clearbank:"Vaciar banco",lbl_copy:"Copiar este banco a",btn_copy:"Copiar",opt_all:"todos los demás",
 hint_pads:"Disposición igual que la MPC: el pad 1 es el de abajo a la izquierda. Los pads con varias carpetas se ven con franjas diagonales: en cada kit se elige una carpeta al azar (todas con la misma probabilidad) y una muestra de ella. El color y el choke del pad son los de su primera carpeta. Un pad vinculado a una carpeta Pack muestra un desplegable para elegir su grupo de palabras clave — déjalo en «— todo el pack —» para usar todas las muestras del pack. Edita los grupos con «Editar grupos de palabras clave».",
 pal_title:"Color de la carpeta",btn_nocolor:"Sin color",btn_import:"Importar colores de un .xpm",
 pal_title_group:"Color del grupo «{0}»",btn_usefolder:"Usar el color de la carpeta",
 hint_pal_group:"Este color se usa en todos los pads con este grupo, dondequiera que estén asignados, en vez del color de su carpeta.",
 group_color_title:"Fija un color para todos los pads que usan «{0}»",
 btn_editgroups:"✎ Editar grupos de palabras clave",title_editgroups:"Edita los grupos aquí, o abre keyword_groups.txt en un editor de texto",
 theme_dark:"Oscuro",theme_light:"Claro",theme_mpc:"MPC clásico",
 kw_title:"Grupos de palabras clave",kw_sub:"Cada grupo es un tipo de pad para el modo Pack. Separa las palabras con comas. Arrastra ⠿ para reordenar.",
 kw_help_btn:"Ayuda de sintaxis",kw_help:"palabra  → el nombre de la carpeta o del archivo la contiene (sin distinguir mayúsculas).\n-palabra  → exclusión: una muestra que la contenga nunca entra en este grupo («-loop» deja los loops fuera de los grupos de one-shots).\n+palabra  → requisito: debe contenerla («Loop Bass: +loop, bass, sub» = loops que además dicen bass o sub).\nLas palabras cortas (hasta 3-4 letras, como sd, cp, hh) solo coinciden como palabra completa. El orden de aquí es el del desplegable de los pads.",
 kw_test:"Contar muestras en:",kw_test_none:"(sin carpeta Pack)",kw_add:"＋ Añadir grupo",kw_reset:"Restaurar valores",kw_openfile:"Abrir como archivo de texto",kw_save:"Guardar grupos",
 kw_col_name:"Grupo",kw_col_words:"Palabras clave",kw_name_ph:"Nombre del grupo",kw_words_ph:"kick, kik, bd, -loop",kw_del:"Borrar grupo",
 kw_drag:"Arrastra para reordenar (o Alt+↑/↓)",
 choke_title:"Choke (grupo de silencio) del pad {0}",choke_chip:"Choke: {0}",choke_follow:"Seguir a la carpeta ({0})",choke_none:"Sin choke",choke_n:"Choke {0}",
 hint_choke_pad:"Los pads con el mismo número de choke se cortan entre sí (p. ej. hi-hat cerrado y abierto). Ponlo aquí en un pad concreto — útil cuando varios pads salen de una misma carpeta Pack.",
 kw_reset_confirm:"¿Sustituir todos tus grupos por los predeterminados? Tu archivo actual se guarda como keyword_groups.txt.bak.",
 kw_used_confirm:"{0} asignación(es) de pads usan grupos que has borrado o renombrado a algo desconocido. Volverán a «todo el pack». ¿Continuar?",
 kw_saved:"{0} grupo(s) de palabras clave guardados",
 err_bad_name:"Cada grupo necesita un nombre (no puede contener «:»).",err_dup_name:"Hay dos grupos con el mismo nombre.",err_no_keywords:"Cada grupo necesita al menos una palabra clave.",err_no_groups:"Añade al menos un grupo.",
 groups_count:"{0} grupo(s) de palabras clave cargados",
 hint_pal:"Estos son los colores de pad propios de la MPC. Si necesitas otro que no esté, importa un .xpm donde hayas pintado pads con él (formato clásico o el que guarda la MPC 3.x) y se añadirá.",
 not_found:"⚠ carpeta no encontrada",n_samples:"{0} samples",pads_lbl:"pads",none:"ninguno",chg_color:"Cambiar color",mute_grp:"Grupo de choque",
 nochoke:"Sin choke",choke:"Choke {0}",no_folders:"Aún no hay carpetas.",tpl_builtin:"Integrada (128 pads vacíos)",not_chosen:"(sin elegir)",
 pick_first:"Selecciona primero una carpeta",added:"{0} carpeta(s) añadida(s)",added_sub:"{0} subcarpeta(s) añadida(s)",skipped:" ({0} omitidas: vacías o ya añadidas)",
 copied:"Banco {0} copiado a {1}",need_out:"Falta la carpeta de salida",need_pads:"Asigna alguna carpeta a algún pad",generating:"Generando…",done:"✔ Terminado",
 busy:"Ya hay una generación en curso",found:"{0} colores encontrados en el archivo.",ask_path:"Ruta completa:",ask_paths:"Rutas de carpetas separadas por ;",
 "col.red_orange":"Rojo anaranjado","col.amber":"Ámbar","col.mustard":"Mostaza","col.gold":"Dorado","col.chartreuse":"Chartreuse","col.light_green":"Verde claro","col.spring":"Verde primavera","col.mint":"Menta","col.sky":"Azul cielo","col.azure":"Azur","col.indigo":"Índigo","col.purple":"Púrpura","col.rose":"Rosa fuerte","col.jade":"Jade","col.red":"Rojo","col.orange":"Naranja","col.yellow":"Amarillo","col.green":"Verde","col.petrol":"Azul petróleo","col.lime":"Lima","col.turquoise":"Turquesa","col.cyan":"Cian","col.blue":"Azul","col.violet":"Violeta","col.magenta":"Magenta","col.pink":"Rosa","col.white":"Blanco","col.grey":"Gris","col.imported":"Importado"}};
let LANG="en";
const t=(k,...a)=>{let s=(I18N[LANG]&&I18N[LANG][k])??I18N.en[k]??k;a.forEach((v,i)=>s=s.split("{"+i+"}").join(v));return s};
function applyStatic(){
  document.querySelectorAll("[data-i18n]").forEach(e=>e.textContent=t(e.dataset.i18n));
  document.querySelectorAll("[data-ph]").forEach(e=>e.placeholder=t(e.dataset.ph));
  document.querySelectorAll("[data-title]").forEach(e=>e.title=t(e.dataset.title));
  for(const o of $("theme").options)o.textContent=t(`theme_${o.value}`);
  document.documentElement.lang=LANG}
const colName=c=>{const m=/^Import(?:ed|ado) (\d+)$/.exec(c.name);return m?t("col.imported")+" "+m[1]:(I18N[LANG]["col."+c.name]||c.name)};
const S={template:"",output:"",kits:10,name:"Kit {n:03d}",seed:"",recursive:true,clear:true,hardlink:false,mix:false,matchKey:"",padNumbering:false,flatOutput:false,lang:"",theme:"",preset:"",folders:[],padmap:{},padChoke:{},warpBpm:true,transposeKey:false};
let PAL=[],PRESETS={},GROUPS={},GROUPNAMES=[],GROUPCOLORS={},active=null,bank=0,palFor=null,palType="folder",MAC=true,saveT=null,copyTarget="all";
const $=id=>document.getElementById(id), BANKS="ABCDEFGH";
const esc=s=>String(s).replace(/[&<>"]/g,c=>({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;"}[c]));
const jsq=s=>String(s).replace(/\\/g,"\\\\").replace(/'/g,"\\'");
const entryColor=(e,f)=>{const g=entryGroup(e);return(g&&GROUPCOLORS[g])?GROUPCOLORS[g]:f.color};
const css=v=>v?`rgb(${((v>>16)&127)*2},${((v>>8)&127)*2},${(v&127)*2})`:"#3a3d44";
async function api(path,body){const r=await fetch(path,{method:body?"POST":"GET",headers:{"X-Token":TOKEN,"Content-Type":"application/json"},body:body?JSON.stringify(body):undefined});return r.json()}
function save(){clearTimeout(saveT);saveT=setTimeout(()=>api("/api/state",S),300)}
const entryId=e=>typeof e==="string"?e:e.id;
const entryGroup=e=>typeof e==="string"?null:(e.group||null);
function folderPads(id){return Object.keys(S.padmap).filter(p=>(S.padmap[p]||[]).some(e=>entryId(e)===id)).map(Number).sort((a,b)=>a-b)}
function rangeText(a){const o=[];for(let i=0;i<a.length;){let j=i;while(a[j+1]===a[j]+1)j++;o.push(j>i+1?a[i]+"-"+a[j]:a.slice(i,j+1).join(", "));i=j+1}return o.join(", ")}
function dlg(msg,o){o=o||{};return new Promise(res=>{
  const d=$("dlg"),i=$("dlgIn");$("dlgMsg").textContent=msg;i.style.display=o.input?"":"none";i.value=o.def||"";
  $("dlgNo").style.display=o.cancel===false?"none":"";$("dlgOk").textContent=t("dlg_ok");$("dlgNo").textContent=t("dlg_cancel");
  const done=v=>{d.style.display="none";document.removeEventListener("keydown",key,true);res(v)};
  const key=e=>{if(e.key==="Escape"){e.preventDefault();done(o.input?null:false)}else if(e.key==="Enter"){e.preventDefault();done(o.input?i.value:true)}};
  document.addEventListener("keydown",key,true);
  $("dlgOk").onclick=()=>done(o.input?i.value:true);$("dlgNo").onclick=()=>done(o.input?null:false);
  d.style.display="flex";if(o.input){i.focus();i.select()}else $("dlgOk").focus()})}
const uiConfirm=m=>dlg(m),uiPrompt=(m,d)=>dlg(m,{input:true,def:d}),uiAlert=m=>dlg(m,{cancel:false});
async function pathFrom(kind){const r=await api("/api/pick",{kind,lang:LANG});if(r.error==="nodialog"){return(await uiPrompt(t("ask_path")))||null}return r.path}
async function pickTo(key,kind){const p=await pathFrom(kind);if(p){S[key]=p;render();save()}}
async function addPaths(paths,skipEmpty){
  const have=new Set(S.folders.map(f=>f.path));let added=0,first=null;
  for(const p of paths){if(have.has(p))continue;
    const used=new Set(S.folders.map(f=>f.color));
    const c=(PAL.find(x=>!used.has(x.value))||PAL[S.folders.length%PAL.length]).value;
    const f={id:"f"+Date.now()+"_"+added,path:p,color:c,count:0,mute:0,pack:false};await scan(f);
    if(skipEmpty&&f.count<=0)continue;
    S.folders.push(f);have.add(p);added++;first=first||f.id}
  if(first)active=first;render();save();return added}
async function addFolder(){
  const r=await api("/api/pick",{kind:"folders",lang:LANG});
  const ps=r.error==="nodialog"?((await uiPrompt(t("ask_paths")))||"").split(";").map(x=>x.trim()).filter(Boolean):(r.path||[]);
  if(ps.length){const n=await addPaths(ps);$("msg").textContent=t("added",n)}}
async function addSubfolders(){
  const p=await pathFrom("folder");if(!p)return;
  const r=await api("/api/subfolders",{path:p});const n=await addPaths(r.paths,true);
  $("msg").textContent=t("added_sub",n)+(r.paths.length>n?t("skipped",r.paths.length-n):"")}
async function clearFolders(){
  if(!S.folders.length||!(await uiConfirm(t("confirm_clear",S.folders.length))))return;
  S.folders=[];S.padmap={};active=null;$("msg").textContent=t("cleared");render();save()}
async function savePreset(){
  if(!S.folders.length){$("msg").textContent=t("preset_need_folders");return}
  const name=((await uiPrompt(t("preset_name"),S.preset||""))||"").trim().slice(0,60);if(!name)return;
  if(PRESETS[name]&&!(await uiConfirm(t("preset_overwrite",name))))return;
  const preset={folders:S.folders.map(f=>({id:f.id,path:f.path,color:f.color||0,mute:f.mute||0})),padmap:S.padmap,padChoke:S.padChoke};
  const r=await api("/api/presets",{action:"save",name,preset});if(r.error)return;
  PRESETS=r.presets;S.preset=name;$("msg").textContent=t("preset_saved",name);render();save()}
async function loadPreset(){
  const name=$("presetSel").value,p=PRESETS[name];if(!p)return;
  if(S.folders.length&&!(await uiConfirm(t("preset_load_confirm",name))))return;
  S.folders=p.folders.map(f=>({...f,count:0}));S.padmap=JSON.parse(JSON.stringify(p.padmap));S.padChoke={...(p.padChoke||{})};
  active=S.folders.length?S.folders[0].id:null;S.preset=name;
  for(const f of S.folders)await scan(f);
  const missing=S.folders.filter(f=>f.count<0).length;
  $("msg").textContent=t("preset_loaded",name)+(missing?t("preset_missing",missing):"");render();save()}
async function deletePreset(){
  const name=$("presetSel").value;if(!PRESETS[name]||!(await uiConfirm(t("preset_delete_confirm",name))))return;
  const r=await api("/api/presets",{action:"delete",name});if(r.error)return;
  PRESETS=r.presets;if(S.preset===name)S.preset="";render();save()}
async function scan(f){
  try{const r=await api("/api/scan",{path:f.path,recursive:S.recursive||f.pack,pack:!!f.pack});
    f.count=typeof r.count==="number"?r.count:-1;f.keys=r.keys||{};f.groupCounts=r.groupCounts||{}}
  catch(e){f.count=-1;f.keys={};f.groupCounts={}}}
let lastScan=Date.now(),scanning=false;
async function rescanAll(manual){
  if(scanning)return;scanning=true;
  try{for(const f of S.folders)await scan(f);lastScan=Date.now();render();if(manual)$("msg").textContent=t("rescanned",S.folders.length)}
  finally{scanning=false}}
addEventListener("focus",()=>{if(Date.now()-lastScan>15000)rescanAll()});
document.addEventListener("visibilitychange",()=>{if(!document.hidden&&Date.now()-lastScan>15000)rescanAll()});
const KEY_ORDER=["C","C#","D","D#","E","F","F#","G","G#","A","A#","B"];
const keySort=(a,b)=>{const ba=a.replace("m",""),bb=b.replace("m","");return KEY_ORDER.indexOf(ba)-KEY_ORDER.indexOf(bb)||a.length-b.length};
function allKeys(){const s=new Set();for(const f of S.folders)for(const k in (f.keys||{}))s.add(k);return[...s].sort(keySort)}
function keyBadge(f){const ks=Object.entries(f.keys||{});if(!ks.length)return"";
  const total=ks.reduce((s,[,n])=>s+n,0);
  const top=[...ks].sort((a,b)=>b[1]-a[1]).slice(0,4).map(([k])=>k).sort(keySort).join(", ")+(ks.length>4?"…":"");
  return t("key_badge",total,f.count,top)}
function rm(id){S.folders=S.folders.filter(f=>f.id!==id);for(const p in S.padmap){const r=S.padmap[p].filter(e=>entryId(e)!==id);if(r.length)S.padmap[p]=r;else delete S.padmap[p]}if(active===id)active=null;render();save()}
function assign(n,add){const cur=S.padmap[n]||[];
  if(cur.some(e=>entryId(e)===active)){const r=cur.filter(e=>entryId(e)!==active);if(r.length)S.padmap[n]=r;else delete S.padmap[n]}
  else S.padmap[n]=add?[...cur,active]:[active]}
function padClick(n,ev){if(!active){$("msg").textContent=t("pick_first");return}
  assign(n,S.mix!==!!(ev&&ev.shiftKey));$("msg").textContent="";render();save()}
function fillBank(){if(!active)return;for(let i=1;i<=16;i++){const n=bank*16+i,cur=S.padmap[n]||[];
  if(!cur.some(e=>entryId(e)===active))S.padmap[n]=S.mix?[...cur,active]:[active]}render();save()}
async function togglePack(id){const f=S.folders.find(x=>x.id===id);f.pack=!f.pack;await scan(f);render();save()}
function setGroup(n,fid,group){
  S.padmap[n]=(S.padmap[n]||[]).map(e=>entryId(e)===fid?(group?{id:fid,group}:fid):e);
  render();save()}
function setMute(id,v){S.folders.find(f=>f.id===id).mute=+v;render();save()}
function copyBank(){const src=[];for(let i=1;i<=16;i++)src.push(S.padmap[bank*16+i]);
  const ts=copyTarget==="all"?[0,1,2,3,4,5,6,7].filter(b=>b!==bank):[+copyTarget];
  const srcC=[];for(let i=1;i<=16;i++)srcC.push(S.padChoke[bank*16+i]);
  for(const b of ts)for(let i=1;i<=16;i++){const v=src[i-1],n=b*16+i;if(v)S.padmap[n]=[...v];else delete S.padmap[n];if(v&&srcC[i-1]!==undefined)S.padChoke[n]=srcC[i-1];else delete S.padChoke[n]}
  $("msg").textContent=t("copied",BANKS[bank],ts.map(b=>BANKS[b]).join(", "));render();save()}
function clearBank(){for(let i=1;i<=16;i++)delete S.padmap[bank*16+i];render();save()}
function openPal(id){palFor=id;palType="folder";const f=S.folders.find(x=>x.id===id);
  $("sw").innerHTML=PAL.map(c=>`<span class="${c.ok?"":"est"}" title="${esc(colName(c))}" style="background:${css(c.value)};${f.color===c.value?"outline:3px solid #fff":""}" onclick="setColor(${c.value})"></span>`).join("");
  $("palTitle").textContent=t("pal_title");$("btnNocolor").textContent=t("btn_nocolor");
  $("btnImport").style.display="";$("palHint").textContent=t("hint_pal");
  $("pal").style.display="flex"}
function openPalGroup(name){palFor=name;palType="group";const cur=GROUPCOLORS[name]||0;
  $("sw").innerHTML=PAL.map(c=>`<span class="${c.ok?"":"est"}" title="${esc(colName(c))}" style="background:${css(c.value)};${cur===c.value?"outline:3px solid #fff":""}" onclick="setColor(${c.value})"></span>`).join("");
  $("palTitle").textContent=t("pal_title_group",name);$("btnNocolor").textContent=t("btn_usefolder");
  $("btnImport").style.display="none";$("palHint").textContent=t("hint_pal_group");
  $("pal").style.display="flex"}
async function setColor(v){
  if(palType==="group"){const r=await api("/api/group_color",{name:palFor,color:v});if(!r.error)GROUPCOLORS=r.groupColors}
  else{const f=S.folders.find(x=>x.id===palFor);if(f)f.color=v}
  $("pal").style.display="none";render();save()}
async function importColors(){const p=await pathFrom("xpm");if(!p)return;const r=await api("/api/import_colors",{path:p});
  if(r.error){await uiAlert(r.error);return}PAL=r.palette;await uiAlert(t("found",r.found));openPal(palFor)}
function applyTheme(){const th=["dark","light","mpc"].includes(S.theme)?S.theme:(matchMedia&&matchMedia("(prefers-color-scheme: light)").matches?"light":"dark");
  document.documentElement.dataset.theme=th;$("theme").value=th}
const lumOf=v=>{const r=((v>>16)&127)*2,g=((v>>8)&127)*2,b=(v&127)*2;return(.299*r+.587*g+.114*b)/255};
$("theme").onchange=e=>{S.theme=e.target.value;applyTheme();save()};
let KW=[],kwT=null,kwSeq=0;
async function kwOpen(){
  const r=await api("/api/groups",{action:"get"});if(r.error)return;
  KW=Object.entries(r.groups).map(([n,k])=>({id:++kwSeq,orig:n,name:n,kw:k.join(", ")}));
  $("kwMsg").textContent="";
  const packs=S.folders.filter(f=>f.pack&&f.count>0);
  $("kwTest").innerHTML=packs.length?packs.map(f=>`<option value="${esc(f.id)}">${esc(f.path.split(/[\\/]/).pop()||f.path)}</option>`).join(""):`<option value="">${esc(t("kw_test_none"))}</option>`;
  $("kwTest").disabled=!packs.length;
  kwRender();$("kw").style.display="flex";kwCounts()}
function kwClose(){$("kw").style.display="none"}
function kwRender(){
  $("kwRows").innerHTML=`<div class="kwr hd"><span></span><span>${esc(t("kw_col_name"))}</span><span>${esc(t("kw_col_words"))}</span><span></span><span></span></div>`+
   KW.map((g,i)=>`<div class="kwr" data-id="${g.id}">
    <span class="grip" tabindex="0" title="${esc(t("kw_drag"))}" onpointerdown="kwDrag(event,${g.id})" onkeydown="kwKey(event,${i})">⠿</span>
    <input type="text" value="${esc(g.name)}" placeholder="${esc(t("kw_name_ph"))}" oninput="kwEdit(${g.id},'name',this.value)">
    <input type="text" value="${esc(g.kw)}" placeholder="${esc(t("kw_words_ph"))}" oninput="kwEdit(${g.id},'kw',this.value)">
    <span class="cnt" id="kc${g.id}"></span>
    <button title="${esc(t("kw_del"))}" onclick="kwDel(${g.id})">✕</button></div>`).join("")}
function kwEdit(id,k,v){const g=KW.find(x=>x.id===id);g[k]=v;clearTimeout(kwT);kwT=setTimeout(kwCounts,500)}
function kwDrag(e,id){
  if(e.button!==undefined&&e.button!==0)return;e.preventDefault();
  const box=$("kwRows"),row=box.querySelector(`.kwr[data-id="${id}"]`);row.classList.add("dragging");
  let y=e.clientY,raf=0;
  const place=()=>{const others=[...box.querySelectorAll(".kwr:not(.hd):not(.dragging)")];
    const next=others.find(r=>{const b=r.getBoundingClientRect();return y<b.top+b.height/2});
    if(next)box.insertBefore(row,next);else box.appendChild(row)};
  const tick=()=>{const b=box.getBoundingClientRect();     // scroll the list while dragging near its edges
    if(y<b.top+30)box.scrollTop-=12;else if(y>b.bottom-30)box.scrollTop+=12;place();raf=requestAnimationFrame(tick)};
  const mv=ev=>{y=ev.clientY;place()};
  const up=()=>{removeEventListener("pointermove",mv);removeEventListener("pointerup",up);removeEventListener("pointercancel",up);
    cancelAnimationFrame(raf);row.classList.remove("dragging");
    const order=[...box.querySelectorAll(".kwr:not(.hd)")].map(r=>+r.dataset.id);
    KW.sort((a,b)=>order.indexOf(a.id)-order.indexOf(b.id));kwRender();kwCounts()};
  addEventListener("pointermove",mv);addEventListener("pointerup",up);addEventListener("pointercancel",up);raf=requestAnimationFrame(tick)}
function kwKey(e,i){if(!e.altKey&&!e.shiftKey)return;if(e.key==="ArrowUp"||e.key==="ArrowDown"){e.preventDefault();kwMove(i,e.key==="ArrowUp"?-1:1);
  const r=$("kwRows").querySelectorAll(".kwr:not(.hd) .grip")[Math.max(0,Math.min(KW.length-1,i+(e.key==="ArrowUp"?-1:1)))];if(r)r.focus()}}
function kwMove(i,d){const j=i+d;if(j<0||j>=KW.length)return;[KW[i],KW[j]]=[KW[j],KW[i]];kwRender();kwCounts()}
function kwDel(id){KW=KW.filter(g=>g.id!==id);kwRender();kwCounts()}
function kwAdd(){KW.push({id:++kwSeq,orig:"",name:"",kw:""});kwRender();kwCounts();
  const rows=$("kwRows");rows.scrollTop=1e9;const ins=rows.querySelectorAll(".kwr:last-child input");if(ins[0])ins[0].focus()}
const kwWords=g=>g.kw.split(",").map(x=>x.trim()).filter(Boolean);
async function kwCounts(){
  const f=S.folders.find(x=>x.id===$("kwTest").value);
  for(const g of KW){const e=$("kc"+g.id);if(e){e.textContent="";e.className="cnt"}}
  if(!f)return;
  const groups={};KW.forEach(g=>{if(g.name.trim()&&kwWords(g).length)groups[g.name.trim()]=kwWords(g)});
  const r=await api("/api/scan",{path:f.path,recursive:true,pack:true,groups});
  for(const g of KW){const n=(r.groupCounts||{})[g.name.trim()],e=$("kc"+g.id);if(e&&n!==undefined){e.textContent=n;e.className="cnt"+(n===0?" zero":"")}}}
$("kwTest").onchange=kwCounts;
async function kwSave(){
  const items=KW.filter(g=>g.name.trim()||g.kw.trim());
  const rows=[...$("kwRows").querySelectorAll(".kwr:not(.hd)")];
  rows.forEach(r=>r.querySelectorAll("input").forEach(i=>i.classList.remove("bad")));
  const names=items.map(g=>g.name.trim().toLowerCase());
  const bad=(g,why)=>{const r=$("kwRows").querySelector(`.kwr[data-id="${g.id}"]`);if(r)r.querySelectorAll("input")[why==="name"?0:1].classList.add("bad");return why};
  for(const g of items){
    if(!g.name.trim()||g.name.includes(":")||g.name.trim().startsWith("#")){bad(g,"name");$("kwMsg").textContent=t("err_bad_name");return}
    if(names.indexOf(g.name.trim().toLowerCase())!==names.lastIndexOf(g.name.trim().toLowerCase())){bad(g,"name");$("kwMsg").textContent=t("err_dup_name");return}
    if(!kwWords(g).length){bad(g,"kw");$("kwMsg").textContent=t("err_no_keywords");return}}
  if(!items.length){$("kwMsg").textContent=t("err_no_groups");return}
  $("kwMsg").textContent="";
  const renames={};items.forEach(g=>{if(g.orig&&g.orig!==g.name.trim())renames[g.orig]=g.name.trim()});
  const newNames=new Set(items.map(g=>g.name.trim()));
  // pad entries that point at a group that will no longer exist (after applying renames)
  const mapName=n=>renames[n]||n;
  let orphan=0;for(const p in S.padmap)for(const e of S.padmap[p]){const g=entryGroup(e);if(g&&!newNames.has(mapName(g)))orphan++}
  if(orphan&&!(await uiConfirm(t("kw_used_confirm",orphan))))return;
  const r=await api("/api/groups",{action:"save",groups:items.map(g=>({name:g.name.trim(),keywords:kwWords(g)})),renames});
  if(r.error){$("kwMsg").textContent=t(`err_${r.error}`);return}
  applyGroups(r,renames);kwClose();$("msg").textContent=t("kw_saved",GROUPNAMES.length);rescanAll()}
function applyGroups(r,renames){
  GROUPS=r.groups;GROUPNAMES=Object.keys(GROUPS);GROUPCOLORS=r.groupColors||{};
  for(const p in S.padmap)S.padmap[p]=S.padmap[p].map(e=>{const g=entryGroup(e);if(!g)return e;
    const n=(renames&&renames[g])||g;return GROUPS[n]?{id:entryId(e),group:n}:entryId(e)});
  render();save()}
async function kwReset(){
  if(!(await uiConfirm(t("kw_reset_confirm"))))return;
  const r=await api("/api/groups",{action:"reset"});if(r.error)return;
  applyGroups(r,{});KW=Object.entries(r.groups).map(([n,k])=>({id:++kwSeq,orig:n,name:n,kw:k.join(", ")}));kwRender();kwCounts();rescanAll()}
addEventListener("keydown",e=>{if(e.key==="Escape"&&$("chk").style.display==="flex")$("chk").style.display="none";if(e.key==="Escape"&&$("opt").style.display==="flex"&&$("dlg").style.display!=="flex")closeOpts();if(e.key==="Escape"&&$("kw").style.display==="flex"&&$("dlg").style.display!=="flex")kwClose()});
async function editGroupsFile(){const r=await api("/api/edit_groups",{});if(r.error)await uiAlert(r.error)}
function folderChoke(n){const e=(S.padmap[n]||[])[0],f=e&&S.folders.find(x=>x.id===entryId(e));return f?(f.mute||0):0}
const padChokeOf=n=>S.padChoke[n]!==undefined?S.padChoke[n]:folderChoke(n);
function openChoke(n){const cur=S.padChoke[n];
  $("chkTitle").textContent=t("choke_title",n);
  $("chkGrid").innerHTML=`<button class="wide ${cur===undefined?"sel":""}" onclick="setPadChoke(${n},null)">${esc(t("choke_follow",folderChoke(n)||t("choke_none")))}</button><button class="wide ${cur===0?"sel":""}" onclick="setPadChoke(${n},0)">${esc(t("choke_none"))}</button>`+
   Array.from({length:32},(_,i)=>`<button class="${cur===i+1?"sel":""}" title="${esc(t("choke_n",i+1))}" onclick="setPadChoke(${n},${i+1})">${i+1}</button>`).join("");
  $("chk").style.display="flex"}
function setPadChoke(n,v){if(v===null)delete S.padChoke[n];else S.padChoke[n]=v;$("chk").style.display="none";render();save()}
function openOpts(){$("opt").style.display="flex"}
function closeOpts(){$("opt").style.display="none"}
function optSummary(){const o=[];if(S.warpBpm)o.push(t("opt_warp"));
  if(S.matchKey){o.push(t("opt_key",S.matchKey==="random"?t("key_random").toLowerCase():S.matchKey));if(S.transposeKey)o.push(t("opt_transpose"))}
  if(S.flatOutput)o.push(t("opt_flat"));if(S.hardlink)o.push(t("opt_link"));if(S.padNumbering)o.push(t("opt_padnum"));
  if(!S.recursive)o.push(t("opt_norec"));if(!S.clear)o.push(t("opt_keep"));return o.join(" · ")||t("opt_sum_none")}
function render(){
  $("optSum").textContent=optSummary();
  if(!S.padChoke||typeof S.padChoke!=="object")S.padChoke={};
  for(const k in S.padChoke)if(!(S.padmap[k]||[]).length)delete S.padChoke[k];
  $("lang").value=LANG;$("btnClear").disabled=$("btnRescan").disabled=!S.folders.length;
  $("groupsInfo").textContent=t("groups_count",GROUPNAMES.length);
  const pn=Object.keys(PRESETS).sort((a,b)=>a.localeCompare(b));
  $("presetSel").innerHTML=pn.length?`<option value="">${esc(t("preset_choose"))}</option>`+pn.map(n=>`<option value="${esc(n)}" ${n===S.preset?"selected":""}>${esc(n)}</option>`).join(""):`<option value="">${esc(t("preset_none"))}</option>`;
  $("btnPload").disabled=$("btnPdel").disabled=!pn.length;$("btnPsave").disabled=!S.folders.length;
  $("folders").innerHTML=S.folders.map(f=>{const pd=folderPads(f.id);
   return `<div class="card ${f.id===active?"on":""}" onclick="active='${f.id}';render()">
    <span class="dot" style="background:${css(f.color)}" title="${esc(t("chg_color"))}" onclick="event.stopPropagation();openPal('${f.id}')"></span>
    <div class="fi"><b>${esc(f.path.split(/[\\/]/).pop()||f.path)}</b><small>${f.count<0?t("not_found"):t("n_samples",f.count)} · ${t("pads_lbl")}: ${pd.length?rangeText(pd):t("none")}</small>${keyBadge(f)?`<small>${esc(keyBadge(f))}</small>`:""}<small>${esc(f.path)}</small></div>
    <label class="packchk" onclick="event.stopPropagation()" title="${esc(t("pack_toggle_title"))}"><input type="checkbox" ${f.pack?"checked":""} onchange="togglePack('${f.id}')"> ${esc(t("pack_toggle"))}</label>
    <select title="${esc(t("mute_grp"))}" onclick="event.stopPropagation()" onchange="setMute('${f.id}',this.value)">${`<option value="0">${t("nochoke")}</option>`+Array.from({length:32},(_,i)=>`<option value="${i+1}" ${f.mute===i+1?"selected":""}>${t("choke",i+1)}</option>`).join("")}</select>
    <button onclick="event.stopPropagation();rm('${f.id}')">✕</button></div>`}).join("")||`<div class="hint">${t("no_folders")}</div>`;
  $("tabs").innerHTML=[...BANKS].map((b,i)=>`<button class="${i===bank?"sel":""}" onclick="bank=${i};render()">${b}</button>`).join("");
  $("bname").textContent=t("bank",BANKS[bank]);
  const groupOpts=(sel,f)=>`<option value="">${esc(t("kw_whole"))}</option>`+GROUPNAMES.map(g=>{const n=f&&f.groupCounts?f.groupCounts[g]:undefined;
    const lbl=n===undefined?g:`${g} (${n})${n===0?" ⚠":""}`;return `<option value="${esc(g)}" ${sel===g?"selected":""}>${esc(lbl)}</option>`}).join("");
  let h="";for(let r=3;r>=0;r--)for(let c=0;c<4;c++){const n=bank*16+r*4+c+1,entries=S.padmap[n]||[],
   pairs=entries.map(e=>({e,f:S.folders.find(x=>x.id===entryId(e))})).filter(x=>x.f),f=pairs[0]&&pairs[0].f;
   const bg=!f?"":pairs.length===1?css(entryColor(pairs[0].e,pairs[0].f)):`linear-gradient(135deg,${pairs.map((x,k)=>`${css(entryColor(x.e,x.f))} ${k*100/pairs.length}% ${(k+1)*100/pairs.length}%`).join(",")})`;
   const titleTxt=pairs.map(({e,f})=>f.path.split(/[\\/]/).pop()+(f.pack?` (🔑 ${entryGroup(e)||t("kw_whole")})`:"")).join(" + ");
   const body=pairs.map(({e,f})=>{
     if(!f.pack)return `<small>${esc(f.path.split(/[\\/]/).pop())}</small>`;
     return `<div class="padkw"><select onclick="event.stopPropagation()" onchange="setGroup(${n},'${f.id}',this.value)">${groupOpts(entryGroup(e),f)}</select></div>`}).join("");
   const dots=pairs.filter(({e,f})=>f.pack&&entryGroup(e)).map(({e,f})=>{const g=entryGroup(e);
     return `<span class="padcolor" style="background:${css(entryColor(e,f))}" onclick="event.stopPropagation();openPalGroup('${jsq(g)}')" title="${esc(t('group_color_title',g))}"></span>`}).join("");
   const lt=f&&(pairs.reduce((a,x)=>a+lumOf(entryColor(x.e,x.f)),0)/pairs.length)>.62;
   const eff=f?padChokeOf(n):0,chip=f&&(pairs.some(x=>x.f.pack)||eff>0||S.padChoke[n]!==undefined)?`<button class="padchoke${S.padChoke[n]===undefined?" dim":""}" title="${esc(t("choke_title",n))}" onclick="event.stopPropagation();openChoke(${n})">C${eff||"–"}</button>`:"";
   h+=`<div class="pad${f?" on":""}${lt?" lt":""}" title="${esc(titleTxt)}" style="${f?`background:${bg}`:""}" onclick="padClick(${n},event)"><span class="n">${n}</span>${body}${dots?`<div class="padtools">${dots}</div>`:""}${chip?`<div class="padchokewrap">${chip}</div>`:""}</div>`}
  $("pads").innerHTML=h;
  const ct=[...Array(8).keys()].filter(b=>b!==bank);if(copyTarget!=="all"&&+copyTarget===bank)copyTarget="all";
  $("copyTo").innerHTML=`<option value="all">${t("opt_all")}</option>`+ct.map(b=>`<option value="${b}">${t("bank",BANKS[b])}</option>`).join("");$("copyTo").value=copyTarget;
  $("tpl").textContent=S.template||t("tpl_builtin");$("out").textContent=S.output||t("not_chosen");
  for(const k of["kits","name","seed"])if(document.activeElement!==$(k))$(k).value=S[k];
  for(const k of["recursive","clear","hardlink","mix","padNumbering","flatOutput","warpBpm","transposeKey"])$(k).checked=S[k];
  $("transposeRow").style.display=S.matchKey?"flex":"none";
  const ks=allKeys(),hasKeys=ks.length>0;
  $("matchKeyOn").checked=!!S.matchKey;$("matchKeyOn").disabled=!hasKeys&&!S.matchKey;
  $("matchKeyRow").style.display=S.matchKey?"flex":"none";
  $("matchKeySel").innerHTML=`<option value="random">${esc(t("key_random"))}</option>`+ks.map(k=>`<option value="${k}" ${S.matchKey===k?"selected":""}>${k}</option>`).join("");
  if(S.matchKey)$("matchKeySel").value=ks.includes(S.matchKey)?S.matchKey:"random";
  $("matchKeyHint").textContent=hasKeys?t("hint_matchkey"):t("hint_matchkey_off")}
for(const k of["kits","name","seed"])$(k).oninput=e=>{S[k]=e.target.value;save()};
for(const k of["recursive","clear","hardlink","mix","padNumbering","flatOutput","warpBpm","transposeKey"])$(k).onchange=async e=>{S[k]=e.target.checked;if(k==="recursive"){for(const f of S.folders)await scan(f);render()}save()};
$("matchKeyOn").onchange=e=>{S.matchKey=e.target.checked?"random":"";render();save()};
$("matchKeySel").onchange=e=>{S.matchKey=e.target.value;save()};
$("lang").onchange=e=>{LANG=S.lang=e.target.value;$("msg").textContent="";applyStatic();render();save()};
async function generate(){
  const m=$("msg");m.className="";
  if(!S.output)return m.textContent=t("need_out");
  if(!Object.keys(S.padmap).length)return m.textContent=t("need_pads");
  $("go").disabled=true;m.textContent=t("generating");$("log").style.display="block";rescanAll();
  const r=await api("/api/generate",S);if(r.error){m.textContent=r.error==="busy"?t("busy"):r.error;$("go").disabled=false;return}
  const iv=setInterval(async()=>{const j=await api("/api/job");$("log").textContent=j.log.join("\n");$("log").scrollTop=1e9;
   if(j.done){clearInterval(iv);$("go").disabled=false;if(j.error){m.className="err";m.textContent=j.error}else m.textContent=t("done")}},400)}
setInterval(()=>api("/api/ping"),5000);
addEventListener("pagehide",()=>navigator.sendBeacon("/api/bye?t="+TOKEN));
(async()=>{const r=await api("/api/state");PAL=r.palette;PRESETS=r.presets||{};GROUPS=r.keywordGroups||{};GROUPNAMES=Object.keys(GROUPS);GROUPCOLORS=r.groupColors||{};MAC=r.mac;if(r.state)Object.assign(S,r.state);for(const p in S.padmap)if(!Array.isArray(S.padmap[p]))S.padmap[p]=[S.padmap[p]];
  for(const f of S.folders)if(f.pack===undefined)f.pack=false;
  LANG=S.lang&&I18N[S.lang]?S.lang:((navigator.language||"en").toLowerCase().startsWith("es")?"es":"en");S.lang=LANG;
  active=S.folders.length?S.folders[0].id:null;applyTheme();applyStatic();render();
  for(const f of S.folders)await scan(f);lastScan=Date.now();render()})();
</script></html>'''


def watchdog():
    """With --auto-quit: closes the server when the tab is closed (or after 3 min of inactivity)."""
    while True:
        time.sleep(1)
        if not JOB["done"]:
            continue
        now = time.time()
        closed = SEEN["bye"] and now - SEEN["bye"] > 6 and SEEN["last"] <= SEEN["bye"] + 0.5
        idle = now - SEEN["last"] > 180
        if closed or idle:
            print(srv("closing"))
            os._exit(0)


def start_server():
    """Starts the local HTTP server on the first free port and returns (server, url)."""
    for port in range(8765, 8785):
        try:
            server = ThreadingHTTPServer(("127.0.0.1", port), H)
            return server, f"http://127.0.0.1:{server.server_address[1]}/"
        except OSError:
            continue
    sys.exit(srv("ports"))


def main():
    fresh_session()
    server, url = start_server()
    print(srv("up", url=url))
    threading.Timer(0.5, lambda: webbrowser.open(url)).start()
    if AUTOQUIT:
        threading.Thread(target=watchdog, daemon=True).start()
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print(srv("bye"))


if __name__ == "__main__":
    main()
