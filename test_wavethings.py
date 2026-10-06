# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Wavethings
"""Tests for Wavethings Kit Creator. Run with:  python3 -m unittest discover -s tests -v"""
import json, os, random, re, subprocess, sys, tempfile, threading, unittest, urllib.request, wave
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


class Languages(unittest.TestCase):
    def test_engine_messages_match_between_languages(self):
        self.assertEqual(set(kc.MSG["en"]), set(kc.MSG["es"]))
        for k in kc.MSG["en"]:
            kw = dict(folder="f", n=1, name="k", out="o", max=1, bad=[], tpl="t", cwd="c", key="C", pads="1, 2")
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

    def test_pick_samples_falls_back_when_no_keyword_matches(self):
        files = kc.scan_folder(self.pack, True)
        cache = {self.pack: files}
        pads = {1: [(self.pack, ["nonexistent_role_xyz"])]}
        fb = []
        for seed in range(30):
            kc.pick_samples(pads, cache, random.Random(seed), fallback=fb)
        self.assertEqual(fb, [1] * 30)   # always falls back: nothing ever matches that keyword

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

    def test_generate_kits_logs_keyword_fallback_in_both_languages(self):
        pads = {5: [(self.pack, ["nonexistent_role_xyz"])]}
        for lang, word in (("en", "pack"), ("es", "pack")):
            kc.LANG = lang
            logs = []
            kc.generate_kits(kc.default_template(), self.tmp / f"out_{lang}", 1, "K", pads,
                             seed=1, log=logs.append)
            self.assertTrue(any(word in l for l in logs), logs)
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

    def test_default_groups_match_the_seven_groups_shipped(self):
        g = kc.load_keyword_groups(kc.DEFAULT_KEYWORD_GROUPS_TEXT)
        self.assertEqual(list(g), ["Kick", "Snare", "Clap", "Closed Hihat", "Open Hihat",
                                   "Percusion", "Melodic"])
        self.assertIn("kick", g["Kick"])
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
        try:
            self.assertEqual(self.api("/api/edit_groups", {}), {"ok": True})
            self.assertTrue(gui.GROUPS_FILE.is_file())   # created before trying to open it
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
            self.assertEqual(calls, [["open", "-a", "TextEdit", "/tmp/x.txt"]])
        finally:
            gui.sys.platform, gui.subprocess.run = old_platform, old_run

    def test_linux_uses_xdg_open(self):
        calls = []
        old_platform, old_run = gui.sys.platform, gui.subprocess.run
        gui.sys.platform, gui.subprocess.run = "linux", lambda cmd, **kw: calls.append(cmd)
        try:
            gui.open_in_editor(Path("/tmp/x.txt"))
            self.assertEqual(calls, [["xdg-open", "/tmp/x.txt"]])
        finally:
            gui.sys.platform, gui.subprocess.run = old_platform, old_run

    def test_windows_uses_startfile(self):
        calls = []
        old_platform = gui.sys.platform
        gui.sys.platform = "win32"
        gui.os.startfile = lambda p: calls.append(p)   # only exists on real Windows
        try:
            gui.open_in_editor(Path(r"C:\x.txt"))
            self.assertEqual(calls, [r"C:\x.txt"])
        finally:
            gui.sys.platform = old_platform
            del gui.os.startfile


class CommandLine(unittest.TestCase):
    def run_cli(self, *args):
        return subprocess.run([sys.executable, str(ROOT / "mpc_kit_creator.py"), *args],
                              capture_output=True, text=True, encoding="utf-8")

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

    def test_cli_rejects_unrecognized_key(self):
        tmp = Path(tempfile.mkdtemp())
        make_wav(tmp / "Bass" / "a.wav")
        r = self.run_cli("--lang", "en", "-o", str(tmp / "o"), "--pad", f"1={tmp / 'Bass'}",
                         "--match-key", "Zx")
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("Zx", r.stderr)


if __name__ == "__main__":
    unittest.main()
