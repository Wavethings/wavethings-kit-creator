# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Wavethings
"""Tests for Wavethings Kit Creator. Run with:  python3 -m unittest discover -s tests -v"""
import json, os, random, re, shutil, subprocess, sys, tempfile, threading, unittest, urllib.request, wave
from unittest import mock
import xml.etree.ElementTree as ET
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
_DATA = tempfile.mkdtemp()
os.environ["KITCREATOR_DATA"] = _DATA          # keep the GUI's settings out of the repo
import mpc_kit_creator as kc                   # noqa: E402
import kit_creator_gui as gui                  # noqa: E402


def make_wav(path, frames=200):
    path.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(44100)
        w.writeframes(b"\x01\x00" * frames)


def sample_names(xpm_text):
    root = ET.fromstring(xpm_text)
    return {i.get("number"): i.find("Layers")[0].find("SampleName").text
            for i in root.find("Program/Instruments") if i.find("Layers")[0].find("SampleName").text}


class TemplateAndXpm(unittest.TestCase):
    def setUp(self):
        self.tpl = kc.default_template()

    def test_default_template_is_valid_and_empty(self):
        root = ET.fromstring(self.tpl)
        self.assertEqual(len(root.find("Program/Instruments")), 128)
        self.assertEqual(sample_names(self.tpl), {})
        self.assertEqual(kc.read_pad_colors(self.tpl), [])

    def test_build_xpm_sets_names_and_escapes(self):
        out = kc.build_xpm(self.tpl, "Kit & <One>", {1: "Kick 1", 5: "Snare"}, True)
        self.assertEqual(sample_names(out), {"1": "Kick 1", "5": "Snare"})
        self.assertEqual(ET.fromstring(out).find("Program/ProgramName").text, "Kit & <One>")

    def test_mute_groups(self):
        out = kc.build_xpm(self.tpl, "K", {3: "a", 4: "b", 9: "c"}, True, {3: 1, 4: 1})
        groups = {i.get("number"): i.find("MuteGroup").text for i in ET.fromstring(out).find("Program/Instruments")}
        self.assertEqual((groups["3"], groups["4"], groups["9"], groups["10"]), ("1", "1", "0", "0"))

    def test_pad_colors_roundtrip_and_isolation(self):
        out = kc.set_pad_colors(self.tpl, {1: 0x7F0000, 10: 0x7F0000, 3: 0x007F00})
        self.assertEqual(kc.read_pad_colors(out), [0x7F0000, 0x007F00])
        ET.fromstring(out)                                   # still valid XML
        self.assertEqual(out.count("universalPad"), self.tpl.count("universalPad"))
        # only the color values changed
        a, b = self.tpl.splitlines(), out.splitlines()
        self.assertEqual(len(a), len(b))
        self.assertTrue(all("value" in y for x, y in zip(a, b) if x != y))

    def test_parse_pad_spec(self):
        self.assertEqual(kc.parse_pad_spec("3"), [3])
        self.assertEqual(kc.parse_pad_spec("1-4"), [1, 2, 3, 4])


class Picking(unittest.TestCase):
    def test_folders_have_equal_chance_regardless_of_size(self):
        small = [Path(f"/A/a{i}.wav") for i in range(5)]
        big = [Path(f"/B/b{i}.wav") for i in range(500)]
        cache = {Path("/A"): small, Path("/B"): big}
        c = Counter(str(kc.pick_samples({1: [Path("/A"), Path("/B")]}, cache, random.Random(s))[1])[1]
                    for s in range(1000))
        self.assertTrue(400 < c["A"] < 600, c)

    def test_no_repeats_while_free_samples_remain(self):
        cache = {Path("/A"): [Path(f"/A/{i}.wav") for i in range(3)]}
        r = kc.pick_samples({1: [Path("/A")], 2: [Path("/A")], 3: [Path("/A")]}, cache, random.Random(1))
        self.assertEqual(len(set(r.values())), 3)

    def test_unique_stems(self):
        st = kc.unique_stems({1: Path("/A/kick.wav"), 2: Path("/B/kick.wav")})
        self.assertEqual(sorted(st.values()), ["kick", "kick_2"])


class Generation(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        for i in range(4):
            make_wav(self.tmp / "lib" / "Kicks" / f"k{i}.wav")
            make_wav(self.tmp / "lib" / "Hats" / "sub" / f"h{i}.wav")
        (self.tmp / "lib" / "Kicks" / "._k0.wav").write_bytes(b"junk")   # macOS resource fork: ignored

    def gen(self, out, **kw):
        pads = {1: [self.tmp / "lib" / "Kicks"], 3: [self.tmp / "lib" / "Hats"], 4: [self.tmp / "lib" / "Hats"]}
        logs = []
        kc.generate_kits(kc.default_template(), out, 2, "Kit {n:02d}", pads, log=logs.append,
                         colors={1: 0x7F0000, 3: 0x007F00, 4: 0x007F00}, mutegroups={3: 1, 4: 1}, **kw)
        return logs

    def test_hardlink_really_links_and_reports(self):
        out = self.tmp / "lnk"
        logs = self.gen(out, hardlink=True)
        found = list(out.rglob("k?.wav"))                                   # kicks (picked at random)
        self.assertTrue(found)
        for p in found:                                                     # same file, no extra space
            self.assertTrue(os.path.samefile(p, self.tmp / "lib" / "Kicks" / p.name))
        self.assertTrue(any("linked" in l and "0 copied" in l for l in logs), logs)

    def test_hardlink_that_cannot_link_copies_and_says_why(self):
        out = self.tmp / "cpy"
        with mock.patch.object(kc.os, "link", side_effect=OSError(18, "Invalid cross-device link")):
            logs = self.gen(out, hardlink=True)
        self.assertTrue(list(out.rglob("k?.wav")))                            # still produced
        self.assertEqual(sum("Invalid cross-device link" in l for l in logs), 1)   # warned once, not per file
        self.assertTrue(any("0 linked" in l for l in logs), logs)

    def test_generates_kits_with_samples_next_to_xpm(self):
        out = self.tmp / "out"
        self.gen(out, seed=1)
        kit = out / "Kit 01"
        xpm = (kit / "Kit 01.xpm").read_text(encoding="utf-8")
        names = sample_names(xpm)
        self.assertEqual(set(names), {"1", "3", "4"})
        for n in names.values():
            self.assertTrue((kit / f"{n}.wav").is_file(), n)
        self.assertNotEqual(names["3"], names["4"])          # same folder, different samples

    def test_seed_is_reproducible_and_existing_kits_are_kept(self):
        self.gen(self.tmp / "a", seed=7)
        self.gen(self.tmp / "b", seed=7)
        ra = (self.tmp / "a" / "Kit 01" / "Kit 01.xpm").read_text(encoding="utf-8")
        rb = (self.tmp / "b" / "Kit 01" / "Kit 01.xpm").read_text(encoding="utf-8")
        self.assertEqual(ra, rb)
        self.gen(self.tmp / "a", seed=7)                      # runs again in the same folder
        self.assertTrue((self.tmp / "a" / "Kit 03").is_dir())

    def test_missing_folder_raises(self):
        with self.assertRaises(FileNotFoundError):
            kc.generate_kits(kc.default_template(), self.tmp / "o", 1, "K", {1: [self.tmp / "nope"]}, log=lambda x: None)


class PadNumbering(unittest.TestCase):
    """Prefixing output samples with their pad number, so a kit folder also works with samplers
    that import a selection of files in alphabetical order (Maschine, Battery...)."""

    def test_unique_stems_prefixes_with_zero_padded_pad_number(self):
        chosen = {1: Path("/A/Kick.wav"), 12: Path("/B/Snare.wav"), 100: Path("/C/Hat.wav")}
        stems = kc.unique_stems(chosen, pad_prefix=True)
        self.assertEqual([stems[chosen[p]] for p in (1, 12, 100)], ["001_Kick", "012_Snare", "100_Hat"])

    def test_names_sort_in_pad_order(self):
        chosen = {p: Path(f"/x/{n}.wav") for p, n in ((2, "Zebra"), (10, "Apple"), (1, "Mango"))}
        stems = sorted(kc.unique_stems(chosen, pad_prefix=True).values())
        self.assertEqual(stems, ["001_Mango", "002_Zebra", "010_Apple"])   # pad order, not alphabetical

    def test_without_the_option_names_are_unchanged(self):
        chosen = {1: Path("/A/Kick.wav")}
        self.assertEqual(kc.unique_stems(chosen), {Path("/A/Kick.wav"): "Kick"})

    def test_same_file_on_two_pads_keeps_a_single_copy(self):
        f = Path("/A/Kick.wav")
        stems = kc.unique_stems({1: f, 5: f}, pad_prefix=True)
        self.assertEqual(len(stems), 1)

    def test_generate_kits_files_and_xpm_agree(self):
        tmp = Path(tempfile.mkdtemp())
        make_wav(tmp / "Kicks" / "Deep.wav")
        make_wav(tmp / "Snares" / "Snap.wav")
        kc.generate_kits(kc.default_template(), tmp / "out", 1, "Kit",
                         {1: [tmp / "Kicks"], 5: [tmp / "Snares"]}, seed=1, log=lambda x: None,
                         pad_numbering=True)
        kit = tmp / "out" / "Kit"
        self.assertEqual(sorted(p.name for p in kit.glob("*.wav")), ["001_Deep.wav", "005_Snap.wav"])
        names = sample_names((kit / "Kit.xpm").read_text(encoding="utf-8"))
        self.assertEqual(names, {"1": "001_Deep", "5": "005_Snap"})   # the .xpm still points at real files


class Languages(unittest.TestCase):
    def test_engine_messages_match_between_languages(self):
        self.assertEqual(set(kc.MSG["en"]), set(kc.MSG["es"]))
        for k in kc.MSG["en"]:
            kw = dict(folder="f", n=1, name="k", out="o", max=1, bad=[], tpl="t", cwd="c", key="C", pads="1, 2", pad=1, group="g", why="w", linked=1, copied=1, warp=1, tuned=1, long=1, notempo=1)
            if k != "h_name":
                kc.MSG["en"][k].format(**kw)
                kc.MSG["es"][k].format(**kw)

    def test_gui_translations_are_complete(self):
        page = gui.PAGE
        blocks = re.search(r"\nen:\{(.*?)\},\nes:\{(.*?)\}\};", page, re.S)
        key = r'(?:^|[,{\n])\s*"?([\w.]+)"?:"'
        en, es = (set(re.findall(key, b)) for b in blocks.groups())
        self.assertEqual(en, es)
        used = set(re.findall(r'data-(?:i18n|ph|title)="(\w+)"', page)) | set(re.findall(r'\bt\("([\w.]+)"', page))
        self.assertFalse(used - en, used - en)


class Interface(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.srv = gui.ThreadingHTTPServer(("127.0.0.1", 0), gui.H)
        threading.Thread(target=cls.srv.serve_forever, daemon=True).start()
        cls.base = f"http://127.0.0.1:{cls.srv.server_address[1]}"
        page = urllib.request.urlopen(cls.base + "/").read().decode()
        cls.token = re.search(r'const TOKEN="(\w+)"', page).group(1)
        cls.page = page

    @classmethod
    def tearDownClass(cls):
        cls.srv.shutdown()

    def api(self, path, body=None):
        req = urllib.request.Request(self.base + path, data=None if body is None else json.dumps(body).encode(),
                                     headers={"X-Token": self.token, "Content-Type": "application/json"})
        return json.load(urllib.request.urlopen(req))

    def test_brand_and_version_in_page(self):
        self.assertIn("Wavethings", self.page)
        self.assertIn("v" + kc.__version__, self.page)
        self.assertNotIn("__VERSION__", self.page)

    def test_api_requires_token(self):
        with self.assertRaises(urllib.error.HTTPError) as cm:
            urllib.request.urlopen(self.base + "/api/state")
        self.assertEqual(cm.exception.code, 403)

    def test_presets_save_overwrite_delete_and_persist(self):
        preset = {"folders": [{"id": "f1", "path": "/x/Kicks", "color": 0x7F0000, "mute": 0, "count": 9},
                              {"id": "f2", "path": "/x/Hats", "color": 0x007F00, "mute": 1}],
                  "padmap": {"1": ["f1"], "3": ["f2", "f1"], "4": "f2"}}
        r = self.api("/api/presets", {"action": "save", "name": "  My Trance  ", "preset": preset})
        saved = r["presets"]["My Trance"]                       # name is trimmed
        self.assertEqual(saved["padmap"]["4"], ["f2"])          # old single-id format is normalized
        self.assertNotIn("count", saved["folders"][0])          # only what a preset stores
        self.assertEqual(saved["folders"][1]["mute"], 1)
        self.assertIn("My Trance", self.api("/api/state")["presets"])
        self.assertIn("My Trance", json.loads(gui.PRESETS_FILE.read_text(encoding="utf-8")))   # on disk
        preset["padmap"] = {"1": ["f1"]}
        r = self.api("/api/presets", {"action": "save", "name": "My Trance", "preset": preset})
        self.assertEqual(list(r["presets"]["My Trance"]["padmap"]), ["1"])                     # overwritten
        r = self.api("/api/presets", {"action": "delete", "name": "My Trance"})
        self.assertNotIn("My Trance", r["presets"])

    def test_presets_reject_invalid_requests(self):
        for body in ({"action": "save", "name": "", "preset": {"folders": [], "padmap": {}}},
                     {"action": "save", "name": "x", "preset": {"folders": "no", "padmap": {}}},
                     {"action": "save", "name": "x"},
                     {"action": "explode", "name": "x"}):
            self.assertEqual(self.api("/api/presets", body), {"error": "invalid"}, body)
        self.assertNotIn("x", self.api("/api/state")["presets"])

    def test_generate_through_api(self):
        tmp = Path(tempfile.mkdtemp())
        for i in range(3):
            make_wav(tmp / "Kicks" / f"k{i}.wav")
        st = {"template": "", "output": str(tmp / "out"), "kits": 2, "name": "K {n}", "seed": "1",
              "recursive": True, "clear": True, "hardlink": False, "lang": "en",
              "folders": [{"id": "f1", "path": str(tmp / "Kicks"), "color": 0x7F0000, "mute": 0}],
              "padmap": {"1": ["f1"], "17": ["f1"]}}
        self.assertTrue(self.api("/api/generate", st)["ok"])
        for _ in range(100):
            job = self.api("/api/job")
            if job["done"]:
                break
            threading.Event().wait(0.1)
        self.assertIsNone(job["error"], job)
        self.assertTrue((tmp / "out" / "K 2" / "K 2.xpm").is_file())

    def run_and_read_mutes(self, st):
        self.assertTrue(self.api("/api/generate", st)["ok"])
        for _ in range(100):
            job = self.api("/api/job")
            if job["done"]:
                break
            threading.Event().wait(0.1)
        self.assertIsNone(job["error"], job)
        out = next(Path(st["output"]).rglob("*.xpm")).read_text(encoding="utf-8")
        return {i.get("number"): i.find("MuteGroup").text for i in ET.fromstring(out).find("Program/Instruments")}

    def test_pad_choke_overrides_the_folder_choke_in_a_pack(self):
        tmp = Path(tempfile.mkdtemp())
        for n in ("Closed Hat 1.wav", "Open Hat 1.wav", "Kick 1.wav"):
            make_wav(tmp / "pack" / n)
        st = {"template": "", "output": str(tmp / "out"), "kits": 1, "name": "K", "seed": "1",
              "recursive": True, "clear": True, "hardlink": False, "lang": "en",
              "folders": [{"id": "p", "path": str(tmp / "pack"), "color": 1, "mute": 7, "pack": True}],
              "padmap": {"1": [{"id": "p", "group": "Closed Hihat"}], "2": [{"id": "p", "group": "Open Hihat"}],
                         "3": [{"id": "p", "group": "Kick"}], "4": [{"id": "p", "group": "Kick"}]},
              "padChoke": {"1": 1, "2": 1, "3": 0, "99": 5, "x": 2, "5": 40}}
        m = self.run_and_read_mutes(st)
        # 1,2 = chosen 1; 3 = explicit "no choke" beats the folder's 7; 4 = no override: folder's 7
        self.assertEqual((m["1"], m["2"], m["3"], m["4"]), ("1", "1", "0", "7"))
        self.assertEqual(m["5"], "0")           # out-of-range / unassigned entries are ignored

    def test_pad_choke_cleaning_and_presets(self):
        self.assertEqual(gui.clean_pad_chokes({"1": 2, "2": "3", "3": 33, "-1": 1, "a": 1, "4": None, "05": 1}),
                         {"1": 2, "2": 3})
        self.assertEqual(gui.clean_pad_chokes("nope"), {})
        r = gui.clean_preset({"folders": [], "padmap": {}, "padChoke": {"1": 4, "2": 99}})
        self.assertEqual(r["padChoke"], {"1": 4})
        self.assertEqual(gui.clean_preset({"folders": [], "padmap": {}})["padChoke"], {})    # older presets

    def test_generate_through_api_with_match_key(self):
        tmp = Path(tempfile.mkdtemp())
        make_wav(tmp / "Bass" / "Bass_C1.wav")
        make_wav(tmp / "Bass" / "Bass_D1.wav")
        make_wav(tmp / "Kick" / "k.wav")
        st = {"template": "", "output": str(tmp / "out"), "kits": 2, "name": "K{n}_{key}", "seed": "1",
              "recursive": True, "clear": True, "hardlink": False, "lang": "en", "matchKey": "C",
              "folders": [{"id": "f1", "path": str(tmp / "Bass"), "color": 1, "mute": 0},
                         {"id": "f2", "path": str(tmp / "Kick"), "color": 2, "mute": 0}],
              "padmap": {"1": ["f1"], "2": ["f2"]}}
        self.assertTrue(self.api("/api/generate", st)["ok"])
        for _ in range(100):
            job = self.api("/api/job")
            if job["done"]:
                break
            threading.Event().wait(0.1)
        self.assertIsNone(job["error"], job)
        xpm = (tmp / "out" / "K1_C" / "K1_C.xpm").read_text(encoding="utf-8")
        self.assertIn("Bass_C1", sample_names(xpm)["1"])

    def test_generate_through_api_with_pad_numbering(self):
        tmp = Path(tempfile.mkdtemp())
        make_wav(tmp / "Kicks" / "k.wav")
        st = {"template": "", "output": str(tmp / "out"), "kits": 1, "name": "K", "seed": "1",
              "recursive": True, "clear": True, "hardlink": False, "lang": "en", "padNumbering": True,
              "folders": [{"id": "f1", "path": str(tmp / "Kicks"), "color": 1, "mute": 0}],
              "padmap": {"7": ["f1"]}}
        self.assertTrue(self.api("/api/generate", st)["ok"])
        for _ in range(100):
            job = self.api("/api/job")
            if job["done"]:
                break
            threading.Event().wait(0.1)
        self.assertIsNone(job["error"], job)
        self.assertTrue((tmp / "out" / "K" / "007_k.wav").is_file())

    def test_generate_through_api_with_flat_output(self):
        tmp = Path(tempfile.mkdtemp())
        make_wav(tmp / "Kicks" / "k.wav")
        st = {"template": "", "output": str(tmp / "out"), "kits": 3, "name": "K{n}", "seed": "1",
              "recursive": True, "clear": True, "hardlink": False, "lang": "en", "flatOutput": True,
              "folders": [{"id": "f1", "path": str(tmp / "Kicks"), "color": 1, "mute": 0}],
              "padmap": {"1": ["f1"]}}
        self.assertTrue(self.api("/api/generate", st)["ok"])
        for _ in range(100):
            job = self.api("/api/job")
            if job["done"]:
                break
            threading.Event().wait(0.1)
        self.assertIsNone(job["error"], job)
        self.assertEqual(sorted(x.name for x in (tmp / "out").iterdir()), ["K1.xpm", "K2.xpm", "K3.xpm", "k.wav"])

    def test_unknown_group_is_skipped_with_a_warning_not_the_whole_pack(self):
        tmp = Path(tempfile.mkdtemp())
        for i in range(3):
            make_wav(tmp / "Pack" / "Keys" / f"EP Chord {i}.wav")
        st = {"template": "", "output": str(tmp / "out"), "kits": 1, "name": "K", "seed": "1",
              "recursive": True, "clear": True, "hardlink": False, "lang": "en",
              "folders": [{"id": "f1", "path": str(tmp / "Pack"), "color": 1, "mute": 0, "pack": True}],
              "padmap": {"1": [{"id": "f1", "group": "Snarez"}],     # a name that isn't in the file
                         "2": ["f1"]}}                               # deliberately the whole pack
        self.api("/api/generate", st)
        for _ in range(100):
            job = self.api("/api/job")
            if job["done"]:
                break
            threading.Event().wait(0.1)
        self.assertTrue(any("Snarez" in l and "keyword_groups.txt" in l for l in job["log"]), job["log"])
        names = sample_names((tmp / "out" / "K" / "K.xpm").read_text(encoding="utf-8"))
        self.assertEqual(set(names), {"2"})     # pad 1 stays empty instead of getting a random sample

    def test_scan_reports_how_many_samples_each_group_matches(self):
        tmp = Path(tempfile.mkdtemp())
        make_wav(tmp / "Pack" / "Drums" / "Snare" / "a.wav")
        make_wav(tmp / "Pack" / "Drums" / "Snare" / "b.wav")
        make_wav(tmp / "Pack" / "Keys" / "EP Chord 1.wav")
        r = self.api("/api/scan", {"path": str(tmp / "Pack"), "recursive": False, "pack": True})
        self.assertEqual(r["count"], 3)                      # a pack is scanned in depth regardless
        self.assertEqual(r["groupCounts"]["Snare"], 2)
        self.assertEqual(r["groupCounts"]["Kick"], 0)        # nothing matches: the interface flags it
        self.assertEqual(self.api("/api/scan", {"path": str(tmp / "Pack"), "recursive": True})["groupCounts"], {})

    def test_generate_through_api_with_pack_keywords(self):
        tmp = Path(tempfile.mkdtemp())
        for i in range(3):
            make_wav(tmp / "Pack" / "Kick" / f"{i}.wav")
        for i in range(2):
            make_wav(tmp / "Pack" / "Snare" / f"{i}.wav")
        st = {"template": "", "output": str(tmp / "out"), "kits": 1, "name": "K", "seed": "1",
              "recursive": True, "clear": True, "hardlink": False, "lang": "en",
              "folders": [{"id": "f1", "path": str(tmp / "Pack"), "color": 1, "mute": 0, "pack": True}],
              "padmap": {"1": [{"id": "f1", "group": "Kick"}], "2": [{"id": "f1", "group": "Snare"}]}}
        self.assertTrue(self.api("/api/generate", st)["ok"])
        for _ in range(100):
            job = self.api("/api/job")
            if job["done"]:
                break
            threading.Event().wait(0.1)
        self.assertIsNone(job["error"], job)
        xpm = (tmp / "out" / "K" / "K.xpm").read_text(encoding="utf-8")
        names = sample_names(xpm)
        self.assertEqual(set(names), {"1", "2"})
        for stem in names.values():
            self.assertTrue((tmp / "out" / "K" / f"{stem}.wav").is_file())


class DataFolder(unittest.TestCase):
    def test_env_override_and_user_folder(self):
        self.assertEqual(gui.default_data_dir(), Path(os.environ["KITCREATOR_DATA"]))
        old = os.environ.pop("KITCREATOR_DATA")
        try:
            d = gui.default_data_dir()
            self.assertEqual(d.name, "Wavethings Kit Creator")
            self.assertNotEqual(d.parent, ROOT)              # never inside the project folder
        finally:
            os.environ["KITCREATOR_DATA"] = old


class KeyDetection(unittest.TestCase):
    def test_parse_key_recognizes_common_patterns(self):
        cases = {
            "Bass_C1.wav": "C", "Synth_Dm.wav": "Dm", "Lead - F#3.wav": "F#",
            "Chord_Bb2.wav": "A#", "Pad_G#m.wav": "G#m", "loop_Db_120bpm.wav": "C#",
            "Arp(Em).wav": "Em", "Chord - Amin.wav": "Am", "G3_Kick.wav": "G",
            "808_A#0.wav": "A#", "Cmin.wav": "Cm",
        }
        for name, expected in cases.items():
            self.assertEqual(kc.parse_key(name), expected, name)

    def test_parse_key_avoids_obvious_false_positives(self):
        for name in ("Kick.wav", "Snare_Room.wav", "01_Kick.wav", "e_piano.wav",
                     "epic_riser.wav", "Hat Closed.wav", "Marimba.wav"):
            self.assertIsNone(kc.parse_key(name), name)

    def test_flats_and_sharps_group_together(self):
        self.assertEqual(kc.parse_key("Db.wav"), kc.parse_key("C#.wav"))
        self.assertEqual(kc.parse_key("Bb.wav"), kc.parse_key("A#.wav"))

    def test_lowercase_letter_is_not_a_note(self):
        # the note letter must be uppercase, like virtually every sample pack tags keys
        self.assertIsNone(kc.parse_key("bass_c1.wav"))

    def test_normalize_key_input(self):
        self.assertEqual(kc.normalize_key_input("Dbm"), "C#m")
        self.assertEqual(kc.normalize_key_input(" f# "), "F#")
        self.assertEqual(kc.normalize_key_input("Gmin"), "Gm")
        self.assertIsNone(kc.normalize_key_input("Zx"))
        self.assertIsNone(kc.normalize_key_input(""))

    def test_index_by_key_groups_files_and_skips_untagged(self):
        files = [Path("Bass_C1.wav"), Path("Bass_C#1.wav"), Path("Kick.wav")]
        idx = kc.index_by_key(files)
        self.assertEqual(set(idx), {"C", "C#"})
        self.assertEqual(idx["C"], [Path("Bass_C1.wav")])


class KeyFilteredPicking(unittest.TestCase):
    def setUp(self):
        self.bass = [Path(f"/Bass/Bass_C{i}.wav") for i in range(2)] + \
                    [Path(f"/Bass/Bass_D{i}.wav") for i in range(2)]
        self.lead = [Path(f"/Lead/Lead_C{i}.wav") for i in range(2)]      # no D here
        self.drums = [Path(f"/Kick/k{i}.wav") for i in range(3)]          # never tagged
        self.cache = {Path("/Bass"): self.bass, Path("/Lead"): self.lead, Path("/Kick"): self.drums}
        self.folder_keys = {Path("/Bass"): kc.index_by_key(self.bass), Path("/Lead"): kc.index_by_key(self.lead)}
        self.tonal = {Path("/Bass"), Path("/Lead")}

    def test_matching_key_is_used_everywhere_it_exists(self):
        pads = {1: [Path("/Bass")], 2: [Path("/Lead")], 3: [Path("/Kick")]}
        for seed in range(100):
            fb = []
            r = kc.pick_samples(pads, self.cache, random.Random(seed), "C", self.folder_keys, self.tonal, fb)
            self.assertIn("_C", r[1].name)
            self.assertIn("_C", r[2].name)
            self.assertEqual(fb, [])   # both tonal folders have a C sample: never falls back

    def test_falls_back_only_for_pads_without_a_match(self):
        pads = {1: [Path("/Bass")], 2: [Path("/Lead")]}
        fallbacks = set()
        for seed in range(100):
            fb = []
            r = kc.pick_samples(pads, self.cache, random.Random(seed), "D", self.folder_keys, self.tonal, fb)
            self.assertIn("_D", r[1].name)     # Bass has D: always filtered
            fallbacks |= set(fb)
        self.assertEqual(fallbacks, {2})       # Lead has no D: always falls back, only pad 2

    def test_non_tonal_folder_is_never_filtered(self):
        pads = {1: [Path("/Kick")]}
        fb = []
        for seed in range(30):
            kc.pick_samples(pads, self.cache, random.Random(seed), "D", self.folder_keys, self.tonal, fb)
        self.assertEqual(fb, [])               # Kick was never restricted, so never "falls back"

    def test_mixing_a_matched_tonal_folder_with_drums_does_not_fall_back(self):
        pads = {1: [Path("/Lead"), Path("/Kick")]}    # Lead has no D, but the pad also has drums
        fb = []
        for seed in range(100):
            kc.pick_samples(pads, self.cache, random.Random(seed), "D", self.folder_keys, self.tonal, fb)
        self.assertEqual(fb, [])               # the drums folder always supplies a candidate

    def test_no_target_key_behaves_like_before(self):
        pads = {1: [Path("/Bass")]}
        a = kc.pick_samples(pads, self.cache, random.Random(5))
        b = kc.pick_samples(pads, self.cache, random.Random(5), None, self.folder_keys, self.tonal)
        self.assertEqual(a, b)


class KeyFilteredGeneration(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        for n in ("C1", "C#1", "D1"):
            make_wav(self.tmp / "Bass" / f"Bass_{n}.wav")
        make_wav(self.tmp / "Lead" / "Lead_C2.wav")     # only C
        for i in range(3):
            make_wav(self.tmp / "Kick" / f"k{i}.wav")   # untagged

    def test_fixed_key_name_placeholder_and_log(self):
        pads = {1: [self.tmp / "Bass"], 2: [self.tmp / "Lead"], 3: [self.tmp / "Kick"]}
        logs = []
        kc.generate_kits(kc.default_template(), self.tmp / "out", 3, "K{n}_{key}", pads,
                         seed=1, log=logs.append, match_key="C")
        for n in (1, 2, 3):
            xpm = (self.tmp / "out" / f"K{n}_C" / f"K{n}_C.xpm").read_text(encoding="utf-8")
            names = sample_names(xpm)
            self.assertIn("C", names["1"].split("_")[-1])
            self.assertIn("C", names["2"].split("_")[-1])
        self.assertTrue(any("tonalidad" not in l and "key C" in l for l in logs) or
                        any("key C" in l for l in logs))

    def test_random_per_kit_picks_from_available_keys_only(self):
        pads = {1: [self.tmp / "Bass"]}
        logs = []
        kc.generate_kits(kc.default_template(), self.tmp / "out2", 20, "K{n:02d}", pads,
                         seed=2, log=logs.append, match_key=True)
        used = {l.split("key ")[1] for l in logs if "key " in l}
        self.assertTrue(used)
        self.assertTrue(used.issubset({"C", "C#", "D"}))

    def test_no_key_detected_disables_filter_without_error(self):
        pads = {1: [self.tmp / "Kick"]}
        logs = []
        kc.generate_kits(kc.default_template(), self.tmp / "out3", 1, "K{n}", pads,
                         seed=1, log=logs.append, match_key="random")
        self.assertTrue((self.tmp / "out3" / "K1" / "K1.xpm").is_file())

    def test_match_key_off_is_a_no_op(self):
        pads = {1: [self.tmp / "Bass"]}
        logs_a, logs_b = [], []
        kc.generate_kits(kc.default_template(), self.tmp / "a", 1, "K", pads, seed=3, log=logs_a.append)
        kc.generate_kits(kc.default_template(), self.tmp / "b", 1, "K", pads, seed=3, log=logs_b.append,
                         match_key=None)
        xa = (self.tmp / "a" / "K" / "K.xpm").read_text(encoding="utf-8")
        xb = (self.tmp / "b" / "K" / "K.xpm").read_text(encoding="utf-8")
        self.assertEqual(xa, xb)


class PackKeywords(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.pack = self.tmp / "Pack"
        for i in range(4):
            make_wav(self.pack / "Drums" / "Kick" / f"{i:03d}.wav")     # matches by subfolder name
        for i in range(3):
            make_wav(self.pack / "Drums" / "Snare" / f"{i:03d}.wav")
        for i in range(2):
            make_wav(self.pack / "Perc" / f"Shaker_{i}.wav")            # matches by file name
        make_wav(self.pack / "random_loop.wav")                        # matches nothing

    def test_matches_keywords_checks_relative_path_case_insensitively(self):
        f = self.pack / "Drums" / "Kick" / "000.wav"
        self.assertTrue(kc.matches_keywords(self.pack, f, ["kick"]))
        self.assertTrue(kc.matches_keywords(self.pack, f, ["KICK"]))
        self.assertFalse(kc.matches_keywords(self.pack, f, ["snare"]))
        self.assertTrue(kc.matches_keywords(self.pack, self.pack / "Perc" / "Shaker_0.wav", ["shaker"]))
        self.assertFalse(kc.matches_keywords(self.pack, self.pack / "random_loop.wav", ["kick", "bd"]))

    def test_pick_samples_restricts_each_pad_to_its_keywords(self):
        files = kc.scan_folder(self.pack, True)
        cache = {self.pack: files}
        pads = {1: [(self.pack, ["kick"])], 2: [(self.pack, ["snare"])], 8: [(self.pack, ["shaker"])]}
        for seed in range(50):
            r = kc.pick_samples(pads, cache, random.Random(seed))
            self.assertIn("Kick", str(r[1]))
            self.assertIn("Snare", str(r[2]))
            self.assertIn("Shaker", str(r[8]))

    def test_pick_samples_leaves_the_pad_empty_when_no_keyword_matches(self):
        # A pad set to "Snare" must never end up with an unrelated sample (a chord, say):
        # when its group matches nothing the pad stays empty and is reported.
        files = kc.scan_folder(self.pack, True)
        cache = {self.pack: files}
        pads = {1: [(self.pack, ["nonexistent_role_xyz"])], 2: [(self.pack, ["snare"])]}
        for seed in range(30):
            um = []
            r = kc.pick_samples(pads, cache, random.Random(seed), unmatched=um)
            self.assertNotIn(1, r)
            self.assertEqual(um, [1])
            self.assertIn("Snare", str(r[2]))

    def test_short_keywords_only_match_whole_words(self):
        pack = self.tmp / "Pack3"
        make_wav(pack / "Subdued Texture.wav")        # "bd" appears inside "Subdued"
        make_wav(pack / "BD_Punch.wav")               # "BD" as a word
        make_wav(pack / "Snare_SD_01.wav")
        self.assertFalse(kc.matches_keywords(pack, pack / "Subdued Texture.wav", ["bd"]))
        self.assertTrue(kc.matches_keywords(pack, pack / "BD_Punch.wav", ["bd"]))
        self.assertTrue(kc.matches_keywords(pack, pack / "Snare_SD_01.wav", ["sd"]))
        self.assertTrue(kc.matches_keywords(pack, pack / "BD_Punch.wav", ["BD"]))   # case-insensitive

    def test_keywords_match_across_separators_camelcase_and_plurals(self):
        pack = self.tmp / "Pack4"
        for n in ("Bass_Drum_01.wav", "HiHatClosed.wav", "Toms/Tom1.wav", "Kicks/x.wav"):
            make_wav(pack / n)
        self.assertTrue(kc.matches_keywords(pack, pack / "Bass_Drum_01.wav", ["bass drum"]))
        self.assertTrue(kc.matches_keywords(pack, pack / "HiHatClosed.wav", ["hat"]))
        self.assertTrue(kc.matches_keywords(pack, pack / "Toms" / "Tom1.wav", ["tom"]))
        self.assertTrue(kc.matches_keywords(pack, pack / "Kicks" / "x.wav", ["kick"]))

    def test_key_filter_never_empties_the_drum_pads_of_a_tonal_pack(self):
        pack = self.tmp / "Pack5"
        for n in ("Lead_C.wav", "Lead_D.wav"):
            make_wav(pack / "Synth" / n)
        for i in range(3):
            make_wav(pack / "Drums" / f"Snare {i}.wav")
        files = kc.scan_folder(pack, True)
        cache, keys, tonal = {pack: files}, {pack: kc.index_by_key(files)}, {pack}
        pads = {1: [(pack, ["snare"])], 2: [(pack, ["lead"])]}
        for seed in range(100):
            um = []
            r = kc.pick_samples(pads, cache, random.Random(seed), "C", keys, tonal, [], um)
            self.assertIn("Snare", str(r[1]))      # keyless drums are not touched by the key
            self.assertIn("Lead_C", str(r[2]))     # the melodic pad is held to the key
            self.assertEqual(um, [])

    def test_pack_folder_mixed_with_plain_folder_on_one_pad(self):
        drums = self.pack / "Drums"
        files = kc.scan_folder(self.pack, True)
        cache = {self.pack: files, drums: kc.scan_folder(drums, True)}
        pads = {1: [(self.pack, ["nonexistent_role_xyz"]), drums]}   # no match in pack, but drums covers it
        fb = []
        for seed in range(30):
            kc.pick_samples(pads, cache, random.Random(seed), fallback=fb)
        self.assertEqual(fb, [])

    def test_generate_kits_with_pack_entries(self):
        pads = {1: [(self.pack, ["kick"])], 2: [(self.pack, ["snare"])]}
        logs = []
        kc.generate_kits(kc.default_template(), self.tmp / "out", 3, "Kit {n}", pads, seed=1, log=logs.append)
        xpm = (self.tmp / "out" / "Kit 1" / "Kit 1.xpm").read_text(encoding="utf-8")
        names = sample_names(xpm)
        self.assertEqual(len(names), 2)
        self.assertTrue((self.tmp / "out" / "Kit 1" / f"{names['1']}.wav").is_file())

    def test_keyword_filtered_folders_are_always_scanned_recursively(self):
        # Regression test: a pack with loose files at its top level (very common in real packs:
        # readme-adjacent demos, bonus one-shots) used to silently ignore its organized
        # subfolders and pick from those loose files instead when "include subfolders" was off,
        # because the keyword match found nothing at the (non-recursive) top level and the
        # fallback-when-no-match safety net kicked in. A folder used with a keyword filter must
        # always be scanned recursively, regardless of the global `recursive` setting.
        # (Uses its own folder, with descriptive file names, so the renamed output still shows
        # which one was picked; self.pack's "000.wav" names don't carry that once copied.)
        pack2 = self.tmp / "Pack2"
        for i in range(3):
            make_wav(pack2 / "Drums" / "Kick" / f"kick_{i}.wav")
            make_wav(pack2 / "Drums" / "Snare" / f"snare_{i}.wav")
        make_wav(pack2 / "Bonus_FX.wav")            # a loose top-level file, unrelated to any group

        pads = {1: [(pack2, ["kick"])], 2: [(pack2, ["snare"])]}
        for seed in range(30):
            r = kc.pick_samples(pads, {pack2: kc.scan_folder(pack2, True)}, random.Random(seed))
            self.assertIn("Kick", str(r[1]))
            self.assertIn("Snare", str(r[2]))

        logs = []
        kc.generate_kits(kc.default_template(), self.tmp / "out_norec", 10, "K{n}", pads,
                         seed=1, log=logs.append, recursive=False)   # global flag OFF
        for n in range(1, 11):
            xpm = (self.tmp / "out_norec" / f"K{n}" / f"K{n}.xpm").read_text(encoding="utf-8")
            names = sample_names(xpm)
            self.assertIn("kick", names["1"].lower(), names)
            self.assertIn("snare", names["2"].lower(), names)

    def test_generate_kits_warns_about_unmatched_pads_in_both_languages(self):
        pads = {5: [(self.pack, ["nonexistent_role_xyz"])]}
        for lang, word in (("en", "left empty"), ("es", "se deja vacío")):
            kc.LANG = lang
            logs = []
            kc.generate_kits(kc.default_template(), self.tmp / f"out_{lang}", 1, "K", pads,
                             seed=1, log=logs.append)
            self.assertTrue(any(word in l for l in logs), logs)
            names = sample_names((self.tmp / f"out_{lang}" / "K" / "K.xpm").read_text(encoding="utf-8"))
            self.assertEqual(names, {})                 # nothing was put on that pad
        kc.LANG = "en"


class KeywordGroupsFile(unittest.TestCase):
    """The plain-text, user-editable keyword-groups file ('Group Name: kw1, kw2, ...')."""

    def test_parses_one_group_per_line(self):
        g = kc.load_keyword_groups("Kick: kick, bd\nSnare: snare, sd\n")
        self.assertEqual(g, {"Kick": ["kick", "bd"], "Snare": ["snare", "sd"]})
        self.assertEqual(list(g), ["Kick", "Snare"])   # dropdown order = file order

    def test_ignores_blank_lines_comments_and_lines_without_a_colon(self):
        g = kc.load_keyword_groups("\n# a comment\nKick: kick\n\nNot a group\n   \n")
        self.assertEqual(g, {"Kick": ["kick"]})

    def test_a_group_with_no_keywords_is_dropped(self):
        self.assertEqual(kc.load_keyword_groups("Empty:\nKick: kick\n"), {"Kick": ["kick"]})

    def test_later_duplicate_name_replaces_the_earlier_one(self):
        self.assertEqual(kc.load_keyword_groups("Kick: a\nKick: b, c\n"), {"Kick": ["b", "c"]})

    def test_strips_whitespace_around_names_and_keywords(self):
        self.assertEqual(kc.load_keyword_groups("  Snare  :  snare , snr  \n"), {"Snare": ["snare", "snr"]})

    def test_default_groups_start_with_the_original_seven(self):
        g = kc.load_keyword_groups(kc.DEFAULT_KEYWORD_GROUPS_TEXT)
        self.assertEqual(list(g)[:7], ["Kick", "Snare", "Clap", "Closed Hihat", "Open Hihat",
                                       "Percusion", "Melodic"])
        for name in ("Bass", "Lead", "Pluck", "Chord", "Stab", "Pad", "FX", "Fill", "Vocal", "Loop",
                     "Loop Drums", "Loop Bass", "Loop Synth", "Loop Vocal", "Loop FX"):
            self.assertIn(name, g)
        self.assertIn("kick", g["Kick"])
        self.assertIn("-loop", g["Kick"])
        self.assertIn("vibraphone", g["Melodic"])


class GuiPackSupport(unittest.TestCase):
    """Server-side plumbing for pack/keyword mode: the padmap entry shape, preset round-trip,
    and the keyword-groups file exposed to the interface. The click-to-assign flow and the
    per-pad dropdown are client-side JavaScript and aren't covered by this Python suite."""

    def test_clean_padmap_entry_normalizes_shapes(self):
        self.assertEqual(gui.clean_padmap_entry("f1"), "f1")
        self.assertEqual(gui.clean_padmap_entry({"id": "f1", "group": " Kick "}),
                         {"id": "f1", "group": "Kick"})
        self.assertEqual(gui.clean_padmap_entry({"id": "f1", "group": ""}), "f1")   # no group: plain id
        self.assertEqual(gui.clean_padmap_entry({"id": "f1"}), "f1")

    def test_clean_preset_keeps_pack_flag_and_group(self):
        preset = {"folders": [{"id": "f1", "path": "/x", "color": 1, "mute": 0, "pack": True}],
                  "padmap": {"1": [{"id": "f1", "group": "Kick"}], "2": ["f1"]}}
        cleaned = gui.clean_preset(preset)
        self.assertTrue(cleaned["folders"][0]["pack"])
        self.assertEqual(cleaned["padmap"]["1"], [{"id": "f1", "group": "Kick"}])
        self.assertEqual(cleaned["padmap"]["2"], ["f1"])

    def test_load_groups_creates_the_file_with_the_defaults_on_first_use(self):
        data_dir = Path(tempfile.mkdtemp())
        old_file = gui.GROUPS_FILE
        gui.GROUPS_FILE = data_dir / "keyword_groups.txt"
        try:
            self.assertFalse(gui.GROUPS_FILE.exists())
            groups = gui.load_groups()
            self.assertTrue(gui.GROUPS_FILE.is_file())
            self.assertIn("Kick", groups)
            # editing the file is picked up on the next call, with no caching/restart needed
            gui.GROUPS_FILE.write_text("Custom: foo, bar\n", encoding="utf-8")
            self.assertEqual(gui.load_groups(), {"Custom": ["foo", "bar"]})
        finally:
            gui.GROUPS_FILE = old_file


class GroupColors(unittest.TestCase):
    """Per-group color overrides: storage file, round-trip, and how run_job resolves them."""

    def setUp(self):
        self.old_file = gui.GROUP_COLORS_FILE
        gui.GROUP_COLORS_FILE = Path(tempfile.mkdtemp()) / "group_colors.json"

    def tearDown(self):
        gui.GROUP_COLORS_FILE = self.old_file

    def test_missing_file_is_an_empty_dict(self):
        self.assertEqual(gui.load_group_colors(), {})

    def test_store_then_load_round_trips(self):
        gui.store_group_colors({"Kick": 0x7F0000, "Snare": 0x007F00})
        self.assertEqual(gui.load_group_colors(), {"Kick": 0x7F0000, "Snare": 0x007F00})

    def test_run_job_prefers_the_groups_color_over_the_folders(self):
        tmp = Path(tempfile.mkdtemp())
        make_wav(tmp / "Pack" / "Kick" / "0.wav")
        gui.store_group_colors({"Kick": 0x7F0000})
        st = {"template": "", "output": str(tmp / "out"), "kits": 1, "name": "K", "seed": "1",
              "recursive": True, "clear": True, "hardlink": False, "lang": "en",
              "folders": [{"id": "f1", "path": str(tmp / "Pack"), "color": 0x007F00, "mute": 0, "pack": True}],
              "padmap": {"1": [{"id": "f1", "group": "Kick"}]}}
        gui.run_job(st)
        self.assertIsNone(gui.JOB["error"], gui.JOB)
        xpm = (tmp / "out" / "K" / "K.xpm").read_text(encoding="utf-8")
        self.assertEqual(gui.kc.read_pad_colors(xpm), [0x7F0000])

    def test_run_job_falls_back_to_the_folders_color_without_an_override(self):
        tmp = Path(tempfile.mkdtemp())
        make_wav(tmp / "Pack" / "Kick" / "0.wav")
        st = {"template": "", "output": str(tmp / "out"), "kits": 1, "name": "K", "seed": "1",
              "recursive": True, "clear": True, "hardlink": False, "lang": "en",
              "folders": [{"id": "f1", "path": str(tmp / "Pack"), "color": 0x007F00, "mute": 0, "pack": True}],
              "padmap": {"1": [{"id": "f1", "group": "Kick"}]}}
        gui.run_job(st)
        xpm = (tmp / "out" / "K" / "K.xpm").read_text(encoding="utf-8")
        self.assertEqual(gui.kc.read_pad_colors(xpm), [0x007F00])


class GroupColorApi(unittest.TestCase):
    """The /api/group_color and /api/edit_groups endpoints, over real HTTP."""

    @classmethod
    def setUpClass(cls):
        cls.srv = gui.ThreadingHTTPServer(("127.0.0.1", 0), gui.H)
        threading.Thread(target=cls.srv.serve_forever, daemon=True).start()
        cls.base = f"http://127.0.0.1:{cls.srv.server_address[1]}"
        cls.token = re.search(r'const TOKEN="(\w+)"', urllib.request.urlopen(cls.base + "/").read().decode()).group(1)

    @classmethod
    def tearDownClass(cls):
        cls.srv.shutdown()

    def setUp(self):
        self.old_file = gui.GROUP_COLORS_FILE
        gui.GROUP_COLORS_FILE = Path(tempfile.mkdtemp()) / "group_colors.json"

    def tearDown(self):
        gui.GROUP_COLORS_FILE = self.old_file

    def api(self, path, body=None):
        req = urllib.request.Request(self.base + path, data=None if body is None else json.dumps(body).encode(),
                                     headers={"X-Token": self.token, "Content-Type": "application/json"})
        return json.load(urllib.request.urlopen(req))

    def test_set_and_clear_a_group_color(self):
        self.assertEqual(self.api("/api/group_color", {"name": "Kick", "color": 0x7F0000}),
                         {"groupColors": {"Kick": 0x7F0000}})
        self.assertEqual(self.api("/api/state")["groupColors"], {"Kick": 0x7F0000})
        self.assertEqual(self.api("/api/group_color", {"name": "Kick", "color": 0}),
                         {"groupColors": {}})   # color 0 removes the override entirely

    def test_rejects_a_missing_name(self):
        self.assertEqual(self.api("/api/group_color", {"name": "", "color": 1}), {"error": "invalid"})

    def test_edit_groups_creates_the_file_and_does_not_crash(self):
        old = gui.GROUPS_FILE
        gui.GROUPS_FILE = Path(tempfile.mkdtemp()) / "keyword_groups.txt"
        opened = []
        try:
            with mock.patch.object(gui, "open_in_editor", opened.append):    # don't launch an editor
                self.assertEqual(self.api("/api/edit_groups", {}), {"ok": True})
            self.assertTrue(gui.GROUPS_FILE.is_file())   # created before trying to open it
            self.assertEqual(opened, [gui.GROUPS_FILE])
        finally:
            gui.GROUPS_FILE = old


class EditorDispatch(unittest.TestCase):
    """open_in_editor() picks the right command per platform, without actually launching
    anything (subprocess.run / os.startfile are replaced with recorders)."""

    def test_macos_uses_textedit(self):
        calls = []
        old_platform, old_run = gui.sys.platform, gui.subprocess.run
        gui.sys.platform, gui.subprocess.run = "darwin", lambda cmd, **kw: calls.append(cmd)
        try:
            gui.open_in_editor(Path("/tmp/x.txt"))
            self.assertEqual(calls, [["open", "-a", "TextEdit", str(Path("/tmp/x.txt"))]])
        finally:
            gui.sys.platform, gui.subprocess.run = old_platform, old_run

    def test_linux_uses_xdg_open(self):
        calls = []
        old_platform, old_run = gui.sys.platform, gui.subprocess.run
        gui.sys.platform, gui.subprocess.run = "linux", lambda cmd, **kw: calls.append(cmd)
        try:
            gui.open_in_editor(Path("/tmp/x.txt"))
            self.assertEqual(calls, [["xdg-open", str(Path("/tmp/x.txt"))]])
        finally:
            gui.sys.platform, gui.subprocess.run = old_platform, old_run

    def test_windows_uses_startfile(self):
        calls = []
        old_platform = gui.sys.platform
        gui.sys.platform = "win32"
        try:
            # os.startfile only exists on real Windows; patch it (without deleting the real one)
            with mock.patch.object(gui.os, "startfile", lambda p: calls.append(p), create=True):
                gui.open_in_editor(Path("x.txt"))
            self.assertEqual(calls, ["x.txt"])
        finally:
            gui.sys.platform = old_platform


class CommandLine(unittest.TestCase):
    def run_cli(self, *args):
        env = dict(os.environ, PYTHONIOENCODING="utf-8")      # Windows pipes default to cp1252
        return subprocess.run([sys.executable, str(ROOT / "mpc_kit_creator.py"), *args],
                              capture_output=True, text=True, encoding="utf-8", errors="replace", env=env)

    def test_version_and_help(self):
        r = self.run_cli("--version")
        self.assertIn("Wavethings Kit Creator " + kc.__version__, r.stdout + r.stderr)
        self.assertEqual(self.run_cli("--lang", "es", "-h").returncode, 0)

    def test_cli_generates_kit(self):
        tmp = Path(tempfile.mkdtemp())
        make_wav(tmp / "K" / "a.wav")
        r = self.run_cli("--lang", "en", "-o", str(tmp / "o"), "--pad", f"1={tmp / 'K'}")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertTrue((tmp / "o" / "Kit 001" / "Kit 001.xpm").is_file())

    def test_cli_match_key_fixed_and_name_placeholder(self):
        tmp = Path(tempfile.mkdtemp())
        make_wav(tmp / "Bass" / "Bass_C1.wav")
        make_wav(tmp / "Bass" / "Bass_D1.wav")
        r = self.run_cli("--lang", "es", "-o", str(tmp / "o"), "--pad", f"1={tmp / 'Bass'}",
                         "--match-key", "C", "--name", "K{n}_{key}")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertTrue((tmp / "o" / "K1_C" / "K1_C.xpm").is_file(), r.stdout)

    def test_cli_match_key_random_needs_no_value(self):
        tmp = Path(tempfile.mkdtemp())
        make_wav(tmp / "Bass" / "Bass_C1.wav")
        r = self.run_cli("-o", str(tmp / "o"), "--pad", f"1={tmp / 'Bass'}", "--match-key")
        self.assertEqual(r.returncode, 0, r.stderr)

    def test_cli_pad_numbering_flag(self):
        tmp = Path(tempfile.mkdtemp())
        make_wav(tmp / "K" / "a.wav")
        r = self.run_cli("-o", str(tmp / "o"), "--pad", f"3={tmp / 'K'}", "--pad-numbering")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertTrue((tmp / "o" / "Kit 001" / "003_a.wav").is_file())

    def test_cli_flat_flag(self):
        tmp = Path(tempfile.mkdtemp())
        make_wav(tmp / "K" / "a.wav")
        r = self.run_cli("-o", str(tmp / "o"), "-n", "2", "--pad", f"1={tmp / 'K'}", "--flat")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(sorted(x.name for x in (tmp / "o").iterdir()),
                         ["Kit 001.xpm", "Kit 002.xpm", "a.wav"])

    def test_cli_rejects_unrecognized_key(self):
        tmp = Path(tempfile.mkdtemp())
        make_wav(tmp / "Bass" / "a.wav")
        r = self.run_cli("--lang", "en", "-o", str(tmp / "o"), "--pad", f"1={tmp / 'Bass'}",
                         "--match-key", "Zx")
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("Zx", r.stderr)


if __name__ == "__main__":
    unittest.main()


class FlatOutput(unittest.TestCase):
    """Option: every .xpm and sample directly in the output folder (easier to browse on the MPC)."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        for i in range(4):
            make_wav(self.tmp / "src" / "Kicks" / f"kick_{i}.wav", frames=100 + i)
            make_wav(self.tmp / "src" / "Snares" / f"snare_{i}.wav", frames=300 + i)
        self.pads = {1: [self.tmp / "src" / "Kicks"], 2: [self.tmp / "src" / "Snares"]}

    def gen(self, out, n=5, **kw):
        logs = []
        kc.generate_kits(kc.default_template(), out, n, "Kit {n:03d}", self.pads, seed=3,
                         log=logs.append, **kw)
        return logs

    def test_default_is_still_one_folder_per_kit(self):
        self.gen(self.tmp / "o", 2)
        self.assertTrue((self.tmp / "o" / "Kit 001" / "Kit 001.xpm").is_file())

    def test_flat_has_no_subfolders_and_every_sample_exists(self):
        out = self.tmp / "o"
        self.gen(out, flat_output=True)
        self.assertEqual([x for x in out.iterdir() if x.is_dir()], [])
        self.assertEqual(len(list(out.glob("*.xpm"))), 5)
        for xpm in out.glob("*.xpm"):
            for pad, stem in sample_names(xpm.read_text(encoding="utf-8")).items():
                self.assertTrue((out / f"{stem}.wav").is_file(), (xpm.name, stem))

    def test_identical_samples_are_shared_between_kits(self):
        out = self.tmp / "o"
        self.gen(out, 20, flat_output=True)
        self.assertLessEqual(len(list(out.glob("*.wav"))), 8)      # only 8 distinct sources exist

    def test_a_different_file_with_the_same_name_is_never_overwritten(self):
        out = self.tmp / "o"
        make_wav(out / "kick_0.wav", frames=999)                   # unrelated, same name
        before = (out / "kick_0.wav").read_bytes()
        self.gen(out, 10, flat_output=True)
        self.assertEqual((out / "kick_0.wav").read_bytes(), before)
        used = {s for x in out.glob("*.xpm") for s in sample_names(x.read_text(encoding="utf-8")).values()}
        for stem in used:
            self.assertTrue((out / f"{stem}.wav").is_file())
        # whenever the source kick_0 is used, it is stored under another name with its own content
        src = (self.tmp / "src" / "Kicks" / "kick_0.wav").read_bytes()
        renamed = [x for x in out.glob("kick_0_*.wav")]
        for x in renamed:
            self.assertEqual(x.read_bytes(), src)

    def test_a_second_batch_adds_kits_without_overwriting_the_first(self):
        out = self.tmp / "o"
        self.gen(out, 3, flat_output=True)
        first = {x.name: x.read_bytes() for x in out.iterdir()}
        self.gen(out, 3, flat_output=True)
        self.assertEqual(len(list(out.glob("*.xpm"))), 6)
        for name, data in first.items():
            self.assertEqual((out / name).read_bytes(), data)

    def test_flat_with_pad_numbering_and_hardlink(self):
        out = self.tmp / "o"
        self.gen(out, 2, flat_output=True, pad_numbering=True, hardlink=True)
        self.assertTrue((out / "001_kick_0.wav").is_file() or list(out.glob("001_*.wav")))
        self.assertTrue(list(out.glob("002_*.wav")))


class RealPackMatching(unittest.TestCase):
    """Folder + file-name matching, using paths taken from two real sample packs (a hand-sorted
    one with numbered folders, and a loop/one-shot pack with 'Drum - Kick - One Shots' folders)."""

    PACK1 = """01 Kicks/001 Kick Techno F.wav
01 Kicks/808s/a16 808 C.wav
01 Kicks/808s/Kick 808 C.wav
02 Snares and Claps/005 Snare Punchy 5 LOW.wav
02 Snares and Claps/030 Clap 2.wav
02 Snares and Claps/B-sides/hash clap.wav
03 Hats and Cymbals/001 Hat 808 Closed.wav
03 Hats and Cymbals/006 Hat 909 Open.wav
03 Hats and Cymbals/B-sides/Hat open crappy.wav
05 Bass/Bass Sub C.wav
06 Leads and Synths/Lead Saw F.wav
07 Pad/Pad Warm A.wav
08 FX/Riser Long.wav
09 Bonus/Gabe MPC Kit/Kick Sharp.WAV
09 Bonus/Gabe MPC Kit/Hat Tiny.WAV""".splitlines()
    PACK2 = """Drum - Kick - One Shots/DPT_Kick_One_Shot_Cappa.wav
Drum - Snare - One Shots/DPT_Snare_One_Shot_Crackon.wav
Drum - Clap - One Shots/DPT_Clap_One_Shot_Snap.wav
Drum - Hat Closed - One Shot/DPT_Hat_Closed_One_Shot_Afro.wav
Drum - Hat Open - One Shot/DPT_Hat_Open_One_Shot_Whisp.wav
Drum - Kick - One Shots/DPT_Kick_One_Shot_Snaplow.wav
Drum - Snare - Loops/DPT_Snare_Loop_125_Bpm_Dry.wav
Drum - Clap - Loops/DPT_Clap_Loop_125_Bpm_A.wav
Drum - Hat - Loops/DPT_Hat_Loop_125_Bpm_A.wav
Drum - Fill - One Shots/DPT_Snare_Fill_One_Shot_A.wav
Drum - Crash - One Shot/DPT_Crash_One_Shot_A.wav
Drum - Toms - One Shots/DPT_Tom_One_Shot_A.wav
Bass - One Shot/DPT_Bass_One_Shot_C.wav
Bass - Loops/DPT_Bass_Loop_125_Bpm_C.wav
FX - Riser - One Shot/DPT_Riser_One_Shot_A.wav
Synth - Loops/DPT_Synth_Loop_125_Bpm_Am.wav""".splitlines()

    def setUp(self):
        self.groups = kc.load_keyword_groups(kc.DEFAULT_KEYWORD_GROUPS_TEXT)
        self.ctx = tuple(tuple(v) for v in self.groups.values())

    def members(self, group, lines):
        root = Path("/pack")
        files = [root / l for l in lines]
        counts = {}
        res = [f.relative_to(root).as_posix() for f in files
               if kc.matches_keywords(root, f, self.groups[group], self.ctx)]
        return res

    def test_pack1_groups(self):
        k = self.members("Kick", self.PACK1)
        self.assertIn("01 Kicks/001 Kick Techno F.wav", k)
        self.assertIn("09 Bonus/Gabe MPC Kit/Kick Sharp.WAV", k)
        self.assertNotIn("05 Bass/Bass Sub C.wav", k)
        self.assertEqual(self.members("Snare", self.PACK1), ["02 Snares and Claps/005 Snare Punchy 5 LOW.wav"])
        self.assertEqual(self.members("Clap", self.PACK1),
                         ["02 Snares and Claps/030 Clap 2.wav", "02 Snares and Claps/B-sides/hash clap.wav"])
        ch = self.members("Closed Hihat", self.PACK1)
        self.assertIn("03 Hats and Cymbals/001 Hat 808 Closed.wav", ch)
        self.assertIn("09 Bonus/Gabe MPC Kit/Hat Tiny.WAV", ch)
        self.assertNotIn("03 Hats and Cymbals/006 Hat 909 Open.wav", ch)
        self.assertNotIn("03 Hats and Cymbals/B-sides/Hat open crappy.wav", ch)
        oh = self.members("Open Hihat", self.PACK1)
        self.assertEqual(sorted(oh), ["03 Hats and Cymbals/006 Hat 909 Open.wav",
                                      "03 Hats and Cymbals/B-sides/Hat open crappy.wav"])

    def test_pack2_one_shots_never_include_loops_or_other_instruments(self):
        for g, folder in (("Kick", "Kick"), ("Snare", "Snare"), ("Clap", "Clap"),
                          ("Closed Hihat", "Hat Closed"), ("Open Hihat", "Hat Open")):
            res = self.members(g, self.PACK2)
            self.assertTrue(res, g)
            for r in res:
                self.assertIn(folder, r, (g, r))
                self.assertNotIn("Loop", r, (g, r))

    def test_a_kick_whose_name_contains_snap_is_still_a_kick(self):
        self.assertIn("Drum - Kick - One Shots/DPT_Kick_One_Shot_Snaplow.wav", self.members("Kick", self.PACK2))
        self.assertNotIn("Drum - Kick - One Shots/DPT_Kick_One_Shot_Snaplow.wav", self.members("Snare", self.PACK2))

    def test_fills_and_loops_have_their_own_groups(self):
        self.assertEqual(self.members("Fill", self.PACK2), ["Drum - Fill - One Shots/DPT_Snare_Fill_One_Shot_A.wav"])
        self.assertNotIn("Drum - Fill - One Shots/DPT_Snare_Fill_One_Shot_A.wav", self.members("Snare", self.PACK2))
        loops = self.members("Loop", self.PACK2)
        self.assertEqual(len(loops), 5)

    def test_required_keyword_splits_loops_by_type(self):
        files = ["Bass - Loops/DPT_Bass_Loop_125_C.wav", "Synth - Pluck - Loops/DPT_Pluck_Loop_A.wav",
                 "Drum - Hat - Loops/DPT_Hat_Loop_1.wav", "Vocal - Chop - Loops/DPT_Vocal_Chop_Loop_You.wav",
                 "Bass - One Shot/DPT_C_Bass_One_Shot_Gold.wav", "Drum - Kick - One Shots/Kick.wav"]
        self.assertEqual(self.members("Loop Bass", files), [files[0]])
        self.assertEqual(self.members("Loop Synth", files), [files[1]])
        self.assertEqual(self.members("Loop Drums", files), [files[2]])
        self.assertEqual(self.members("Loop Vocal", files), [files[3]])
        self.assertEqual(self.members("Loop", files), files[:4])
        self.assertEqual(self.members("Bass", files), [files[4]])      # not the bass loop

    def test_subgroups_split_melodic_but_melodic_keeps_them(self):
        files = ["06 Leads/Lead Sad C.wav", "06 Leads/Synth Pluck 3 C.wav", "07 Pad/Chords/EP chord 2.wav",
                 "05 Bass/Angry bass/long growl F.wav", "01 Kicks/Kick 808 C.wav"]
        self.assertEqual(self.members("Lead", files), [files[0]])
        self.assertEqual(self.members("Pluck", files), [files[1]])
        self.assertEqual(self.members("Chord", files), [files[2]])
        self.assertEqual(self.members("Bass", files), [files[3]])
        mel = self.members("Melodic", files)
        for f in files[:4]:
            self.assertIn(f, mel)

    def test_exclusion_keyword(self):
        root = Path("/p")
        self.assertTrue(kc.matches_keywords(root, root / "Kick 01.wav", ["kick", "-loop"]))
        self.assertFalse(kc.matches_keywords(root, root / "Loops/Kick 01.wav", ["kick", "-loop"]))

    def test_short_keywords_are_whole_words_only(self):
        root = Path("/p")
        self.assertFalse(kc.matches_keywords(root, root / "Subdued.wav", ["bd"]))
        self.assertTrue(kc.matches_keywords(root, root / "BD 01.wav", ["bd"]))
        self.assertTrue(kc.matches_keywords(root, root / "Hats/Closed 1.wav", ["hat"]))

    def test_group_match_counts(self):
        root = Path("/pack")
        files = [root / l for l in self.PACK1]
        c = kc.group_match_counts(root, files, self.groups)
        self.assertEqual(c["Snare"], 1)
        self.assertEqual(c["Fill"], 0)
        self.assertEqual(list(c), list(self.groups))

    def test_key_filter_does_not_touch_drum_pads_but_narrows_melodic_ones(self):
        tmp = Path(tempfile.mkdtemp())
        for i in range(3):
            make_wav(tmp / "Snares" / f"Snare {i}.wav")
        for k in ("C", "D", "E"):
            make_wav(tmp / "Keys" / f"Piano Chord {k}.wav")
        files = kc.scan_folder(tmp, True)
        cache, keys, tonal = {tmp: files}, {tmp: kc.index_by_key(files)}, {tmp}
        pads = {1: [(tmp, self.groups["Snare"], self.ctx)], 2: [(tmp, self.groups["Melodic"], self.ctx)]}
        for seed in range(30):
            r = kc.pick_samples(pads, cache, random.Random(seed), "D", keys, tonal, [], [])
            self.assertIn("Snare", r[1].name)
            self.assertEqual(r[2].name, "Piano Chord D.wav")


class GroupsFileUpgrade(unittest.TestCase):
    def setUp(self):
        self.old = gui.GROUPS_FILE
        self.dir = Path(tempfile.mkdtemp())
        gui.GROUPS_FILE = self.dir / "keyword_groups.txt"

    def tearDown(self):
        gui.GROUPS_FILE = self.old

    @staticmethod
    def legacy_text():
        return "".join(f"{n}: {', '.join(k)}\n" for n, k in kc.LEGACY_DEFAULT_GROUPS.items())

    def test_untouched_legacy_file_is_upgraded_and_backed_up(self):
        gui.GROUPS_FILE.write_text("# my comment\n" + self.legacy_text(), encoding="utf-8")
        self.assertIn("Loop", gui.load_groups())
        self.assertTrue((self.dir / "keyword_groups.txt.bak").is_file())
        self.assertIn("# my comment", (self.dir / "keyword_groups.txt.bak").read_text(encoding="utf-8"))

    def test_first_192_build_defaults_are_upgraded_too(self):
        gui.GROUPS_FILE.write_text("".join(f"{n}: {', '.join(k)}\n" for n, k in
                                           kc.LEGACY_DEFAULT_GROUPS_192.items()), encoding="utf-8")
        self.assertIn("Loop Bass", gui.load_groups())

    def test_edited_file_is_left_alone(self):
        text = self.legacy_text() + "Snare: snare\n"
        gui.GROUPS_FILE.write_text(text, encoding="utf-8")
        gui.load_groups()
        self.assertEqual(gui.GROUPS_FILE.read_text(encoding="utf-8"), text)
        self.assertFalse((self.dir / "keyword_groups.txt.bak").exists())

    def test_new_file_gets_the_current_defaults(self):
        self.assertEqual(list(gui.load_groups())[-1], "Loop FX")


class MpcColors(unittest.TestCase):
    """Palette of the MPC's own pad colors and reading them from an .xpm saved by MPC 3.x."""

    def mpc3_xpm(self, colors):
        import gzip
        pads = ",\n".join(f'                "value{i}": {c}' for i, c in enumerate(colors))
        doc = ('ACVS\n3.9.1.2\nSerialisableProgramData\njson\nOSX\n{\n "data": {\n "programPads": {\n'
               ' "universalPad": 65280,\n "pads": {\n' + pads + '\n }\n }\n }\n}\n')
        path = Path(tempfile.mkdtemp()) / "Colors.xpm"
        path.write_bytes(gzip.compress(doc.encode("utf-8")))
        return path

    def test_8bit_to_7bit_conversion(self):
        self.assertEqual(kc.mpc3_color_to_classic(0xFF0000), 0x7F0000)
        self.assertEqual(kc.mpc3_color_to_classic(0x00FF00), 0x007F00)
        self.assertEqual(kc.mpc3_color_to_classic(0x0022FF), 0x00117F)
        self.assertEqual(kc.mpc3_color_to_classic(0), 0)

    def test_reads_gzip_json_xpm_and_skips_empty_pads(self):
        path = self.mpc3_xpm([0xFF0000, 0x00FF00, 0xFF0000, 0, 0])
        self.assertEqual(kc.read_color_file(path), [0x7F0000, 0x007F00])

    def test_reads_classic_xpm_too(self):
        path = Path(tempfile.mkdtemp()) / "k.xpm"
        path.write_text(kc.set_pad_colors(kc.default_template(), {1: 0x7F0000, 2: 0x114F68}), encoding="utf-8")
        self.assertEqual(kc.read_color_file(path), [0x7F0000, 0x114F68])

    def test_default_palette_has_the_25_mpc_colors_all_in_range(self):
        pal = gui.DEFAULT_PALETTE
        self.assertEqual(len(pal), 26)                       # 25 MPC colors + the classic petrol
        self.assertEqual(len({v for _, v, _ in pal}), 26)    # no duplicates
        self.assertTrue(all(ok for _, _, ok in pal))
        for _, v, _ in pal:
            self.assertTrue(all(((v >> sh) & 0xFF) <= 0x7F for sh in (0, 8, 16)), hex(v))
        self.assertEqual(pal[0][1], 0x7F0000)


class StartFresh(unittest.TestCase):
    def setUp(self):
        self.old = gui.STATE_FILE
        gui.STATE_FILE = Path(tempfile.mkdtemp()) / "state.json"

    def tearDown(self):
        gui.STATE_FILE = self.old

    def test_previous_session_is_forgotten_but_language_is_kept(self):
        gui.STATE_FILE.write_text(json.dumps({"lang": "es", "folders": [{"id": "a", "path": "/x"}],
                                              "padmap": {"1": ["a"]}, "output": "/o"}))
        gui.fresh_session()
        self.assertEqual(json.loads(gui.STATE_FILE.read_text()), {"lang": "es"})

    def test_theme_is_kept_too_but_only_a_known_one(self):
        gui.STATE_FILE.write_text(json.dumps({"theme": "mpc", "padmap": {"1": ["a"]}}))
        gui.fresh_session()
        self.assertEqual(json.loads(gui.STATE_FILE.read_text()), {"theme": "mpc"})
        gui.STATE_FILE.write_text(json.dumps({"theme": "<script>"}))
        gui.fresh_session()
        self.assertEqual(json.loads(gui.STATE_FILE.read_text()), {})

    def test_works_without_a_previous_state_or_with_a_corrupt_one(self):
        gui.fresh_session()
        self.assertEqual(json.loads(gui.STATE_FILE.read_text()), {})
        gui.STATE_FILE.write_text("{not json")
        gui.fresh_session()
        self.assertEqual(json.loads(gui.STATE_FILE.read_text()), {})

    def test_presets_survive(self):
        old = gui.PRESETS_FILE
        gui.PRESETS_FILE = Path(tempfile.mkdtemp()) / "presets.json"
        try:
            gui.store_presets({"A": {"folders": [], "padmap": {}}})
            gui.fresh_session()
            self.assertIn("A", gui.load_presets())
        finally:
            gui.PRESETS_FILE = old


class LoopsWarpAndKey(unittest.TestCase):
    """Long loops keep their real end, tempo in the name turns Warp on, samples go to the kit's key."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())

    def layer(self, xpm, pad):
        inst = [i for i in ET.fromstring(xpm).find("Program/Instruments") if i.get("number") == str(pad)][0]
        return inst, inst.find("Layers")[0]

    def make(self, rel, frames=200):
        make_wav(self.tmp / rel, frames)
        return self.tmp / rel

    def gen(self, pads, **kw):
        logs = []
        kc.generate_kits(kc.default_template(), self.tmp / "out", 1, "K", pads, log=logs.append, seed=1, **kw)
        return (self.tmp / "out" / "K" / "K.xpm").read_text(encoding="utf-8"), logs

    def test_audio_frames_reads_wav_and_aiff_headers(self):
        self.assertEqual(kc.audio_frames(self.make("a.wav", 1234)), 1234)
        f = self.tmp / "b.aif"                              # 3 frames, 16-bit mono AIFF
        comm = b"COMM" + (18).to_bytes(4, "big") + (1).to_bytes(2, "big") + (3).to_bytes(4, "big") + \
               (16).to_bytes(2, "big") + b"\x40\x0e\xac\x44" + b"\x00" * 6
        f.write_bytes(b"FORM" + (4 + len(comm)).to_bytes(4, "big") + b"AIFF" + comm)
        self.assertEqual(kc.audio_frames(f), 3)
        (self.tmp / "junk.wav").write_bytes(b"nope")
        self.assertIsNone(kc.audio_frames(self.tmp / "junk.wav"))
        self.assertIsNone(kc.audio_frames(self.tmp / "missing.wav"))

    def test_audio_frames_handles_float_and_24_bit_wav(self):
        for bits, fmt in ((24, 1), (32, 3)):
            fmtc = (fmt).to_bytes(2, "little") + (2).to_bytes(2, "little") + (44100).to_bytes(4, "little") + \
                   (44100 * 2 * bits // 8).to_bytes(4, "little") + (2 * bits // 8).to_bytes(2, "little") + bits.to_bytes(2, "little")
            data = b"\0" * (100 * 2 * bits // 8)
            body = b"WAVE" + b"fmt " + len(fmtc).to_bytes(4, "little") + fmtc + b"data" + len(data).to_bytes(4, "little") + data
            f = self.tmp / f"x{bits}.wav"
            f.write_bytes(b"RIFF" + len(body).to_bytes(4, "little") + body)
            self.assertEqual(kc.audio_frames(f), 100)

    def test_parse_bpm(self):
        cases = {"Loop_128bpm.wav": 128, "BPM 140 - Pad.wav": 140, "Drums 174 BPM/x.wav": None, "122-Kick.wav": None,
                 "Pad_90_Bpm_C.wav": 90, "Bass bpm128.wav": 128, "Loop 300bpm.wav": None, "Hat 40 bpm.wav": None}
        for name, want in cases.items():
            self.assertEqual(kc.parse_bpm(name), want, name)
        # a tempo only on the folder counts for long samples, never for one-shots
        self.assertEqual(kc.parse_bpm("Trance 140 BPM/Kicks/k.wav", frames=100), None)
        self.assertEqual(kc.parse_bpm("Trance 140 BPM/Loops/l.wav", frames=400000), 140)

    def test_key_shift(self):
        self.assertEqual([kc.key_shift(a, "C") for a in ("C", "D", "A", "G", "F#", "Am", "Dm")], [0, -2, 3, 5, -6, 3, -2])
        self.assertEqual(kc.key_shift("Bm", "E"), 5)                  # the root in the name decides: B up to E
        self.assertEqual(kc.key_shift("Bm", "Em"), 5)
        self.assertEqual(kc.key_shift("C", "G"), -5)

    def test_long_loop_gets_its_real_end_and_short_ones_keep_the_default(self):
        self.make("L/Long Loop.wav", 400000)
        self.make("S/Short.wav", 5000)
        xpm, logs = self.gen({1: [self.tmp / "L"], 2: [self.tmp / "S"]})
        _, l1 = self.layer(xpm, 1)
        _, l2 = self.layer(xpm, 2)
        self.assertEqual((l1.find("SliceEnd").text, l1.find("SampleEnd").text), ("400000", "400000"))
        self.assertEqual((l2.find("SliceEnd").text, l2.find("SampleEnd").text), ("300000", "0"))
        self.assertTrue(any("long samples with full length: 1" in l for l in logs), logs)

    def test_warp_is_on_for_loops_only_whatever_number_the_name_has(self):
        self.make("L/Synth Loop 128bpm.wav", 330750)        # labelled tempo (7.5 s = 4 bars at 128)
        self.make("L2/DPT_A#_138_Bass_Synth_Loop_Chunk.wav", int(44100 * 240 * 4 / 138))   # bare number
        self.make("L3/Chord Loop 07.wav", int(44100 * 240 * 2 / 100))   # a number that isn't a tempo: use length
        self.make("K/Kick 808 128bpm.wav")                 # not a loop: untouched, even with a bpm in it
        pads = {1: [self.tmp / "L"], 2: [self.tmp / "L2"], 3: [self.tmp / "L3"], 4: [self.tmp / "K"]}
        xpm, logs = self.gen(pads)
        got = {p: (self.layer(xpm, p)[0].find("WarpEnable").text, self.layer(xpm, p)[0].find("WarpTempo").text)
               for p in (1, 2, 3, 4)}
        self.assertEqual(got[1], ("True", "128.000000"))
        self.assertEqual(got[2], ("True", "138.000000"))
        self.assertEqual(got[3], ("True", "100.000000"))
        self.assertEqual(got[4][0], "False")
        self.assertEqual(self.layer(xpm, 1)[0].find("OneShot").text, "True")      # nothing else changed
        shutil.rmtree(self.tmp / "out")
        xpm, _ = self.gen(pads, warp_bpm=False)
        self.assertTrue(all(self.layer(xpm, p)[0].find("WarpEnable").text == "False" for p in (1, 2, 3, 4)))

    def test_a_pad_assigned_a_loop_keyword_group_is_warped_even_without_loop_in_the_name(self):
        self.make("pack/Groove 01.wav", 44100 * 4)            # 4 s = 2 bars at 120
        self.make("pack/Hit.wav", 100)
        kw = (self.tmp / "pack", ["loop", "groove"], None)
        xpm, _ = self.gen({1: [kw]})
        self.assertEqual(self.layer(xpm, 1)[0].find("WarpEnable").text, "True")
        self.assertEqual(self.layer(xpm, 1)[0].find("WarpTempo").text, "120.000000")

    def test_pad_volume_lowers_assigned_pads_only(self):
        self.make("K/Kick.wav")
        xpm, _ = self.gen({1: [self.tmp / "K"]}, pad_volume_db=-6)
        i1, l1 = self.layer(xpm, 1)
        self.assertAlmostEqual(float(i1.find("Volume").text), 0.707946 * 10 ** (-6 / 20), places=5)   # -6 dB
        self.assertEqual(l1.find("Volume").text, "1.000000")                    # the layer is not touched
        self.assertEqual(self.layer(xpm, 2)[0].find("Volume").text, "0.707946")  # an empty pad neither
        shutil.rmtree(self.tmp / "out")
        xpm, _ = self.gen({1: [self.tmp / "K"]})                                 # default: unchanged
        self.assertEqual(self.layer(xpm, 1)[0].find("Volume").text, "0.707946")

    def test_gui_volume_is_clamped(self):
        self.assertEqual([gui.clean_volume_db(v) for v in (-6, "-3", 0, 3, -30, None, "x")], [-6, -3, 0, 0, 0, 0, 0])

    def test_tempo_helpers(self):
        self.assertEqual(kc.estimate_tempo(44100 * 4, 44100), 120.0)          # 2 bars
        self.assertEqual(kc.estimate_tempo(int(44100 * 7.5), 44100), 128.0)    # 4 bars
        self.assertIsNone(kc.estimate_tempo(44100 // 2, 44100))                # too short to be a loop
        self.assertIsNone(kc.estimate_tempo(None, 44100))
        self.assertEqual(kc.loop_tempo("Loop 140bpm.wav"), (140.0, "name"))
        self.assertEqual(kc.loop_tempo("Loop 01.wav"), (None, None))           # nothing to go on
        self.assertTrue(kc.looks_like_loop("Packs/Drum Loops/x.wav"))
        self.assertFalse(kc.looks_like_loop("Packs/Kicks/k.wav"))

    def test_transposes_other_keys_to_the_kit_key_only_on_tonal_pads(self):
        self.make("Bass/Bass_D.wav")            # D -> C = -2
        for n in ("Kick A", "Kick B", "Snare 1", "Snare 2", "Hat 1"):    # names that merely look like a key
            self.make(f"Drums/{n}.wav")
        pads = {1: [self.tmp / "Bass"], 2: [self.tmp / "Drums"]}
        xpm, logs = self.gen(pads, match_key="C", transpose_key=True)
        self.assertEqual(self.layer(xpm, 1)[1].find("TuneCoarse").text, "-2")
        self.assertTrue(any("transposed to the key: 1" in l for l in logs), logs)
        self.assertTrue(any("Transposed to C (pad, semitones): 1 (-2)" in l for l in logs), logs)
        shutil.rmtree(self.tmp / "out")
        xpm, _ = self.gen(pads, match_key="C")          # off: the old behavior, no tuning
        self.assertEqual(self.layer(xpm, 1)[1].find("TuneCoarse").text, "0")

    def test_transposition_draws_from_every_key_not_only_the_kit_key(self):
        for k in ("C", "D", "E", "F", "G", "A"):
            self.make(f"Bass/Bass_{k}.wav")
        tunes = set()
        for seed in range(12):
            out = self.tmp / f"o{seed}"
            kc.generate_kits(kc.default_template(), out, 1, "K", {1: [self.tmp / "Bass"]}, log=lambda x: None,
                             seed=seed, match_key="C", transpose_key=True)
            xpm = (out / "K" / "K.xpm").read_text(encoding="utf-8")
            name = sample_names(xpm)["1"]
            tunes.add((name, self.layer(xpm, 1)[1].find("TuneCoarse").text))
        for name, tune in tunes:            # every sample lands on C
            self.assertEqual(int(tune), kc.key_shift(name.split("_")[1], "C"), name)
        self.assertGreater(len({n for n, _ in tunes}), 2)       # and not just the one already in C


class GroupEditorApi(GroupColorApi):
    """The in-app keyword editor: /api/groups (get / save / reset) and draft counts in /api/scan."""

    def setUp(self):
        super().setUp()
        self.old_groups = gui.GROUPS_FILE
        gui.GROUPS_FILE = Path(tempfile.mkdtemp()) / "keyword_groups.txt"

    def tearDown(self):
        gui.GROUPS_FILE = self.old_groups
        super().tearDown()

    def save(self, *items, renames=None):
        return self.api("/api/groups", {"action": "save", "renames": renames or {},
                                        "groups": [{"name": n, "keywords": k} for n, k in items]})

    def test_get_returns_the_default_groups(self):
        r = self.api("/api/groups", {"action": "get"})
        self.assertIn("Kick", r["groups"])
        self.assertEqual(r["groups"], gui.kc.load_keyword_groups(gui.kc.DEFAULT_KEYWORD_GROUPS_TEXT))

    def test_save_writes_a_file_that_reloads_the_same_and_keeps_the_header(self):
        r = self.save(("Kick", ["kick", "-loop"]), ("Loop Bass", ["+loop", "bass"]))
        self.assertEqual(r["groups"], {"Kick": ["kick", "-loop"], "Loop Bass": ["+loop", "bass"]})
        text = gui.GROUPS_FILE.read_text(encoding="utf-8")
        self.assertTrue(text.startswith("# Wavethings Kit Creator"))
        self.assertEqual(list(gui.kc.load_keyword_groups(text)), ["Kick", "Loop Bass"])    # order kept
        self.assertEqual(self.api("/api/state")["keywordGroups"], r["groups"])

    def test_save_accepts_keywords_as_a_comma_string_and_drops_empty_rows(self):
        r = self.save(("A", "x, y ,"), ("", ""))
        self.assertEqual(r["groups"], {"A": ["x", "y"]})

    def test_save_validates(self):
        self.assertEqual(self.save(("a:b", ["x"])), {"error": "bad_name"})
        self.assertEqual(self.save(("", ["x"])), {"error": "bad_name"})
        self.assertEqual(self.save(("A", ["x"]), ("a", ["y"])), {"error": "dup_name"})
        self.assertEqual(self.save(("A", [])), {"error": "no_keywords"})
        self.assertEqual(self.save(), {"error": "no_groups"})
        self.assertFalse(gui.GROUPS_FILE.exists())            # nothing was written on a rejected save

    def test_rename_moves_the_group_color_and_delete_drops_it(self):
        gui.store_group_colors({"Kick": 5, "Snare": 6})
        r = self.save(("Bombo", ["kick"]), ("Snare", ["snare"]), renames={"Kick": "Bombo"})
        self.assertEqual(r["groupColors"], {"Bombo": 5, "Snare": 6})
        r = self.save(("Bombo", ["kick"]))
        self.assertEqual(r["groupColors"], {"Bombo": 5})

    def test_reset_restores_defaults_and_keeps_a_backup(self):
        self.save(("Only", ["one"]))
        r = self.api("/api/groups", {"action": "reset"})
        self.assertEqual(r["groups"], gui.kc.load_keyword_groups(gui.kc.DEFAULT_KEYWORD_GROUPS_TEXT))
        self.assertIn("Only", gui.GROUPS_FILE.with_name("keyword_groups.txt.bak").read_text(encoding="utf-8"))

    def test_unknown_action_is_rejected(self):
        self.assertEqual(self.api("/api/groups", {"action": "nope"}), {"error": "invalid"})

    def test_scan_counts_unsaved_draft_groups(self):
        pack = Path(tempfile.mkdtemp())
        for n in ("Kick 01.wav", "Kick 02.wav", "Snare 01.wav"):
            (pack / n).write_bytes(b"RIFF")
        r = self.api("/api/scan", {"path": str(pack), "pack": True,
                                   "groups": {"Mine": ["kick"], "Empty": ["zzz"]}})
        self.assertEqual(r["groupCounts"], {"Mine": 2, "Empty": 0})


class ScanApi(unittest.TestCase):
    def test_scan_sees_files_added_after_the_first_scan(self):
        tmp = Path(tempfile.mkdtemp())
        make_wav(tmp / "S" / "a.wav")
        self.assertEqual(len(kc.scan_folder(tmp / "S", True)), 1)
        make_wav(tmp / "S" / "b.wav")
        self.assertEqual(len(kc.scan_folder(tmp / "S", True)), 2)


class DesktopApp(unittest.TestCase):
    """The desktop shell with a fake pywebview (the real windows can't open in the test run)."""

    @classmethod
    def setUpClass(cls):
        sys.path.insert(0, str(ROOT / "desktop"))
        import app
        cls.app = app

    class FakeWindow:
        def __init__(self, result):
            self.result, self.calls = result, []

        def create_file_dialog(self, kind, **kw):
            self.calls.append((kind, kw))
            return self.result

    class FakeWebview:
        class FileDialog:
            OPEN, FOLDER = "open", "folder"

    def picker(self, result):
        w = self.FakeWindow(result)
        return self.app.make_picker(self.FakeWebview, lambda: w), w

    def test_several_folders(self):
        pick, w = self.picker(("/a/Kicks/", "/b/Snares"))
        self.assertEqual(pick("folders", "en"), (["/a/Kicks", "/b/Snares"], None))
        self.assertEqual(w.calls[0], ("folder", {"allow_multiple": True}))

    def test_cancelled_dialogs(self):
        pick, _ = self.picker(None)
        self.assertEqual(pick("folders"), ([], None))
        self.assertEqual(pick("folder"), (None, None))
        self.assertEqual(pick("xpm"), (None, None))

    def test_single_folder_and_xpm_file(self):
        pick, w = self.picker(("C:\\Samples\\Pack\\",))
        self.assertEqual(pick("out"), ("C:\\Samples\\Pack", None))
        self.assertEqual(w.calls[0][0], "folder")
        pick, w = self.picker(("/k/kit.xpm",))
        self.assertEqual(pick("xpm"), ("/k/kit.xpm", None))
        self.assertEqual(w.calls[0][0], "open")
        self.assertIn("XPM files (*.xpm)", w.calls[0][1]["file_types"])

    def test_older_pywebview_constants(self):
        class Old:
            FOLDER_DIALOG, OPEN_DIALOG = "F", "O"
        w = self.FakeWindow(("/x",))
        pick = self.app.make_picker(Old, lambda: w)
        pick("folder")
        self.assertEqual(w.calls[0][0], "F")

    def test_gui_uses_the_installed_picker(self):
        pick, _ = self.picker(("/z/Pack",))
        gui.PICKER = pick
        try:
            self.assertEqual(gui.pick("folder", "es"), ("/z/Pack", None))
        finally:
            gui.PICKER = None
