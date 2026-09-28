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
            kw = dict(folder="f", n=1, name="k", out="o", max=1, bad=[], tpl="t", cwd="c")
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


if __name__ == "__main__":
    unittest.main()
