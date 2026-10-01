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

# MPC pad colors: (R<<16)|(G<<8)|B, channels 0-127. Names are keys translated in the page.
# "ok": True = value seen in a real .xpm; False = estimated (calibrate with "Import colors").
DEFAULT_PALETTE = [
    ("red", 0x7F0000, True), ("orange", 0x7F3300, True), ("yellow", 0x7F7F00, True),
    ("green", 0x007F00, True), ("petrol", 0x114F68, True),
    ("lime", 0x3F7F00, False), ("turquoise", 0x007F5F, False), ("cyan", 0x007F7F, False),
    ("blue", 0x00007F, False), ("violet", 0x3F007F, False), ("magenta", 0x7F007F, False),
    ("pink", 0x7F2A55, False), ("white", 0x7F7F7F, False), ("grey", 0x3F3F3F, False),
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


def clean_preset(p):
    """Keeps only what a preset stores: folders (path, color, mute group) and pad assignments."""
    folders = [{"id": str(f["id"]), "path": str(f["path"]), "color": int(f.get("color") or 0),
                "mute": int(f.get("mute") or 0)} for f in p["folders"]]
    padmap = {str(k): [str(i) for i in (v if isinstance(v, list) else [v])] for k, v in p["padmap"].items()}
    return {"folders": folders, "padmap": padmap}


def palette():
    pal = [{"name": n, "value": v, "ok": ok} for n, v, ok in DEFAULT_PALETTE]
    try:
        for e in json.loads(PAL_FILE.read_text()):
            if all(p["value"] != e["value"] for p in pal):
                pal.append(e)
    except (OSError, ValueError):
        pass
    return pal


def pick(kind, lang="en"):
    """Native macOS dialog. Returns (path or list of paths, error)."""
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
        pads, colors, mutes = {}, {}, {}
        for pad, ids in st["padmap"].items():
            ids = ids if isinstance(ids, list) else [ids]
            fs = [folders[i] for i in ids if i in folders]
            if fs:   # the pad's color and mute group are those of its first folder
                pads[int(pad)] = [Path(f["path"]).expanduser() for f in fs]
                colors[int(pad)] = int(fs[0].get("color") or 0)
                mutes[int(pad)] = int(fs[0].get("mute") or 0)
        if not pads:
            raise ValueError(kc.msg("no_pads"))
        seed = int(st["seed"]) if str(st.get("seed", "")).strip() else None
        match_key = st.get("matchKey") or None      # "" (off), "random", or a canonical key
        kc.generate_kits(template, st["output"], int(st["kits"]), st["name"] or "Kit {n:03d}", pads,
                         colors=colors, clear_others=bool(st["clear"]), recursive=bool(st["recursive"]),
                         hardlink=bool(st["hardlink"]), seed=seed, log=log, mutegroups=mutes,
                         match_key=match_key)
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
        if self.path == "/api/subfolders":
            d = Path(data["path"]).expanduser()
            subs = sorted((str(x) for x in d.iterdir() if x.is_dir() and not x.name.startswith(".")),
                          key=str.lower) if d.is_dir() else []
            return self.send({"paths": subs})
        if self.path == "/api/scan":
            d = Path(data["path"]).expanduser()
            files = kc.scan_folder(d, data.get("recursive", True)) if d.is_dir() else []
            keys = {k: len(v) for k, v in kc.index_by_key(files).items()} if files else {}
            return self.send({"count": len(files) if d.is_dir() else -1, "keys": keys})
        if self.path == "/api/import_colors":
            try:
                vals = kc.read_pad_colors(Path(data["path"]).expanduser().read_text(encoding="utf-8"))
            except OSError as e:
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
:root{--bg:#1b1c1f;--pn:#26282c;--ln:#383b41;--tx:#e6e7ea;--mu:#9a9fa8;--ac:#4c8dff}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--tx);font:14px -apple-system,system-ui,sans-serif}
header{padding:14px 20px;border-bottom:1px solid var(--ln);font-weight:600;font-size:16px;display:flex;justify-content:space-between;align-items:center;gap:12px}
header select{font-weight:400}
.brand{letter-spacing:.02em}.sub{color:var(--mu);font-weight:400;font-size:13px;margin-left:6px}
footer{max-width:1200px;margin:0 auto;padding:4px 20px 22px;color:var(--mu);font-size:12px}
main{display:grid;grid-template-columns:minmax(320px,1fr) 440px;gap:16px;padding:16px 20px;max-width:1200px;margin:auto}
@media(max-width:900px){main{grid-template-columns:1fr}}
section{background:var(--pn);border:1px solid var(--ln);border-radius:10px;padding:14px;margin-bottom:16px}
h2{margin:0 0 10px;font-size:12px;letter-spacing:.06em;text-transform:uppercase;color:var(--mu)}
button{background:#33363c;color:var(--tx);border:1px solid var(--ln);border-radius:6px;padding:6px 10px;cursor:pointer;font:inherit}
button:hover{background:#3d4148}button.pri{background:var(--ac);border-color:var(--ac);color:#fff;font-weight:600;padding:9px 16px}
button:disabled{opacity:.5}input[type=text],input[type=number]{background:#17181b;color:var(--tx);border:1px solid var(--ln);border-radius:6px;padding:6px 8px;font:inherit;width:100%}
.row{display:flex;gap:8px;align-items:center;margin-bottom:8px}.row label{width:90px;color:var(--mu);flex:none}
.path{flex:1;min-width:0;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;color:var(--mu);direction:rtl;text-align:left}
.card{display:flex;gap:10px;align-items:center;border:2px solid var(--ln);border-radius:8px;padding:8px 10px;margin-bottom:8px;cursor:pointer;background:#202226}
.card.on{border-color:var(--ac);background:#1f2a3f}.card{min-width:0}
.fi{flex:1;min-width:0}.fi b{display:block}
.fi small{color:var(--mu);display:block;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.dot{width:26px;height:26px;border-radius:50%;border:2px solid #fff3;flex:none;cursor:pointer}
.tabs{display:flex;gap:4px;margin-bottom:10px}.tabs button{flex:1;padding:5px 0}.tabs .sel{background:var(--ac);border-color:var(--ac)}
.grid{display:grid;grid-template-columns:repeat(4,1fr);gap:8px}
.pad{aspect-ratio:1;min-width:0;min-height:0;overflow:hidden;border-radius:8px;border:1px solid #0006;display:flex;flex-direction:column;align-items:center;justify-content:center;background:#2e3035;cursor:pointer;color:#fffc;font-weight:600;user-select:none}
.pad small{width:100%;box-sizing:border-box;padding:0 4px;font-weight:400;font-size:10px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;text-align:center}
.pad:hover{outline:2px solid #fff6}.hint{color:var(--mu);font-size:12px;margin-top:8px}
.chk{display:flex;gap:6px;align-items:center;margin:4px 0;color:var(--mu)}
#pal{position:fixed;inset:0;background:#000a;display:none;align-items:center;justify-content:center;z-index:9}
#pal>div{background:var(--pn);border:1px solid var(--ln);border-radius:10px;padding:16px;width:340px}
.sw{display:grid;grid-template-columns:repeat(5,1fr);gap:8px;margin:12px 0}
.sw span{aspect-ratio:1;border-radius:8px;cursor:pointer;border:2px solid #fff2;position:relative}
.sw span.est::after{content:"?";position:absolute;right:3px;top:0;font-size:11px;color:#fffb}
pre{background:#131416;border-radius:6px;padding:10px;max-height:220px;overflow:auto;margin:10px 0 0;font-size:12px;white-space:pre-wrap}
.err{color:#ff7b72}
.card select{width:auto;background:#17181b;color:var(--tx);border:1px solid var(--ln);border-radius:6px;padding:3px 4px;font:inherit;font-size:12px}
select.big,header select{background:#17181b;color:var(--tx);border:1px solid var(--ln);border-radius:6px;padding:5px;font:inherit}
</style>
<header><span><b class="brand">Wavethings</b> Kit Creator <span class="sub" data-i18n="hdr_sub"></span></span><select id="lang"><option value="en">English</option><option value="es">Español</option></select></header>
<main>
<div>
 <section><h2 data-i18n="sec_presets"></h2>
  <div class="row" style="margin:0"><select class="big" id="presetSel" style="flex:1;min-width:0"></select><button id="btnPload" onclick="loadPreset()" data-i18n="btn_pload"></button><button id="btnPsave" onclick="savePreset()" data-i18n="btn_psave"></button><button id="btnPdel" onclick="deletePreset()" data-i18n="btn_pdel"></button></div>
  <div class="hint" data-i18n="hint_presets"></div>
 </section>
 <section><h2 data-i18n="sec_folders"></h2>
  <div id="folders"></div>
  <div class="row" style="margin:0"><button onclick="addFolder()" data-i18n="btn_add"></button><button onclick="addSubfolders()" data-i18n="btn_addsub" data-title="title_addsub"></button><button id="btnClear" onclick="clearFolders()" style="margin-left:auto" data-i18n="btn_clear" data-title="title_clear"></button></div>
  <div class="hint" data-i18n="hint_folders"></div>
 </section>
 <section><h2 data-i18n="sec_output"></h2>
  <div class="row"><label data-i18n="lbl_template"></label><button onclick="pickTo('template','xpm')" data-i18n="btn_othertpl"></button><button onclick="S.template='';render();save()" data-i18n="btn_builtin" data-title="title_builtin"></button><span class="path" id="tpl"></span></div>
  <div class="row"><label data-i18n="lbl_folder"></label><button onclick="pickTo('output','out')" data-i18n="btn_choose"></button><span class="path" id="out"></span></div>
  <div class="row"><label data-i18n="lbl_kits"></label><input type="number" min="1" id="kits" style="width:90px">
   <label style="width:auto;margin-left:10px" data-i18n="lbl_name"></label><input type="text" id="name"></div>
  <div class="row"><label data-i18n="lbl_seed"></label><input type="text" id="seed" data-ph="ph_seed"></div>
  <label class="chk"><input type="checkbox" id="recursive"><span data-i18n="chk_recursive"></span></label>
  <label class="chk"><input type="checkbox" id="clear"><span data-i18n="chk_clear"></span></label>
  <label class="chk"><input type="checkbox" id="hardlink"><span data-i18n="chk_hardlink"></span></label>
  <label class="chk"><input type="checkbox" id="matchKeyOn"><span data-i18n="chk_matchkey"></span></label>
  <div class="row" id="matchKeyRow" style="display:none;margin:0 0 8px"><label data-i18n="lbl_matchkey"></label><select class="big" id="matchKeySel" style="flex:1"></select></div>
  <div class="hint" id="matchKeyHint" data-i18n="hint_matchkey"></div>
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
<div id="pal" onclick="if(event.target===this)this.style.display='none'"><div>
 <b data-i18n="pal_title"></b><div class="sw" id="sw"></div>
 <div class="row"><button onclick="setColor(0)" data-i18n="btn_nocolor"></button><button onclick="importColors()" data-i18n="btn_import"></button></div>
 <div class="hint" data-i18n="hint_pal"></div>
</div></div>
<script>
const TOKEN="__TOKEN__";
const I18N={
en:{sec_presets:"Presets",btn_pload:"Load",btn_psave:"Save…",btn_pdel:"Delete",preset_none:"(no presets yet)",preset_choose:"Choose a preset…",
 hint_presets:"A preset remembers your folders, pad assignments, choke groups and colors. Folder paths are saved as they are on this computer.",
 preset_name:"Preset name:",preset_overwrite:"A preset called “{0}” already exists. Overwrite it?",preset_saved:"Preset “{0}” saved",
 preset_load_confirm:"Replace the current folders and pad assignments with preset “{0}”?",preset_loaded:"Preset “{0}” loaded",
 preset_missing:" · {0} folder(s) not found (is a drive disconnected?)",preset_delete_confirm:"Delete preset “{0}”?",preset_need_folders:"Add at least one folder first",
 hdr_sub:"· .xpm drum kit generator for MPC",footer:"© 2026 Wavethings · Open source (MIT License)",sec_folders:"Sample folders",btn_add:"＋ Add folders",btn_addsub:"＋ Add subfolders of…",btn_clear:"Clear all",title_clear:"Remove every folder and its pad assignments",confirm_clear:"Remove all {0} folders and their pad assignments?",cleared:"All folders removed",
 title_addsub:"Adds each subfolder of a parent folder as a separate folder",
 hint_folders:"1) Add folders (⌘-click to pick several, or a parent folder to add all its subfolders) · 2) select one · 3) click the pads on the right to assign it. The folder's color is applied to its pads on the MPC. “Choke” = mute group: pads of folders with the same number cut each other off (e.g. closed and open hi-hat).",
 sec_output:"Output",lbl_template:"Template",btn_othertpl:"Use another .xpm…",btn_builtin:"Built-in",title_builtin:"Back to the built-in template",
 lbl_folder:"Folder",btn_choose:"Choose…",lbl_kits:"No. of kits",lbl_name:"Name",lbl_seed:"Seed",ph_seed:"(optional) same seed = same kits",
 chk_recursive:"Include subfolders",chk_clear:"Empty and uncolor unassigned pads",chk_hardlink:"Link samples instead of copying (saves space)",
 chk_matchkey:"Filter by key",lbl_matchkey:"Key",key_random:"Random per kit",
 hint_matchkey:"Reads the root note from file names (e.g. \"Bass_C1\", \"Synth_Dm\", \"Lead - F#3\") and, when a folder has that information, keeps every kit's samples in one key. Folders without a detected key (drums, for example) are never filtered. Detection is best-effort and may miss or misread some names; add {key} to the name pattern above to show each kit's key. Filterable folders show a 🎵 badge.",
 hint_matchkey_off:"No folder has a detected key yet — add \"C1\", \"Dm\", \"F#\" and the like to your file names to use this filter.",
 key_badge:"🎵 {0}/{1} tagged · {2}",
 btn_go:"Generate kits",sec_pads:"Pads",bank:"Bank {0}",
 chk_mix:"Mix: clicking a pad adds the active folder (instead of replacing). Shift+click does the opposite.",
 btn_fill:"Whole bank → active folder",btn_clearbank:"Clear bank",lbl_copy:"Copy this bank to",btn_copy:"Copy",opt_all:"all the others",
 hint_pads:"Layout matches the MPC: pad 1 is bottom-left. Pads with several folders show diagonal stripes: for each kit one folder is picked at random (equal probability for all) and then a sample from it. The pad's color and choke come from its first folder.",
 pal_title:"Folder color",btn_nocolor:"No color",btn_import:"Import colors from an .xpm",
 hint_pal:"Swatches marked “?” are estimated values. If the color looks different on your MPC, import an .xpm where you painted pads with the MPC's colors and they will be added with their exact values.",
 not_found:"⚠ folder not found",n_samples:"{0} samples",pads_lbl:"pads",none:"none",chg_color:"Change color",mute_grp:"Mute group",
 nochoke:"No choke",choke:"Choke {0}",no_folders:"No folders yet.",tpl_builtin:"Built-in (128 empty pads)",not_chosen:"(not chosen)",
 pick_first:"Select a folder first",added:"{0} folder(s) added",added_sub:"{0} subfolder(s) added",skipped:" ({0} skipped: empty or already added)",
 copied:"Bank {0} copied to {1}",need_out:"Missing output folder",need_pads:"Assign a folder to at least one pad",generating:"Generating…",done:"✔ Done",
 busy:"A generation is already running",found:"{0} colors found in the file.",ask_path:"Full path:",ask_paths:"Folder paths separated by ;",
 "col.red":"Red","col.orange":"Orange","col.yellow":"Yellow","col.green":"Green","col.petrol":"Petrol blue","col.lime":"Lime","col.turquoise":"Turquoise","col.cyan":"Cyan","col.blue":"Blue","col.violet":"Violet","col.magenta":"Magenta","col.pink":"Pink","col.white":"White","col.grey":"Grey","col.imported":"Imported"},
es:{sec_presets:"Presets",btn_pload:"Cargar",btn_psave:"Guardar…",btn_pdel:"Borrar",preset_none:"(aún no hay presets)",preset_choose:"Elige un preset…",
 hint_presets:"Un preset recuerda tus carpetas, asignaciones de pads, grupos de choque y colores. Las rutas se guardan tal como están en este equipo.",
 preset_name:"Nombre del preset:",preset_overwrite:"Ya existe un preset llamado «{0}». ¿Sobrescribirlo?",preset_saved:"Preset «{0}» guardado",
 preset_load_confirm:"¿Reemplazar las carpetas y asignaciones actuales por el preset «{0}»?",preset_loaded:"Preset «{0}» cargado",
 preset_missing:" · {0} carpeta(s) no encontrada(s) (¿hay un disco desconectado?)",preset_delete_confirm:"¿Borrar el preset «{0}»?",preset_need_folders:"Añade primero alguna carpeta",
 hdr_sub:"· generador de kits .xpm para MPC",footer:"© 2026 Wavethings · Código abierto (Licencia MIT)",sec_folders:"Carpetas de samples",btn_add:"＋ Añadir carpetas",btn_addsub:"＋ Añadir subcarpetas de…",btn_clear:"Quitar todas",title_clear:"Quita todas las carpetas y sus asignaciones de pads",confirm_clear:"¿Quitar las {0} carpetas y sus asignaciones de pads?",cleared:"Carpetas quitadas",
 title_addsub:"Añade cada subcarpeta de una carpeta madre como carpeta independiente",
 hint_folders:"1) Añade carpetas (⌘-clic para elegir varias, o una carpeta madre para añadir todas sus subcarpetas) · 2) selecciona una · 3) pulsa los pads de la derecha para asignársela. El color de la carpeta se aplica a sus pads en la MPC. “Choke” = grupo de choque: los pads de carpetas con el mismo número se cortan entre sí (p. ej. hi-hat cerrado y abierto).",
 sec_output:"Salida",lbl_template:"Plantilla",btn_othertpl:"Usar otro .xpm…",btn_builtin:"Integrada",title_builtin:"Volver a la plantilla integrada",
 lbl_folder:"Carpeta",btn_choose:"Elegir…",lbl_kits:"Nº de kits",lbl_name:"Nombre",lbl_seed:"Semilla",ph_seed:"(opcional) misma semilla = mismos kits",
 chk_recursive:"Incluir subcarpetas",chk_clear:"Vaciar y sin color en pads sin asignar",chk_hardlink:"Enlazar samples en vez de copiar (ahorra espacio)",
 chk_matchkey:"Filtrar por tonalidad",lbl_matchkey:"Tonalidad",key_random:"Aleatoria por kit",
 hint_matchkey:"Lee la nota raíz en el nombre del archivo (p. ej. \"Bass_C1\", \"Synth_Dm\", \"Lead - F#3\") y, cuando una carpeta tiene esa información, usa siempre la misma tonalidad dentro de cada kit. Las carpetas sin tonalidad detectada (batería, por ejemplo) nunca se filtran. La detección es aproximada y puede fallar con algunos nombres; añade {key} al patrón de nombre de arriba para mostrar la tonalidad de cada kit. Las carpetas filtrables llevan una insignia 🎵.",
 hint_matchkey_off:"Ninguna carpeta tiene tonalidad detectada todavía — añade \"C1\", \"Dm\", \"F#\" o similar a los nombres de archivo para usar este filtro.",
 key_badge:"🎵 {0}/{1} con tonalidad · {2}",
 btn_go:"Generar kits",sec_pads:"Pads",bank:"Banco {0}",
 chk_mix:"Mezclar: al pulsar un pad, añadir la carpeta activa (en vez de reemplazar). Mayús+clic hace lo contrario.",
 btn_fill:"Banco completo → carpeta activa",btn_clearbank:"Vaciar banco",lbl_copy:"Copiar este banco a",btn_copy:"Copiar",opt_all:"todos los demás",
 hint_pads:"Disposición igual que la MPC: el pad 1 es el de abajo a la izquierda. Los pads con varias carpetas se ven con franjas diagonales: en cada kit se elige una carpeta al azar (todas con la misma probabilidad) y una muestra de ella. El color y el choke del pad son los de su primera carpeta.",
 pal_title:"Color de la carpeta",btn_nocolor:"Sin color",btn_import:"Importar colores de un .xpm",
 hint_pal:"Las casillas con “?” son valores estimados. Si en la MPC ves un color distinto, importa un .xpm donde hayas pintado pads con los colores de la MPC y se añadirán con sus valores exactos.",
 not_found:"⚠ carpeta no encontrada",n_samples:"{0} samples",pads_lbl:"pads",none:"ninguno",chg_color:"Cambiar color",mute_grp:"Grupo de choque",
 nochoke:"Sin choke",choke:"Choke {0}",no_folders:"Aún no hay carpetas.",tpl_builtin:"Integrada (128 pads vacíos)",not_chosen:"(sin elegir)",
 pick_first:"Selecciona primero una carpeta",added:"{0} carpeta(s) añadida(s)",added_sub:"{0} subcarpeta(s) añadida(s)",skipped:" ({0} omitidas: vacías o ya añadidas)",
 copied:"Banco {0} copiado a {1}",need_out:"Falta la carpeta de salida",need_pads:"Asigna alguna carpeta a algún pad",generating:"Generando…",done:"✔ Terminado",
 busy:"Ya hay una generación en curso",found:"{0} colores encontrados en el archivo.",ask_path:"Ruta completa:",ask_paths:"Rutas de carpetas separadas por ;",
 "col.red":"Rojo","col.orange":"Naranja","col.yellow":"Amarillo","col.green":"Verde","col.petrol":"Azul petróleo","col.lime":"Lima","col.turquoise":"Turquesa","col.cyan":"Cian","col.blue":"Azul","col.violet":"Violeta","col.magenta":"Magenta","col.pink":"Rosa","col.white":"Blanco","col.grey":"Gris","col.imported":"Importado"}};
let LANG="en";
const t=(k,...a)=>{let s=(I18N[LANG]&&I18N[LANG][k])??I18N.en[k]??k;a.forEach((v,i)=>s=s.split("{"+i+"}").join(v));return s};
function applyStatic(){
  document.querySelectorAll("[data-i18n]").forEach(e=>e.textContent=t(e.dataset.i18n));
  document.querySelectorAll("[data-ph]").forEach(e=>e.placeholder=t(e.dataset.ph));
  document.querySelectorAll("[data-title]").forEach(e=>e.title=t(e.dataset.title));
  document.documentElement.lang=LANG}
const colName=c=>{const m=/^Import(?:ed|ado) (\d+)$/.exec(c.name);return m?t("col.imported")+" "+m[1]:(I18N[LANG]["col."+c.name]||c.name)};
const S={template:"",output:"",kits:10,name:"Kit {n:03d}",seed:"",recursive:true,clear:true,hardlink:false,mix:false,matchKey:"",lang:"",preset:"",folders:[],padmap:{}};
let PAL=[],PRESETS={},active=null,bank=0,palFor=null,MAC=true,saveT=null,copyTarget="all";
const $=id=>document.getElementById(id), BANKS="ABCDEFGH";
const esc=s=>String(s).replace(/[&<>"]/g,c=>({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;"}[c]));
const css=v=>v?`rgb(${((v>>16)&127)*2},${((v>>8)&127)*2},${(v&127)*2})`:"#3a3d44";
async function api(path,body){const r=await fetch(path,{method:body?"POST":"GET",headers:{"X-Token":TOKEN,"Content-Type":"application/json"},body:body?JSON.stringify(body):undefined});return r.json()}
function save(){clearTimeout(saveT);saveT=setTimeout(()=>api("/api/state",S),300)}
function folderPads(id){return Object.keys(S.padmap).filter(p=>(S.padmap[p]||[]).includes(id)).map(Number).sort((a,b)=>a-b)}
function rangeText(a){const o=[];for(let i=0;i<a.length;){let j=i;while(a[j+1]===a[j]+1)j++;o.push(j>i+1?a[i]+"-"+a[j]:a.slice(i,j+1).join(", "));i=j+1}return o.join(", ")}
async function pathFrom(kind){const r=await api("/api/pick",{kind,lang:LANG});if(r.error==="nodialog"){return prompt(t("ask_path"))||null}return r.path}
async function pickTo(key,kind){const p=await pathFrom(kind);if(p){S[key]=p;render();save()}}
async function addPaths(paths,skipEmpty){
  const have=new Set(S.folders.map(f=>f.path));let added=0,first=null;
  for(const p of paths){if(have.has(p))continue;
    const used=new Set(S.folders.map(f=>f.color));
    const c=(PAL.find(x=>!used.has(x.value))||PAL[S.folders.length%PAL.length]).value;
    const f={id:"f"+Date.now()+"_"+added,path:p,color:c,count:0,mute:0};await scan(f);
    if(skipEmpty&&f.count<=0)continue;
    S.folders.push(f);have.add(p);added++;first=first||f.id}
  if(first)active=first;render();save();return added}
async function addFolder(){
  const r=await api("/api/pick",{kind:"folders",lang:LANG});
  const ps=r.error==="nodialog"?(prompt(t("ask_paths"))||"").split(";").map(x=>x.trim()).filter(Boolean):(r.path||[]);
  if(ps.length){const n=await addPaths(ps);$("msg").textContent=t("added",n)}}
async function addSubfolders(){
  const p=await pathFrom("folder");if(!p)return;
  const r=await api("/api/subfolders",{path:p});const n=await addPaths(r.paths,true);
  $("msg").textContent=t("added_sub",n)+(r.paths.length>n?t("skipped",r.paths.length-n):"")}
function clearFolders(){
  if(!S.folders.length||!confirm(t("confirm_clear",S.folders.length)))return;
  S.folders=[];S.padmap={};active=null;$("msg").textContent=t("cleared");render();save()}
async function savePreset(){
  if(!S.folders.length){$("msg").textContent=t("preset_need_folders");return}
  const name=(prompt(t("preset_name"),S.preset||"")||"").trim().slice(0,60);if(!name)return;
  if(PRESETS[name]&&!confirm(t("preset_overwrite",name)))return;
  const preset={folders:S.folders.map(f=>({id:f.id,path:f.path,color:f.color||0,mute:f.mute||0})),padmap:S.padmap};
  const r=await api("/api/presets",{action:"save",name,preset});if(r.error)return;
  PRESETS=r.presets;S.preset=name;$("msg").textContent=t("preset_saved",name);render();save()}
async function loadPreset(){
  const name=$("presetSel").value,p=PRESETS[name];if(!p)return;
  if(S.folders.length&&!confirm(t("preset_load_confirm",name)))return;
  S.folders=p.folders.map(f=>({...f,count:0}));S.padmap=JSON.parse(JSON.stringify(p.padmap));
  active=S.folders.length?S.folders[0].id:null;S.preset=name;
  for(const f of S.folders)await scan(f);
  const missing=S.folders.filter(f=>f.count<0).length;
  $("msg").textContent=t("preset_loaded",name)+(missing?t("preset_missing",missing):"");render();save()}
async function deletePreset(){
  const name=$("presetSel").value;if(!PRESETS[name]||!confirm(t("preset_delete_confirm",name)))return;
  const r=await api("/api/presets",{action:"delete",name});if(r.error)return;
  PRESETS=r.presets;if(S.preset===name)S.preset="";render();save()}
async function scan(f){const r=await api("/api/scan",{path:f.path,recursive:S.recursive});f.count=r.count;f.keys=r.keys||{}}
const KEY_ORDER=["C","C#","D","D#","E","F","F#","G","G#","A","A#","B"];
const keySort=(a,b)=>{const ba=a.replace("m",""),bb=b.replace("m","");return KEY_ORDER.indexOf(ba)-KEY_ORDER.indexOf(bb)||a.length-b.length};
function allKeys(){const s=new Set();for(const f of S.folders)for(const k in (f.keys||{}))s.add(k);return[...s].sort(keySort)}
function keyBadge(f){const ks=Object.entries(f.keys||{});if(!ks.length)return"";
  const total=ks.reduce((s,[,n])=>s+n,0);
  const top=[...ks].sort((a,b)=>b[1]-a[1]).slice(0,4).map(([k])=>k).sort(keySort).join(", ")+(ks.length>4?"…":"");
  return t("key_badge",total,f.count,top)}
function rm(id){S.folders=S.folders.filter(f=>f.id!==id);for(const p in S.padmap){const r=S.padmap[p].filter(x=>x!==id);if(r.length)S.padmap[p]=r;else delete S.padmap[p]}if(active===id)active=null;render();save()}
function assign(n,add){const cur=S.padmap[n]||[];
  if(cur.includes(active)){const r=cur.filter(x=>x!==active);if(r.length)S.padmap[n]=r;else delete S.padmap[n]}
  else S.padmap[n]=add?[...cur,active]:[active]}
function padClick(n,ev){if(!active){$("msg").textContent=t("pick_first");return}
  assign(n,S.mix!==!!(ev&&ev.shiftKey));$("msg").textContent="";render();save()}
function fillBank(){if(!active)return;for(let i=1;i<=16;i++){const n=bank*16+i,cur=S.padmap[n]||[];
  if(!cur.includes(active))S.padmap[n]=S.mix?[...cur,active]:[active]}render();save()}
function setMute(id,v){S.folders.find(f=>f.id===id).mute=+v;render();save()}
function copyBank(){const src=[];for(let i=1;i<=16;i++)src.push(S.padmap[bank*16+i]);
  const ts=copyTarget==="all"?[0,1,2,3,4,5,6,7].filter(b=>b!==bank):[+copyTarget];
  for(const b of ts)for(let i=1;i<=16;i++){const v=src[i-1],n=b*16+i;if(v)S.padmap[n]=[...v];else delete S.padmap[n]}
  $("msg").textContent=t("copied",BANKS[bank],ts.map(b=>BANKS[b]).join(", "));render();save()}
function clearBank(){for(let i=1;i<=16;i++)delete S.padmap[bank*16+i];render();save()}
function openPal(id){palFor=id;const f=S.folders.find(x=>x.id===id);
  $("sw").innerHTML=PAL.map(c=>`<span class="${c.ok?"":"est"}" title="${esc(colName(c))}" style="background:${css(c.value)};${f.color===c.value?"outline:3px solid #fff":""}" onclick="setColor(${c.value})"></span>`).join("");
  $("pal").style.display="flex"}
function setColor(v){const f=S.folders.find(x=>x.id===palFor);if(f)f.color=v;$("pal").style.display="none";render();save()}
async function importColors(){const p=await pathFrom("xpm");if(!p)return;const r=await api("/api/import_colors",{path:p});
  if(r.error){alert(r.error);return}PAL=r.palette;alert(t("found",r.found));openPal(palFor)}
function render(){
  $("lang").value=LANG;$("btnClear").disabled=!S.folders.length;
  const pn=Object.keys(PRESETS).sort((a,b)=>a.localeCompare(b));
  $("presetSel").innerHTML=pn.length?`<option value="">${esc(t("preset_choose"))}</option>`+pn.map(n=>`<option value="${esc(n)}" ${n===S.preset?"selected":""}>${esc(n)}</option>`).join(""):`<option value="">${esc(t("preset_none"))}</option>`;
  $("btnPload").disabled=$("btnPdel").disabled=!pn.length;$("btnPsave").disabled=!S.folders.length;
  $("folders").innerHTML=S.folders.map(f=>{const pd=folderPads(f.id);
   return `<div class="card ${f.id===active?"on":""}" onclick="active='${f.id}';render()">
    <span class="dot" style="background:${css(f.color)}" title="${esc(t("chg_color"))}" onclick="event.stopPropagation();openPal('${f.id}')"></span>
    <div class="fi"><b>${esc(f.path.split(/[\\/]/).pop()||f.path)}</b><small>${f.count<0?t("not_found"):t("n_samples",f.count)} · ${t("pads_lbl")}: ${pd.length?rangeText(pd):t("none")}</small>${keyBadge(f)?`<small>${esc(keyBadge(f))}</small>`:""}<small>${esc(f.path)}</small></div>
    <select title="${esc(t("mute_grp"))}" onclick="event.stopPropagation()" onchange="setMute('${f.id}',this.value)">${`<option value="0">${t("nochoke")}</option>`+Array.from({length:32},(_,i)=>`<option value="${i+1}" ${f.mute===i+1?"selected":""}>${t("choke",i+1)}</option>`).join("")}</select>
    <button onclick="event.stopPropagation();rm('${f.id}')">✕</button></div>`}).join("")||`<div class="hint">${t("no_folders")}</div>`;
  $("tabs").innerHTML=[...BANKS].map((b,i)=>`<button class="${i===bank?"sel":""}" onclick="bank=${i};render()">${b}</button>`).join("");
  $("bname").textContent=t("bank",BANKS[bank]);
  let h="";for(let r=3;r>=0;r--)for(let c=0;c<4;c++){const n=bank*16+r*4+c+1,fs=(S.padmap[n]||[]).map(i=>S.folders.find(x=>x.id===i)).filter(Boolean),f=fs[0];
   const bg=!f?"":fs.length===1?css(f.color):`linear-gradient(135deg,${fs.map((x,k)=>`${css(x.color)} ${k*100/fs.length}% ${(k+1)*100/fs.length}%`).join(",")})`;
   h+=`<div class="pad" title="${esc(fs.map(x=>x.path.split(/[\\/]/).pop()).join(" + "))}" style="${f?`background:${bg}`:""}" onclick="padClick(${n},event)"><span>${n}</span><small>${f?esc(f.path.split(/[\\/]/).pop())+(fs.length>1?" +"+(fs.length-1):""):""}</small></div>`}
  $("pads").innerHTML=h;
  const ct=[...Array(8).keys()].filter(b=>b!==bank);if(copyTarget!=="all"&&+copyTarget===bank)copyTarget="all";
  $("copyTo").innerHTML=`<option value="all">${t("opt_all")}</option>`+ct.map(b=>`<option value="${b}">${t("bank",BANKS[b])}</option>`).join("");$("copyTo").value=copyTarget;
  $("tpl").textContent=S.template||t("tpl_builtin");$("out").textContent=S.output||t("not_chosen");
  for(const k of["kits","name","seed"])if(document.activeElement!==$(k))$(k).value=S[k];
  for(const k of["recursive","clear","hardlink","mix"])$(k).checked=S[k];
  const ks=allKeys(),hasKeys=ks.length>0;
  $("matchKeyOn").checked=!!S.matchKey;$("matchKeyOn").disabled=!hasKeys&&!S.matchKey;
  $("matchKeyRow").style.display=S.matchKey?"flex":"none";
  $("matchKeySel").innerHTML=`<option value="random">${esc(t("key_random"))}</option>`+ks.map(k=>`<option value="${k}" ${S.matchKey===k?"selected":""}>${k}</option>`).join("");
  if(S.matchKey)$("matchKeySel").value=ks.includes(S.matchKey)?S.matchKey:"random";
  $("matchKeyHint").textContent=hasKeys?t("hint_matchkey"):t("hint_matchkey_off")}
for(const k of["kits","name","seed"])$(k).oninput=e=>{S[k]=e.target.value;save()};
for(const k of["recursive","clear","hardlink","mix"])$(k).onchange=async e=>{S[k]=e.target.checked;if(k==="recursive"){for(const f of S.folders)await scan(f);render()}save()};
$("matchKeyOn").onchange=e=>{S.matchKey=e.target.checked?"random":"";render();save()};
$("matchKeySel").onchange=e=>{S.matchKey=e.target.value;save()};
$("lang").onchange=e=>{LANG=S.lang=e.target.value;$("msg").textContent="";applyStatic();render();save()};
async function generate(){
  const m=$("msg");m.className="";
  if(!S.output)return m.textContent=t("need_out");
  if(!Object.keys(S.padmap).length)return m.textContent=t("need_pads");
  $("go").disabled=true;m.textContent=t("generating");$("log").style.display="block";
  const r=await api("/api/generate",S);if(r.error){m.textContent=r.error==="busy"?t("busy"):r.error;$("go").disabled=false;return}
  const iv=setInterval(async()=>{const j=await api("/api/job");$("log").textContent=j.log.join("\n");$("log").scrollTop=1e9;
   if(j.done){clearInterval(iv);$("go").disabled=false;if(j.error){m.className="err";m.textContent=j.error}else m.textContent=t("done")}},400)}
setInterval(()=>api("/api/ping"),5000);
addEventListener("pagehide",()=>navigator.sendBeacon("/api/bye?t="+TOKEN));
(async()=>{const r=await api("/api/state");PAL=r.palette;PRESETS=r.presets||{};MAC=r.mac;if(r.state)Object.assign(S,r.state);for(const p in S.padmap)if(!Array.isArray(S.padmap[p]))S.padmap[p]=[S.padmap[p]];
  LANG=S.lang&&I18N[S.lang]?S.lang:((navigator.language||"en").toLowerCase().startsWith("es")?"es":"en");S.lang=LANG;
  for(const f of S.folders)await scan(f);active=S.folders.length?S.folders[0].id:null;applyStatic();render()})();
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


def main():
    server = None
    for port in range(8765, 8785):
        try:
            server = ThreadingHTTPServer(("127.0.0.1", port), H)
            break
        except OSError:
            continue
    if not server:
        sys.exit(srv("ports"))
    url = f"http://127.0.0.1:{server.server_address[1]}/"
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
