#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Wavethings
"""
Wavethings Kit Creator - engine and command-line tool (mpc_kit_creator.py).
Generates classic-format MPC .xpm drum kits from folders of samples.

Quick start:
  python3 mpc_kit_creator.py -o out_folder -n 20 \\
      --pad 1=~/Samples/Kicks --pad 2=~/Samples/Snares --pad 3-4=~/Samples/Hats --mute 3-4=1

Or with a configuration file:
  python3 mpc_kit_creator.py -c config.json

Pads: 1-16 = Bank A, 17-32 = Bank B ... up to 128 (same numbering as the MPC).
A pad can take several folders (repeat --pad 1=...) and ranges are allowed (--pad 1-4=...).
The interface language follows your system locale; force it with --lang en|es.
"""
import argparse, json, os, random, re, shutil, sys
from pathlib import Path
from xml.sax.saxutils import escape

__version__ = "1.9.1"

AUDIO_EXTS = {".wav", ".aif", ".aiff"}

# ---------------------------------------------------------------- musical key detection
# Looks for a root note in the file name: an uppercase letter A-G, with an optional
# accidental (# or b), octave number and minor marker (m / min), e.g. "Bass_C1.wav",
# "Synth_Dm.wav", "Lead - F#3.wav", "Chord_Bb2.wav", "Pad_G#m.wav". The note must stand as
# its own token (surrounded by the start/end of the name or a non letter/digit character)
# and use an UPPERCASE letter, which is how virtually every sample pack tags keys; this
# keeps ordinary words (e.g. "e_piano", "Bass") from being misread as note names. Known
# limitations: "Bmaj7" and combinations like "A#m3" (octave after the minor marker) are not
# recognized; a bare letter with no accidental/octave/minor marker ("Take_A.wav") is treated
# as a note, which is occasionally a false positive.
_KEY_BASE = {"C": 0, "D": 2, "E": 4, "F": 5, "G": 7, "A": 9, "B": 11}
_KEY_SHARPS = ["C", "C#", "D", "D#", "E", "F", "F#", "G", "G#", "A", "A#", "B"]
KEY_RE = re.compile(r"(?<![A-Za-z0-9])([A-G])(#|b)?(-?[0-8])?(m(?:in)?)?(?![A-Za-z0-9])")
KEY_INPUT_RE = re.compile(r"^\s*([A-G])(#|b)?(m(?:in)?)?\s*$", re.IGNORECASE)


def canonical_key(letter, accidental, minor):
    """('A', '#', True) -> 'A#m'. Normalizes flats to their sharp spelling so 'Db' and 'C#'
    group together."""
    semitone = (_KEY_BASE[letter] + (1 if accidental == "#" else -1 if accidental == "b" else 0)) % 12
    return _KEY_SHARPS[semitone] + ("m" if minor else "")


def parse_key(filename):
    """Returns the canonical key detected in a file name (e.g. "F#", "Dm"), or None."""
    m = KEY_RE.search(Path(filename).stem)
    return canonical_key(m.group(1), m.group(2), bool(m.group(4))) if m else None


def normalize_key_input(text):
    """Parses a key typed by hand (e.g. "Dbm", "f#", "Gmin"); returns the canonical form or None."""
    m = KEY_INPUT_RE.match(text or "")
    if not m:
        return None
    letter, accidental, minor = m.groups()
    return canonical_key(letter.upper(), accidental.lower() if accidental else None, bool(minor))


def index_by_key(files):
    """{key: [files with that detected key, ...]} for a list of sample paths."""
    idx = {}
    for f in files:
        k = parse_key(f.name)
        if k:
            idx.setdefault(k, []).append(f)
    return idx


# ---------------------------------------------------------------- pack / keyword mode
# Named groups of keywords for Pack mode, so the interface can offer a dropdown per pad
# instead of free text. Each group is matched against a sample's path relative to the pack
# folder (see matches_keywords), so it works whether a pack is organized into subfolders by
# instrument or just has descriptive file names. The actual groups live in a plain-text file
# the person edits themselves (see load_keyword_groups/DEFAULT_KEYWORD_GROUPS_TEXT); nothing
# here is hard-coded other than that starting content.
DEFAULT_KEYWORD_GROUPS_TEXT = """\
# Wavethings Kit Creator - keyword groups for Pack mode.
# One group per line: Group Name: keyword1, keyword2, keyword3
# Edit this file in any plain-text editor and reload the page to see the change. Matching is
# case-insensitive and checks each sample's path (file name and subfolder names) inside the
# pack, so a group like "Kick" also matches a "Kicks/" or "Kick One Shots/" subfolder.
Kick: kick, kek, kik, bass drum, bassdrum, bass kick, bd
Snare: snare, snr, snap, stick, sd
Clap: clap, cp
Closed Hihat: hat, closed, ride, hhcl, clhat, clhihat, tops, hh
Open Hihat: open, hhop, ohh, ophihat, ophat
Percusion: perc, tom, cymbal, crash, shake, tamb, wood, bell
Melodic: key, acid, synth, pad, bass, sub, 808, guitar, lead, vibraphone
"""


def load_keyword_groups(text):
    """Parses 'Group Name: keyword1, keyword2, ...' lines into an ordered {name: [keywords]}
    dict (insertion order = file order, which is how the dropdown lists them). Blank lines,
    lines starting with '#', and lines without a colon are ignored. A later line reusing the
    same name replaces the earlier one."""
    groups = {}
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#") or ":" not in line:
            continue
        name, _, rest = line.partition(":")
        name = name.strip()
        kws = [k.strip() for k in rest.split(",") if k.strip()]
        if name and kws:
            groups[name] = kws
    return groups

INSTR_RE = re.compile(r'(<Instrument number="(\d+)">)(.*?)(</Instrument>)', re.S)
LAYER_RE = re.compile(r'(<Layer number="(\d+)">.*?<SampleName>)(.*?)(</SampleName>)', re.S)
NAME_RE = re.compile(r"<ProgramName>.*?</ProgramName>", re.S)
PADS_BLOCK_RE = re.compile(r'(&quot;pads&quot;:\s*\{)(.*?)(\})', re.S)
PADVAL_RE = re.compile(r'(&quot;value(\d+)&quot;:\s*)(\d+)')

# ---------------------------------------------------------------- messages (en / es)
MSG = {
    "en": {
        "no_folder": "Folder not found: {folder}",
        "scan": "  {folder}: {n} samples",
        "empty": "  ! folder has no compatible samples (.wav/.aif/.aiff)",
        "kit": "[{name}] {n} pads assigned",
        "done": "\nDone: {n} kit(s) in {out}",
        "no_pads": "No pads assigned",
        "err_no_pads": "Pad assignments are missing (--pad or config file)",
        "err_choke": "Mute groups go from 1 to 32",
        "err_range": "Pads out of range (1-{max}): {bad}",
        "err_tpl": "Template not found: {tpl}\nCurrent folder: {cwd}\n"
                   "Copy the .xpm there or use the full path (drag the file into the Terminal).",
        "desc": "Wavethings Kit Creator - generator of MPC .xpm drum kits",
        "h_config": "JSON configuration file",
        "h_template": ".xpm template (optional: the built-in one is used by default)",
        "h_output": "output folder",
        "h_kits": "number of kits to generate",
        "h_name": 'name pattern, e.g. "Trance {n:03d}"',
        "h_pad": "N=folder or N-M=folder (repeat to add several folders to a pad)",
        "h_mute": "mute group: N=G or N-M=G (G from 1 to 32), e.g. --mute 3-4=1",
        "h_norec": "do not search subfolders",
        "h_keep": "do not empty the unassigned pads of the template",
        "h_link": "hard-link samples instead of copying them",
        "h_seed": "seed for reproducible results",
        "h_lang": "interface language (default: system language)",
        "h_key": "filter by key: KEY for a fixed key (e.g. C#m), or no value for a random key "
                 "per kit; only affects folders where a key was detected in file names",
        "h_padnum": "prefix every output sample name with its pad number, so the kit folder "
                    "also works with samplers that import files in alphabetical order with no "
                    ".xpm support of their own (Maschine, Battery, several hardware samplers)",
        "err_key": "Unrecognized key: {key} (examples: C, F#, Dbm, Gmin)",
        "key_none": "  ! --match-key was used but no key was detected in any file name: ignored",
        "kit_key": "[{name}] {n} pads assigned · key {key}",
        "key_fallback": "  (no {key} sample for pad(s) {pads}: used another key)",
        "kw_unmatched": "  ! no sample matches the keyword group on pad(s) {pads}: left empty",
        "group_missing": "  ! pad {pad}: keyword group \"{group}\" is not in keyword_groups.txt: skipped",
    },
    "es": {
        "no_folder": "No existe la carpeta: {folder}",
        "scan": "  {folder}: {n} samples",
        "empty": "  ! carpeta sin samples compatibles (.wav/.aif/.aiff)",
        "kit": "[{name}] {n} pads asignados",
        "done": "\nListo: {n} kit(s) en {out}",
        "no_pads": "No hay pads asignados",
        "err_no_pads": "Faltan las asignaciones de pads (--pad o archivo de configuración)",
        "err_choke": "Los grupos de choque van de 1 a 32",
        "err_range": "Pads fuera de rango (1-{max}): {bad}",
        "err_tpl": "No encuentro la plantilla: {tpl}\nCarpeta actual: {cwd}\n"
                   "Copia el .xpm ahí o usa la ruta completa (arrastra el archivo a la Terminal).",
        "desc": "Wavethings Kit Creator - generador de kits .xpm para MPC",
        "h_config": "archivo JSON de configuración",
        "h_template": ".xpm de plantilla (opcional: por defecto se usa la integrada)",
        "h_output": "carpeta de salida",
        "h_kits": "número de kits a generar",
        "h_name": 'patrón del nombre, p. ej. "Trance {n:03d}"',
        "h_pad": "N=carpeta o N-M=carpeta (repite para añadir varias carpetas a un pad)",
        "h_mute": "grupo de choque: N=G o N-M=G (G de 1 a 32), p. ej. --mute 3-4=1",
        "h_norec": "no entrar en subcarpetas",
        "h_keep": "no vaciar los pads sin asignar de la plantilla",
        "h_link": "enlazar en vez de copiar los samples",
        "h_seed": "semilla para resultados reproducibles",
        "h_lang": "idioma de la interfaz (por defecto: el del sistema)",
        "h_key": "filtrar por tonalidad: TONALIDAD para una fija (p. ej. C#m), o sin valor para "
                 "una tonalidad al azar por kit; solo afecta a carpetas con tonalidad detectada "
                 "en los nombres de archivo",
        "h_padnum": "añade el número de pad delante de cada muestra de salida, para que la carpeta "
                    "del kit también funcione con samplers que importan archivos en orden "
                    "alfabético y no leen .xpm (Maschine, Battery, varios samplers hardware)",
        "err_key": "Tonalidad no reconocida: {key} (ejemplos: C, F#, Dbm, Gmin)",
        "key_none": "  ! se usó --match-key pero no se detectó tonalidad en ningún archivo: se ignora",
        "kit_key": "[{name}] {n} pads asignados · tonalidad {key}",
        "key_fallback": "  (sin muestra en {key} para el/los pad(s) {pads}: se usó otra tonalidad)",
        "kw_unmatched": "  ! ninguna muestra coincide con el grupo de palabras clave en el/los pad(s) {pads}: se deja vacío",
        "group_missing": "  ! pad {pad}: el grupo de palabras clave \"{group}\" no está en keyword_groups.txt: se omite",
    },
}


def detect_lang():
    v = (os.environ.get("KITCREATOR_LANG") or os.environ.get("LC_ALL")
         or os.environ.get("LC_MESSAGES") or os.environ.get("LANG") or "")
    return "es" if v.lower().startswith("es") else "en"


LANG = detect_lang()


def msg(_key, **kw):
    text = MSG.get(LANG, MSG["en"])[_key]
    return text.format(**kw) if kw else text


def set_pad_colors(text, colors, clear_others=True):
    """MPC pad colors are integers (R<<16)|(G<<8)|B with 7-bit channels (0-127).
    colors = {pad (1-128): int}. Unassigned pads -> 0 if clear_others."""
    def block(m):
        def val(vm):
            pad = int(vm.group(2)) + 1
            if pad in colors:
                return vm.group(1) + str(int(colors[pad]))
            return vm.group(1) + "0" if clear_others else vm.group(0)
        return m.group(1) + PADVAL_RE.sub(val, m.group(2)) + m.group(3)
    return PADS_BLOCK_RE.sub(block, text, count=1)


def read_pad_colors(text):
    """Distinct non-zero pad color values used in an .xpm."""
    m = PADS_BLOCK_RE.search(text)
    if not m:
        return []
    seen = []
    for vm in PADVAL_RE.finditer(m.group(2)):
        v = int(vm.group(3))
        if v and v not in seen:
            seen.append(v)
    return seen


def parse_pad_spec(spec):
    """'3' -> [3]; '1-4' -> [1,2,3,4]"""
    spec = spec.strip()
    if "-" in spec:
        a, b = spec.split("-", 1)
        return list(range(int(a), int(b) + 1))
    return [int(spec)]


def scan_folder(folder, recursive=True):
    it = folder.rglob("*") if recursive else folder.glob("*")
    return sorted(
        p for p in it
        if p.is_file() and p.suffix.lower() in AUDIO_EXTS and not p.name.startswith(".")
    )


def _word_tokens(text):
    """Lowercase word tokens of a path, splitting on separators, camelCase and letter/digit
    boundaries: "Drums/HiHat_Closed01.wav" -> ["drums", "hi", "hat", "closed", "01", "wav"]."""
    text = re.sub(r"([a-z])([A-Z])", r"\1 \2", text)
    return [t.lower() for t in re.findall(r"[A-Za-z]+|\d+", text)]


def _keyword_hit(keyword, rel, tokens):
    """One keyword against one relative path. Keywords of 3+ characters match anywhere in the
    path (case-insensitive), so "kick" finds "Kicks/", "kick_01" and "BigKick". Shorter ones
    ("sd", "cp", "bd", "hh") are too easy to find inside unrelated words ("Subdued" contains
    "bd"), so they only match as a whole word, with an optional plural "s"."""
    k = keyword.strip().lower()
    if not k:
        return False
    kt = _word_tokens(keyword)
    if len(k.replace(" ", "")) >= 3:
        if k in rel:
            return True
    if not kt:
        return False
    n = len(kt)
    for i in range(len(tokens) - n + 1):
        if all(tokens[i + j] == kt[j] or tokens[i + j] == kt[j] + "s" for j in range(n)):
            return True
    return False


def matches_keywords(folder, file, keywords):
    """True if any keyword matches the sample's path relative to `folder` (so a keyword can
    match a subfolder name like "Kick" as well as the file name itself, which is how most
    sample packs are actually organized). See _keyword_hit for how each keyword is matched."""
    try:
        rel_raw = file.relative_to(folder).as_posix()
    except ValueError:
        rel_raw = file.name
    rel, tokens = rel_raw.lower(), _word_tokens(rel_raw)
    return any(_keyword_hit(k, rel, tokens) for k in keywords)


def _pad_sources(entries):
    """Normalizes one pad's source list: plain Path entries (ordinary folders) and
    (Path, keywords) tuples (pack folders filtered by keyword, see load_keyword_groups) can be
    mixed freely. Returns (ordered unique folder list, {folder: keywords_or_None})."""
    srcs, kw_for = [], {}
    for e in entries:
        path, kw = e if isinstance(e, tuple) else (e, None)
        if path not in kw_for:
            srcs.append(path)
            kw_for[path] = kw
        elif kw and not kw_for[path]:
            kw_for[path] = kw
    return srcs, kw_for


def pick_samples(pads, cache, rng, target_key=None, folder_keys=None, tonal_folders=None,
                 fallback=None, unmatched=None):
    """Pick one sample per pad. With several folders on a pad, a folder is chosen first
    (equal probability for each, regardless of how many samples it has) and then a sample.
    Avoids repeating a sample within the kit while free ones remain.

    Each entry in pads[pad] is either a folder Path (ordinary folder, no filter) or a
    (Path, keywords) tuple restricting that pad to samples matching any of `keywords` within
    that one folder (see matches_keywords) — this is how pack/keyword mode narrows one big,
    unsorted folder down to a single pad's role (e.g. "kick").

    A keyword group that matches nothing leaves the pad EMPTY (its number is appended to
    `unmatched` if that's a list) rather than filling it with an unrelated sample: a pad set
    to "Snare" must never end up with a chord.

    target_key/folder_keys/tonal_folders (all optional) add the key filter. For an ordinary
    folder it keeps only samples in target_key, if the folder has any sample with a detected
    key (tonal_folders). For a keyword-filtered pack it applies per pad instead: only if the
    samples matching that pad's keywords include some with a detected key (a melodic pad) are
    they limited to target_key, so the drum pads of a pack, which carry no key, are never
    emptied by it. If a pad can't be satisfied in target_key, another key is used (and its
    number is appended to `fallback`) rather than leaving it empty."""
    key_filtering = bool(target_key and tonal_folders)
    used, chosen = set(), {}
    for pad in sorted(pads):
        srcs, kw_for = _pad_sources(pads[pad])
        srcs = [x for x in srcs if cache[x]]
        if not srcs:
            continue

        pools, relaxed = {}, False
        for x in srcs:
            kw = kw_for.get(x)
            if kw:
                pool = [f for f in cache[x] if matches_keywords(x, f, kw)]
                if key_filtering and x in tonal_folders:
                    keyed = [f for f in pool if parse_key(f.name)]
                    if keyed:                                   # a melodic pad: apply the key
                        in_key = [f for f in keyed if parse_key(f.name) == target_key]
                        if in_key:
                            pool = in_key
                        else:
                            relaxed = True                      # another key beats an empty pad
            elif key_filtering and x in tonal_folders:
                pool = folder_keys[x].get(target_key, [])
            else:
                pool = cache[x]
            pools[x] = pool

        if not any(pools.values()):
            if any(kw_for.get(x) for x in srcs):
                if unmatched is not None:
                    unmatched.append(pad)
                continue                                        # explicit keywords found nothing
            if fallback is not None:                            # key filter only: use any key
                fallback.append(pad)
            pools = {x: cache[x] for x in srcs}
        elif relaxed and fallback is not None:
            fallback.append(pad)

        free = {x: [f for f in pools[x] if f not in used] for x in srcs}
        avail = [x for x in srcs if free[x]]
        if avail:
            src = rng.choice(avail)
            f = rng.choice(free[src])
        else:                       # everything used: allow repeats
            src = rng.choice(srcs)
            f = rng.choice(pools[src] or cache[src])
        used.add(f)
        chosen[pad] = f
    return chosen


def unique_stems(chosen, pad_prefix=False):
    """Unique sample name (without extension) within the kit. With pad_prefix, every name
    starts with its pad number zero-padded to 3 digits (e.g. "001_Kick"), so that sorting the
    kit folder by file name also sorts it by pad. That's what lets a sampler with no .xpm
    support of its own (Maschine, Battery, many hardware samplers) import the kit anyway: select
    a bank's 16 files in Finder/Explorer (they're already in the right order) and drop them
    together onto the sampler's first pad, which typically auto-assigns one file per pad in the
    order they were selected. chosen is iterated in pad order (guaranteed by pick_samples)."""
    file_to_stem, taken = {}, set()
    for pad, f in chosen.items():
        if f in file_to_stem:
            continue
        base = f"{pad:03d}_{f.stem}" if pad_prefix else f.stem
        cand, i = base, 2
        while cand.lower() in taken:
            cand = f"{base}_{i}"
            i += 1
        taken.add(cand.lower())
        file_to_stem[f] = cand
    return file_to_stem


MUTE_RE = re.compile(r"<MuteGroup>\d*</MuteGroup>")


def build_xpm(template, name, assignment, clear_others, mutegroups=None):
    """Replaces only sample names, program name and (optionally) mute groups; the rest is untouched."""
    def fill_instrument(m):
        head, num, body, tail = m.groups()
        pad = int(num)

        def fill_layer(lm):
            if int(lm.group(2)) == 1 and pad in assignment:
                return lm.group(1) + escape(assignment[pad]) + lm.group(4)
            if clear_others:
                return lm.group(1) + lm.group(4)
            return lm.group(0)

        body = LAYER_RE.sub(fill_layer, body)
        if mutegroups is not None:   # mute group (1-32, 0 = none)
            g = mutegroups.get(pad, 0) if (pad in assignment or clear_others) else None
            if g is not None:
                body = MUTE_RE.sub(f"<MuteGroup>{int(g)}</MuteGroup>", body, count=1)
        return head + body + tail

    out = INSTR_RE.sub(fill_instrument, template)
    return NAME_RE.sub(lambda _: f"<ProgramName>{escape(name)}</ProgramName>", out, count=1)


def default_template():
    """Built-in template: 128 empty pads with default parameters (one-shot, mono...).
    It contains no audio, sample names or colors: only the format's default parameters."""
    import base64, zlib
    return zlib.decompress(base64.b64decode("".join(_TEMPLATE_B64.split()))).decode("utf-8")


_TEMPLATE_B64 = """
eNrt3F1zW9dh7+F7fQqNLnqVmNgv2Bu7pZVx7KjxxI7VSHE7vcnAFCQxIQGVBN1oOvnuBUCCeNmb4lryWjOdznMuzhHBLQjaXPoR
/D/OOf3N3y8vnv48u7o+X8y/fFZ8MXr2dDY/W7w5n7/78tmfX7/49eTZb54/eXL6/cuvf/zhp7/OzpbPnzx9evrj7e9Y/3r10Yvz
i9lftg+VXxSnJweP3F701YcPF+dn0+X6kdWz/frH05P9h3oX7T1h88Xoi6I5uP7oyV9eTJdvF1eXz3949R+nJ/cfrV/qyd6Vpy+v
Fu+uppdPlx8/zL589s3VzeWz7TPcfuaP08vZ8z+cL1dPsvfAwSUvp2+un//P5qF/+q+bxfJf9h6/feCfn95+enfJn+fn65s8vehf
sLvo5+nFzWy0veLt9OJ6dn/VP3519IyvV3+D8CcbP/xEN9uXtnr928urclyUxxd+GPz7PfQnjn710CXF45eUj19SPX5J/fgl48cv
aR6/pH38ksnjl3QBty7k9gbc3yLgBhcBd7gIuMVFwD0uAm5yEXCXi4DbXATc5zLgPpch5zjgPpcB97kMuM9lwH0uA+5zGXCfy4D7
XAbc5yrgPlcB97kKCUbAfa4C7nMVcJ+rgPtcBdznKuA+VwH3uQ64z3XAfa4D7nMdUuaA+1wH3Oc64D7XAfe5DrjPdcB9Hgfc53HA
fR4H3OdxwH0eh3wLDLjP44D7PA64z+OA+zwOuM9NwH1uAu5zE3Cfm4D73ATc5ybkvUbAfW4C7nMTcJ+bgPvcBtznNuA+twH3uQ24
z23AfW4D7nMb8qYu4D63Afe5DbjPk4D7PAm4z5OA+zwJuM+TgPs8CbjPk4D7PAl59xxwnycB97kLuM9dwH3uAu5zF3Cfu4D73AXc
5y7gPncB97kL+TEl6OeUkB9URiE/qYxCflQZhfysMgr5YWUU8tPKKOTHlVHIzyujkB9YRiF3POxHw5A7HvTDYdBPh0E/Hgb9fBj0
A2LQT4hBPyKG/IxYhPyQWJRBP42H3PGQnxOLkB8Ui5CfFIuQHxWLvZ8VHx6Y/jy/uZ69eRm1HhW7p3ty+3//436R2+xvd3PhzZvz
xZ8WN8u7ie7wofL05JMXvLr56dv5m9nfn4/2L7x/tP8bvn4/nc9nF789X15OPzyv9n/X4ae2v/Xb+fXsann9u/n0p4vZm+evr25m
pydHD97+TXqv9PTVbP6meD76YrT5P6cntx/vPlcefa7c+1x19Llq73P10efqu8/9uLi4uZytPtmO2q5uTk/uHrj97PfrF/ZiPUme
nny/9yIXF4vtw5tf3y2l0/nqicZ3f8r6o+3Xa7m43Oy3L84vlrOr58X6L3702N1TnC/P3u+91NuPbz/3+mY++3oxvbqerb90ex/t
Pv3ifH7/yc2v7/4ai/n96938+mDZ/cvLxcXHD+8X84/r39p/8Mn2i7q8Wt2Z+fJ6/wt999jT+c3lT7OrL58Vz57fH+KBg3r88Gjo
sH7Wgf2Fhzbu4A4f3scO8GOH+LGD/NhhfvxAf+JQf+Jgf+pwRx7wgIP8yGHeHejbL8/uPN++zPtju3oNR2d4qzarV/OH2cfl1fTs
b3s38ugTu9/x3eK//7i4PavbX+4++fvzd+83D62+K5ye3H+0u+Dbd/PF1ey30+vZ5hN3N/fo0d3l/7mYz15eTDcv//7Xh1+7f71a
3HxYv5rdB4cXvJ5evZsti+0l2w+HLioPLyoHL6oOL6oGL6oPL9o/kq/OL28ulnuv6vCB4QvL4wvLBy6sji+sHriwPr5w/zV+93Zx
3N77hw6u+np1rt++Pbzs7rGD6+7/He5d1/unuP4jNv+u9v/Mg39aP8xnr94vlneHffvR8XFes9j6G//eR7tL7l5dcf+H9F7un2bX
i/l0frb/cnePHf9hv5v//NXlsvcv5+7hvSq8Xb+Wxc3Z+9eLuwbsfs/AJ/caNrtYnJ0vP75evFpOr/b/qOPPDP2e26f7ark8/Pf9
wAUPP8Mnfu8nftfqNqwe+TD7xO++v+T4zvZe8wOv9Pbhb2ZnqzasLq7bsq63F98+enztq5vr5fR8vncIDh8/vv5Ps4vZ9HrWeynb
x4+v//3i4k3v4s2Dg696c0Jvj/Txo7178s397dr/DXsPH3/vO3otew8eX3n8Wo4fPb6+91p6D/d+R+8U7j88+Hr2vqL7jx5f2/+K
Hj5+fH3/K3r4+NCJPi7i8WeGfs9Df+/hC/rPsHo/c32+PP959cv9v9vAZwdf8UFNDx/fy+6LHw7e922+2K9WbzFWbzYOvu7rNk7X
3/rv3/hsPtz//KuP87PNN5b1/3vwG2fXs+X2+/3tB3vvHQ9ewum/T68+vJ5dflis3kbsXv7u0d2Vv/1w+d1ideNuz9/2o8Nnun2r
uv2j9x7Z+764vJqtvoIvZ1dnq3fv03er9y+bN5O9h/du2vTj7Or64K+4eWjwrf/tN4Cz1Vdq+0/l7oPDK+6+IR6f4aOrPvHW87Ef
miLecwa879wetrvvSpsDdvR9aHvJ7+Zvbt8S3v368IJX08sPF7P7p9n/cOjC9RPcX9Z/tu8Wiw/br/bm1/1P3/9Ruw/6F939Mdtf
9i/4+mpxff12+mb23Wz+bvl+e/Hxw/3fuL6V26s3vz685OGfSG7/JS0Wy+078PtfH16yetv+evO2/e5Z7j8eup2b/7hpezt3/6XT
0VXr/5jr/qrNB0dXXZyfzW5/BC3KbnXh7uPDC785v5qdbf5rr9Xr331weNEPb9+uC7G64u5XA3/Y7rzsPhq4bP21q7Y/HW4fGLju
4FwcPfLA5QdXPnTR5ji8ODglD35uvyYnm5x8IjClwAiMwPyfCMz/t7ZU2qIt2qItGdpSa4u2aIu2/PK23D2wI9iTncF+imVLLItl
sSyWxbJYFstiWSyLZbEslrU+WB+sD1gWywqMwJg3say2aIu2YFlt0RZt0ZacLFthWSyLZbEslsWyWBbLYlksi2WxrPXB+mB9wLJY
VmAExryJZbVFW7QFy2qLtmiLtuRk2RrLYlksi2WxLJbFslgWy2JZLItlrQ/WB+sDlsWyAiMw5k0sqy3aoi1YVlu0RVu0JSfLjrEs
lsWyWBbLYlksi2WxLJbFsljW+mB9sD5gWSwrMAJj3sSy2qIt2oJltUVbtEVbcrJsg2WxLJbFslgWy2JZLItlsSyWxbLWB+uD9QHL
YlmBERjzJpbVFm3RFiyrLdqiLdqSk2VbLItlsSyWxbJYFstiWSyLZbEslrU+WB+sD1gWywqMwJg3say2aIu2YFlt0RZt0ZacLDvB
slgWy2JZLItlsSyWxbJYFstiWeuD9cH6gGWxrMAIjHkTy2qLtmgLltUWbdEWbcnJsh2WxbJYFstiWSyLZbEslsWyWBbLWh+sD9YH
LItlBUZgzJtYVlu0RVuwrLZoi7ZoS06WLUZclstyWS7LZbksl+WyXJbLclkua34wP5gfuCyXFRiBsW9yWW3RFm3hstqiLdqiLVld
tuCyXJbLclkuy2W5LJflslyWy3JZ84P5wfzAZbmswAiMfZPLaou2aAuX1RZt0RZtyeqyJZflslyWy3JZLstluSyX5bJclsuaH8wP
5gcuy2UFRmDsm1xWW7RFW7istmiLtmhLVpetuCyX5bJclstyWS7LZbksl+WyXNb8YH4wP3BZLiswAmPf5LLaoi3awmW1RVu0RVuy
umzNZbksl+WyXJbLclkuy2W5LJflsuYH84P5gctyWYERGPsml9UWbdEWLqst2qIt2pLVZcdclstyWS7LZbksl+WyXJbLclkua34w
P5gfuCyXFRiBsW9yWW3RFm3hstqiLdqiLVldtuGyXJbLclkuy2W5LJflslyWy3JZ84P5wfzAZbmswAiMfZPLaou2aAuX1RZt0RZt
yeqyLZflslyWy3JZLstluSyX5bJclsuaH8wP5gcuy2UFRmDsm1xWW7RFW7istmiLtmhLVpedcFkuy2W5LJflslyWy3JZLstluaz5
wfxgfuCyXFZgBMa+yWW1RVu0hctqi7Zoi7ZkddmOy3JZLstluSyX5bJclstyWS7LZc0P5gfzA5flsgIjMPZNLqst2qItXFZbtEVb
tCWny5YjLstluSyX5bJclstyWS7LZbkslzU/mB/MD1yWywqMwNg3uay2aIu2cFlt0RZt0ZasLltwWS7LZbksl+WyXJbLclkuy2W5
rPnB/GB+4LJcVmAExr7JZbVFW7SFy2qLtmiLtmR12ZLLclkuy2W5LJflslyWy3JZLstlzQ/mB/MDl+WyAiMw9k0uqy3aoi1cVlu0
RVu0JavLVlyWy3JZLstluSyX5bJclstyWS5rfjA/mB+4LJcVGIGxb3JZbdEWbeGy2qIt2qItWV225rJclstyWS7LZbksl+WyXJbL
clnzg/nB/MBluazACIx9k8tqi7ZoC5fVFm3RFm3J6rJjLstluSyX5bJclstyWS7LZbkslzU/mB/MD1yWywqMwNg3uay2aIu2cFlt
0RZt0ZasLttwWS7LZbksl+WyXJbLclkuy2W5rPnB/GB+4LJcVmAExr7JZbVFW7SFy2qLtmiLtmR12ZbLclkuy2W5LJflslyWy3JZ
LstlzQ/mB/MDl+WyAiMw9k0uqy3aoi1cVlu0RVu0JavLTrgsl+WyXJbLclkuy2W5LJflslzW/GB+MD9wWS4rMAJj3+Sy2qIt2sJl
tUVbtEVbsrpsx2W5LJflslyWy3JZLstluSyX5bLmB/OD+YHLclmBERj7JpfVFm3RFi6rLdqiLdqS02WrEZflslyWy3JZLstluSyX
5bJclsuaH8wP5gcuy2UFRmDsm1xWW7RFW7istmiLtmhLVpctuCyX5bJclstyWS7LZbksl+WyXNb8YH4wP3BZLiswAmPf5LLaoi3a
wmW1RVu0RVuyumzJZbksl+WyXJbLclkuy2W5LJflsuYH84P5gctyWYERGPsml9UWbdEWLqst2qIt2pLVZSsuy2W5LJflslyWy3JZ
LstluSyXNT+YH8wPXJbLCozA2De5rLZoi7ZwWW3RFm3RlqwuW3NZLstluSyX5bJclstyWS7LZbms+cH8YH7gslxWYATGvslltUVb
tIXLaou2aIu2ZHXZMZflslyWy3JZLstluSyX5bJclsuaH8wP5gcuy2UFRmDsm1xWW7RFW7istmiLtmhLVpdtuCyX5bJclstyWS7L
Zbksl+WyXNb8YH4wP3BZLiswAmPf5LLaoi3awmW1RVu0RVuyumzLZbksl+WyXJbLclkuy2W5LJflsuYH84P5gctyWYERGPsml9UW
bdEWLqst2qIt2pLVZSdclstyWS7LZbksl+WyXJbLclkua34wP5gfuCyXFRiBsW9yWW3RFm3hstqiLdqiLVldtuOyXJbLclkuy2W5
LJflslyWy3JZ84P5wfzAZbmswAiMfZPLaou2aAuX1RZt0RZtyemy9YjLclkuy2W5LJflslyWy3JZLstlzQ/mB/MDl+WyAiMw9k0u
qy3aoi1cVlu0RVu0JavLFlyWy3JZLstluSyX5bJclstyWS5rfjA/mB+4LJcVGIGxb3JZbdEWbeGy2qIt2qItWV225LJclstyWS7L
Zbksl+WyXJbLclnzg/nB/MBluazACIx9k8tqi7ZoC5fVFm3RFm3J6rIVl+WyXJbLclkuy2W5LJflslyWy5ofzA/mBy7LZQVGYOyb
XFZbtEVbuKy2aIu2aEtWl625LJflslyWy3JZLstluSyX5bJc1vxgfjA/cFkuKzACY9/kstqiLdrCZbVFW7RFW7K67JjLclkuy2W5
LJflslyWy3JZLstlzQ/mB/MDl+WyAiMw9k0uqy3aoi1cVlu0RVu0JavLNlyWy3JZLstluSyX5bJclstyWS5rfjA/mB+4LJcVGIGx
b3JZbdEWbeGy2qIt2qItWV225bJclstyWS7LZbksl+WyXJbLclnzg/nB/MBluazACIx9k8tqi7ZoC5fVFm3RFm3J6rITLstluSyX
5bJclstyWS7LZbkslzU/mB/MD1yWywqMwNg3uay2aIu2cFlt0RZt0ZasLttxWS7LZbksl+WyXJbLclkuy2W5rPnB/GB+4LJcVmAE
xr7JZbVFW7SFy2qLtmiLtuR02fGIy3JZLstluSyX5bJclstyWS7LZc0P5gfzA5flsgIjMPZNLqst2qItXFZbtEVbtCWryxZclsty
WS7LZbksl+WyXJbLclkua34wP5gfuCyXFRiBsW9yWW3RFm3hstqiLdqiLVldtuSyXJbLclkuy2W5LJflslyWy3JZ84P5wfzAZbms
wAiMfZPLaou2aAuX1RZt0RZtyeqyFZflslyWy3JZLstluSyX5bJclsuaH8wP5gcuy2UFRmDsm1xWW7RFW7istmiLtmhLVpetuSyX
5bJclstyWS7LZbksl+WyXNb8YH4wP3BZLiswAmPf5LLaoi3awmW1RVu0RVuyuuyYy3JZLstluSyX5bJclstyWS7LZc0P5gfzA5fl
sgIjMPZNLqst2qItXFZbtEVbtCWryzZclstyWS7LZbksl+WyXJbLclkua34wP5gfuCyXFRiBsW9yWW3RFm3hstqiLdqiLVldtuWy
XJbLclkuy2W5LJflslyWy3JZ84P5wfzAZbmswAiMfZPLaou2aAuX1RZt0RZtyeqyEy7LZbksl+WyXJbLclkuy2W5LJc1P5gfzA9c
lssKjMDYN7mstmiLtnBZbdEWbdGWrC7bcVkuy2W5LJflslyWy3JZLstluaz5wfxgfuCyXFZgBMa+yWW1RVu0hctqi7Zoi7bkdNlm
xGW5LJflslyWy3JZLstluSyX5bLmB/OD+YHLclmBERj7JpfVFm3RFi6rLdqiLdqS1WULLstluSyX5bJclstyWS7LZbkslzU/mB/M
D1yWywqMwNg3uay2aIu2cFlt0RZt0ZasLltyWS7LZbksl+WyXJbLclkuy2W5rPnB/GB+4LJcVmAExr7JZbVFW7SFy2qLtmiLtmR1
2YrLclkuy2W5LJflslyWy3JZLstlzQ/mB/MDl+WyAiMw9k0uqy3aoi1cVlu0RVu0JavL1lyWy3JZLstluSyX5bJclstyWS5rfjA/
mB+4LJcVGIGxb3JZbdEWbeGy2qIt2qItWV12zGW5LJflslyWy3JZLstluSyX5bLmB/OD+YHLclmBERj7JpfVFm3RFi6rLdqiLdqS
1WUbLstluSyX5bJclstyWS7LZbkslzU/mB/MD1yWywqMwNg3uay2aIu2cFlt0RZt0ZasLttyWS7LZbksl+WyXJbLclkuy2W5rPnB
/GB+4LJcVmAExr7JZbVFW7SFy2qLtmiLtmR12QmX5bJclstyWS7LZbksl+WyXJbLmh/MD+YHLstlBUZg7JtcVlu0RVu4rLZoi7Zo
S1aX7bgsl+WyXJbLclkuy2W5LJflslzW/GB+MD9wWS4rMAJj3+Sy2qIt2sJltUVbtEVbcrpsO+KyXJbLclkuy2W5LJflslyWy3JZ
84P5wfzAZbmswAiMfZPLaou2aAuX1RZt0RZtyeqyBZflslyWy3JZLstluSyX5bJclsuaH8wP5gcuy2UFRmDsm1xWW7RFW7istmiL
tmhLVpctuSyX5bJclstyWS7LZbksl+WyXNb8YH4wP3BZLiswAmPf5LLaoi3awmW1RVu0RVuyumzFZbksl+WyXJbLclkuy2W5LJfl
suYH84P5gctyWYERGPsml9UWbdEWLqst2qIt2pLVZWsuy2W5LJflslyWy3JZLstluSyXNT+YH8wPXJbLCozA2De5rLZoi7ZwWW3R
Fm3RlqwuO+ayXJbLclkuy2W5LJflslyWy3JZ84P5wfzAZbmswAiMfZPLaou2aAuX1RZt0RZtyeqyDZflslyWy3JZLstluSyX5bJc
lsuaH8wP5gcuy2UFRmDsm1xWW7RFW7istmiLtmhLVpdtuSyX5bJclstyWS7LZbksl+WyXNb8YH4wP3BZLiswAmPf5LLaoi3awmW1
RVu0RVuyuuyEy3JZLstluSyX5bJclstyWS7LZc0P5gfzA5flsgIjMPZNLqst2qItXFZbtEVbtCWry3ZclstyWS7LZbksl+WyXJbL
clkua34wP5gfuCyXFRiBsW9yWW3RFm3hstqiLdqiLTlddjLislyWy3JZLstluSyX5bJclstyWfOD+cH8wGW5rMAIjH2Ty2qLtmgL
l9UWbdEWbcnqsgWX5bJclstyWS7LZbksl+WyXJbLmh/MD+YHLstlBUZg7JtcVlu0RVu4rLZoi7ZoS1aXLbksl+WyXJbLclkuy2W5
LJflslzW/GB+MD9wWS4rMAJj3+Sy2qIt2sJltUVbtEVbsrpsxWW5LJflslyWy3JZLstluSyX5bLmB/OD+YHLclmBERj7JpfVFm3R
Fi6rLdqiLdqS1WVrLstluSyX5bJclstyWS7LZbkslzU/mB/MD1yWywqMwNg3uay2aIu2cFlt0RZt0ZasLjvmslyWy3JZLstluSyX
5bJclstyWfOD+cH8wGW5rMAIjH2Ty2qLtmgLl9UWbdEWbcnqsg2X5bJclstyWS7LZbksl+WyXJbLmh/MD+YHLstlBUZg7JtcVlu0
RVu4rLZoi7ZoS1aXbbksl+WyXJbLclkuy2W5LJflslzW/GB+MD9wWS4rMAJj3+Sy2qIt2sJltUVbtEVbsrrshMtyWS7LZbksl+Wy
XJbLclkuy2XND+YH8wOX5bICIzD2TS6rLdqiLVxWW7RFW7Qlq8t2XJbLclkuy2W5LJflslyWy3JZLmt+MD+YH7gslxUYgbFvcllt
0RZt4bLaoi3aoi05XbYbcVkuy2W5LJflslyWy3JZLstluaz5wfxgfuCyXFZgBMa+yWW1RVu0hctqi7Zoi7ZkddmCy3JZLstluSyX
5bJclstyWS7LZc0P5gfzA5flsgIjMPZNLqst2qItXFZbtEVbtCWry5ZclstyWS7LZbksl+WyXJbLclkua34wP5gfuCyXFRiBsW9y
WW3RFm3hstqiLdqiLVldtuKyXJbLclkuy2W5LJflslyWy3JZ84P5wfzAZbmswAiMfZPLaou2aAuX1RZt0RZtyeqyNZflslyWy3JZ
LstluSyX5bJclsuaH8wP5gcuy2UFRmDsm1xWW7RFW7istmiLtmhLVpcdc1kuy2W5LJflslyWy3JZLstluaz5wfxgfuCyXFZgBMa+
yWW1RVu0hctqi7Zoi7ZkddmGy3JZLstluSyX5bJclstyWS7LZc0P5gfzA5flsgIjMPZNLqst2qItXFZbtEVbtCWry7ZclstyWS7L
Zbksl+WyXJbLclkua34wP5gfuCyXFRiBsW9yWW3RFm3hstqiLdqiLVlddsJluSyX5bJclstyWS7LZbksl+Wy5gfzg/mBy3JZgREY
+yaX1RZt0RYuqy3aoi3aktVlOy7LZbksl+WyXJbLclkuy2W5LJc1P5gfzA9clssKjMDYN7mstmiLtnBZbdEWbdGWnC5bjEZgFsyC
WTALZsEsmAWzYBbMglkwa3+wP9gfwCyYFRiBMXCCWW3RFm0Bs9qiLdqiLXlhtgCzYBbMglkwC2bBLJgFs2AWzIJZ+4P9wf4AZsGs
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
9E9sF31im/6J7aJPbNM/sV30iW36J7aLPrFN/8QW0Se26Z/YMn5s6J/YMvrENv0TW0af2KZ/YsvoE9v2T2wZfWLb/okto09s2z+x
ZfSJbfsntow+sW3/xJbxg1L/xJbRJ7YdeFcQfWLb/omtok9s2z+xVfSJbfsntoo+sZP+ia2iT+xkoLHRX51J/8Ru/vO+yGepBp4l
+uszqQeeJfoLNBnI7Cj+K9QMPEv8l6gdeJboqkwmA88Sv+x2A88S3ZVuNPAs0WHpioFniT673cDZLaLPbjdwdovos9sNnN0i+ux2
A2e3iD673cDZLeJX/IGzW0Sf3W7g7BbRZ7cbOLtFPAeMBg5v8Rk0MXB649/JrdLWf5r493Krtg08TfyiPxo4wPHv51Z1G3iaeBoY
DRzh+Pd0q74NPE08mYwGDnH8+7pV4Qae5jNQq3+K40/NAI3Fn5kBGos/MQM0Fn9eBmgs/rQM0Vj8kwxAQ/yT9M/tZ5yT/qmNb90A
jcWXrhx60xD/LEPZjX+WoerGP8tAdONP7QCPxX+fLgZ8rPgM5R4obvzBHRCyz/g+PUBkj3ybvv/w/r9bWT/r5n/xfPRfsmweG/5P
We7/19IH/0vpzVMfPtJ7mjLN01RpnqZO8zTjNE/TpHmaNs3TTNI8TZfmaQ6/lf+C50l0jotEB7lIdJKLREe5SHSWi0SHuUh0motE
x7lIdJ7LROe5TNXlROe5THSey0TnuUx0nstE57lMdJ7LROe5THSeq0TnuUp0nqtUbzQSnecq0XmuEp3nKtF5rhKd5yrRea4Snec6
0XmuE53nOtF5rlO9c050nutE57lOdJ7rROe5TnSe60TneZzoPI8TnedxovM8TnSex6l+FEx0nseJzvM40XkeJzrP40TnuUl0nptE
57lJdJ6bROe5SXSem1TbRqLz3CQ6z02i89wkOs9tovPcJjrPbaLz3CY6z22i89wmOs9tqrEu0XluE53nNtF5niQ6z5NE53mS6DxP
Ep3nSaLzPEl0nieJzvMk1fqc6DxPEp3nLtF57hKd5y7Ree4Snecu0XnuEp3nLtF57hKd5y4VpyTzlFSgMkolKqNUpDJKZSqjVKgy
SqUqo1SsMkrlKqNUsDJKdbLTUWGqk50MC5NpYTIuTOaFycAwmRgmI8NUZlikQsOiTKbgqU52KjcsUsFhkUoOi1R0WKSyw+Jz8HD3
8e4/HPq3787nf/vq+vr83Xzz/zHOyfrx1YVXi3dX08vnT05Pvn/59Y8//PTX2dny+f8ClxsDmg==
"""


def generate_kits(template, out_root, n_kits, name_pat, pads, colors=None, clear_others=True,
                  recursive=True, hardlink=False, seed=None, log=print, mutegroups=None,
                  match_key=None, pad_numbering=False):
    """pads: {pad: [folder Path, ...]}; colors: {pad: int} or None (leave colors untouched).

    match_key: None/False disables key filtering. True or "random" picks a different key for
    each kit, at random among the keys detected in the assigned folders. A specific key (e.g.
    "C#m", typically from normalize_key_input) fixes that key for every kit. Only folders with
    at least one sample whose file name carries a detected key are restricted by the filter;
    others (e.g. drums) are unaffected. name_pat may use "{key}" to include the kit's key.
    pad_numbering: prefixes every output sample name with its pad number (see unique_stems) so
    the kit folder also works with samplers that have no .xpm support of their own and just
    import a selection of files in alphabetical order (Maschine, Battery, several hardware
    samplers) — drag a bank's 16 files together onto the sampler's first pad.
    An entry of pads[pad] may be a (Path, keywords) tuple instead of a plain Path to restrict
    that one pad to samples matching any of those keywords within that folder; see
    pick_samples and matches_keywords."""
    all_folders, any_keywords, keyword_folders = set(), False, set()
    for entries in pads.values():
        srcs, kw_for = _pad_sources(entries)
        all_folders.update(srcs)
        any_keywords = any_keywords or any(kw_for.values())
        keyword_folders.update(x for x, kw in kw_for.items() if kw)

    cache = {}
    for folder in all_folders:
        if not folder.is_dir():
            raise FileNotFoundError(msg("no_folder", folder=folder))
        # A folder used with a keyword filter on any pad is always scanned recursively,
        # regardless of the "include subfolders" setting: keyword matching is specifically
        # meant to find samples by their subfolder name (see matches_keywords), and a pack
        # scanned non-recursively would see none of its organized content, find no matches,
        # and silently fall back to picking from whatever loose files sit at its top level.
        cache[folder] = scan_folder(folder, True if folder in keyword_folders else recursive)
        log(msg("scan", folder=folder, n=len(cache[folder])))
        if not cache[folder]:
            log(msg("empty"))

    folder_keys, tonal_folders, available_keys = {}, set(), []
    if match_key:
        for folder, files in cache.items():
            idx = index_by_key(files)
            if idx:
                folder_keys[folder] = idx
                tonal_folders.add(folder)
        available_keys = sorted({k for idx in folder_keys.values() for k in idx})
        if not available_keys:
            log(msg("key_none"))
            match_key = None

    rng = random.Random(seed)
    out_root = Path(out_root).expanduser()
    out_root.mkdir(parents=True, exist_ok=True)

    n = 0
    for _ in range(n_kits):
        n += 1
        target_key = None
        if match_key:
            target_key = (rng.choice(available_keys) if match_key is True or match_key == "random"
                         else match_key)
        name = name_pat.format(n=n, key=target_key or "")
        while (out_root / name).exists():      # never overwrite existing kits
            n += 1
            name = name_pat.format(n=n, key=target_key or "")
        fallback_pads = [] if (target_key or any_keywords) else None
        unmatched_pads = [] if any_keywords else None
        chosen = pick_samples(pads, cache, rng, target_key, folder_keys, tonal_folders,
                              fallback_pads, unmatched_pads)
        stems = unique_stems(chosen, pad_numbering)
        kit_dir = out_root / name
        kit_dir.mkdir()

        for f, stem in stems.items():
            dest = kit_dir / (stem + f.suffix.lower())
            if hardlink:
                try:
                    os.link(f, dest)
                    continue
                except OSError:
                    pass
            shutil.copy2(f, dest)

        assignment = {pad: stems[f] for pad, f in chosen.items()}
        xpm = build_xpm(template, name, assignment, clear_others, mutegroups)
        if colors is not None:
            xpm = set_pad_colors(xpm, colors, clear_others)
        (kit_dir / f"{name}.xpm").write_text(xpm, encoding="utf-8")
        if target_key:
            log(msg("kit_key", name=name, n=len(assignment), key=target_key))
        else:
            log(msg("kit", name=name, n=len(assignment)))
        if fallback_pads and target_key:
            log(msg("key_fallback", key=target_key,
                    pads=", ".join(str(p) for p in sorted(set(fallback_pads)))))
        if unmatched_pads:
            log(msg("kw_unmatched", pads=", ".join(str(p) for p in sorted(set(unmatched_pads)))))
    log(msg("done", n=n_kits, out=out_root))


def main():
    global LANG
    for i, x in enumerate(sys.argv):        # pick the language before building --help
        if x == "--lang" and i + 1 < len(sys.argv):
            LANG = sys.argv[i + 1]
        elif x.startswith("--lang="):
            LANG = x.split("=", 1)[1]
    ap = argparse.ArgumentParser(description=msg("desc"))
    ap.add_argument("-V", "--version", action="version", version=f"Wavethings Kit Creator {__version__}")
    ap.add_argument("-c", "--config", help=msg("h_config"))
    ap.add_argument("-t", "--template", help=msg("h_template"))
    ap.add_argument("-o", "--output", help=msg("h_output"))
    ap.add_argument("-n", "--kits", type=int, help=msg("h_kits"))
    ap.add_argument("--name", help=msg("h_name"))
    ap.add_argument("--pad", action="append", default=[], help=msg("h_pad"))
    ap.add_argument("--mute", action="append", default=[], help=msg("h_mute"))
    ap.add_argument("--no-recursive", action="store_true", help=msg("h_norec"))
    ap.add_argument("--keep-others", action="store_true", help=msg("h_keep"))
    ap.add_argument("--hardlink", action="store_true", help=msg("h_link"))
    ap.add_argument("--seed", type=int, help=msg("h_seed"))
    ap.add_argument("--match-key", nargs="?", const="random", default=None, metavar="KEY", help=msg("h_key"))
    ap.add_argument("--pad-numbering", action="store_true", help=msg("h_padnum"))
    ap.add_argument("--lang", choices=["en", "es"], help=msg("h_lang"))
    a = ap.parse_args()

    cfg = {}
    if a.config:
        cfg = json.loads(Path(a.config).expanduser().read_text(encoding="utf-8"))

    template_path = a.template or cfg.get("template")
    output = a.output or cfg.get("output", "kits")
    n_kits = a.kits or cfg.get("kits", 1)
    name_pat = a.name or cfg.get("name", "Kit {n:03d}")
    recursive = not a.no_recursive and cfg.get("recursive", True)
    clear_others = not a.keep_others and cfg.get("clear_unassigned", True)
    hardlink = a.hardlink or cfg.get("hardlink", False)
    seed = a.seed if a.seed is not None else cfg.get("seed")
    match_key = a.match_key if a.match_key is not None else cfg.get("match_key")
    if match_key not in (None, False, True, "random"):
        normalized = normalize_key_input(match_key)
        if not normalized:
            ap.error(msg("err_key", key=match_key))
        match_key = normalized
    pad_numbering = a.pad_numbering or cfg.get("pad_numbering", False)

    pads = {}
    for spec, folders in cfg.get("pads", {}).items():
        for p in parse_pad_spec(spec):
            pads.setdefault(p, []).extend(
                Path(f).expanduser() for f in ([folders] if isinstance(folders, str) else folders))
    for item in a.pad:
        spec, _, folder = item.partition("=")
        for p in parse_pad_spec(spec):
            pads.setdefault(p, []).append(Path(folder).expanduser())

    mutegroups = {}
    for spec, g in cfg.get("mute_groups", {}).items():
        for p in parse_pad_spec(spec):
            mutegroups[p] = int(g)
    for item in a.mute:
        spec, _, g = item.partition("=")
        for p in parse_pad_spec(spec):
            mutegroups[p] = int(g)
    if any(not 0 <= g <= 32 for g in mutegroups.values()):
        ap.error(msg("err_choke"))

    if not pads:
        ap.error(msg("err_no_pads"))
    tp = Path(template_path).expanduser() if template_path else None
    if tp is None:
        template = default_template()
    elif not tp.is_file():
        bases = [Path(__file__).resolve().parent]
        if a.config:
            bases.insert(0, Path(a.config).expanduser().resolve().parent)
        for b in bases:
            if (b / tp).is_file():
                tp = b / tp
                break
        else:
            sys.exit(msg("err_tpl", tpl=template_path, cwd=Path.cwd()))
    if tp is not None:
        template = tp.read_text(encoding="utf-8")
    max_pad = len(INSTR_RE.findall(template))
    bad = [p for p in pads if not 1 <= p <= max_pad]
    if bad:
        ap.error(msg("err_range", max=max_pad, bad=bad))

    try:
        generate_kits(template, output, n_kits, name_pat, pads, clear_others=clear_others,
                      recursive=recursive, hardlink=hardlink, seed=seed,
                      mutegroups=mutegroups or None, match_key=match_key,
                      pad_numbering=pad_numbering)
    except FileNotFoundError as e:
        sys.exit(str(e))


if __name__ == "__main__":
    main()
