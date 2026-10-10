# Changelog

All notable changes to this project are documented here. Format based on
[Keep a Changelog](https://keepachangelog.com/), versions follow [Semantic Versioning](https://semver.org/).

## [1.9.2] - 2026-10-10

### Added
- **All kits and samples in one folder** (checkbox "All kits and samples in one folder", `--flat`
  on the command line, `"flat_output": true` in the config). Instead of a subfolder per kit,
  every `.xpm` and all samples go straight into the output folder, which is easier to browse
  on the MPC (its file browser can filter to show only kits). A sample already there with
  identical content is shared by the kits; a different file with the same name is stored as
  `name_2`, `name_3`…; nothing existing is ever overwritten, so later batches simply add kits.
- **Exclusion keywords**: a keyword starting with `-` (e.g. `-loop`, `-open`) keeps matching
  samples out of a group.
- **Required keywords**: a keyword starting with `+` must be present in the path (`Loop Bass:
  +loop, bass, sub` = loops that also say bass or sub).
- **Subgroups** in the default groups. Melodic is split into Bass, Lead, Pluck, Chord, Stab, Pad,
  Arp, Keys and Acid, and loops into Loop Drums, Loop Bass, Loop Synth, Loop Vocal and Loop FX,
  based on how the two example packs organize their folders. The broad groups (Melodic, Loop)
  still contain their subgroups. Other new groups: FX, Fill, Vocal and Loop.

### Changed
- **Keyword matching is tuned for real sample packs**, which are sorted into folders by role
  (and usually keep loops apart from one-shots). Folder names and file names are now judged
  separately: a folder named after a group is a strong signal ("Drum - Kick - One Shots/…"), a
  file name that names a group wins unless folder and name point elsewhere, and a sample that
  clearly belongs to another group is not shared with this one ("Hats/Open Hat 1" is not a
  closed hihat; a Kick named "Snaplow" is not a snare). Short keywords still match only as
  whole words. Tested on two real packs.
- The key filter only narrows a pad whose samples are mostly tonal (at least half carry a key).
- The default `keyword_groups.txt` has the new groups and exclusions. A file you never edited
  is upgraded automatically (the old one is saved as `keyword_groups.txt.bak`); an edited file
  is left as it is.

## [1.9.1] - 2026-09-28

### Fixed
- **A pad set to a keyword group could end up with an unrelated sample** (a chord on a Snare
  pad). Causes, all fixed:
  - When a group matched nothing in the pack, the pad was filled with *any* sample of the pack.
    It is now left **empty** and reported in the log.
  - A group name that isn't in `keyword_groups.txt` (renamed or deleted) silently used the whole
    pack. It is now skipped, with a warning.
  - The key filter treated a whole pack as "tonal" and could empty its drum pads (which carry no
    key), triggering the fallback above. It now applies per pad: only pads whose matching
    samples carry a key are limited to it. If a melodic pad has nothing in the chosen key it uses
    another key instead of staying empty.
  - Keywords of one or two letters (`sd`, `cp`, `bd`, `hh`) matched inside unrelated words
    ("Subdued" contains "bd"); they now only match as whole words. Longer keywords still match
    anywhere, and now also across separators and camelCase ("Bass_Drum", "HiHat").

### Added
- Each option in a pad's group dropdown shows how many samples of that pack it matches, e.g.
  "Snare (12)", with a ⚠ when it's 0, so a group that finds nothing is visible before generating.

## [1.9.0] - 2026-09-28

### Fixed
- **Pack/keyword filtering could silently ignore subfolders and pick random samples instead.**
  A folder used with a keyword group (or the key filter) is now always scanned recursively,
  regardless of the "include subfolders" setting — matching by subfolder name (how most packs
  are organized) only works if those subfolders actually get scanned. Previously, with that
  setting off, a pack with any loose files at its top level would find no keyword matches there,
  trigger the no-match fallback, and quietly pick from those loose files instead — looking like
  the filter wasn't working at all.

### Added
- **Number samples by pad**, a new option (checkbox in the interface, `--pad-numbering` on the
  command line) that prefixes every output sample with its pad number (e.g. "001_Kick.wav").
  Sorting the kit folder by name then also sorts it by pad, which is what lets samplers with no
  `.xpm` support of their own — Maschine, Battery, several hardware samplers — import a kit too:
  select a bank's 16 files together and drop them onto the sampler's first pad, and it typically
  auto-assigns one file per pad from there, in that order.

## [1.8.0] - 2026-09-28

### Added
- **Colors by keyword group**: click the small dot next to a pad's group dropdown to give that
  group (e.g. "Kick") its own color, used on every pad set to it, wherever it's assigned —
  instead of always matching the folder's color. Stored in `group_colors.json`, next to
  `keyword_groups.txt`. A pad with no color override keeps using its folder's color, as before.
- **"✎ Edit keyword groups" button**, under the folder list, opens `keyword_groups.txt` directly
  in a text editor (TextEdit on macOS) without having to find it in Finder — the file is
  created first if it doesn't exist yet. Also shows how many groups are currently loaded.

## [1.7.0] - 2026-09-28

### Changed
- **Pack / keyword mode now uses named groups picked from a dropdown**, instead of typing
  keywords by hand on each pad. A pad linked to a Pack folder shows a small dropdown listing
  the groups, each a named set of synonyms (e.g. "Kick: kick, kek, kik, bd"). The groups
  themselves live in a plain-text file, `keyword_groups.txt`, in the app's settings folder —
  edit it in any text editor and reload the page; existing pad assignments pick up the edit
  automatically, since each pad stores the group's name rather than a copy of its keywords.
  Ships with a starting set of 7 groups (Kick, Snare, Clap, Closed Hihat, Open Hihat,
  Percusion, Melodic), created the first time the file is needed.
- Removed the free-text keyword prompt and the "auto-map with default keywords" button from
  1.5.0; assigning a pad to a Pack folder now works exactly like an ordinary folder (click to
  link/unlink), with the dropdown appearing once it's linked.

## [1.6.0] - 2026-09-28

### Removed
- The standalone desktop app (`desktop/app.py`, PyInstaller, pywebview, `build-apps.yml`) added
  in 1.4.0. Back to a single, simpler way to run it outside the command line: the browser-based
  interface (`kit_creator_gui.py`), plus the lightweight macOS `.app` wrapper (`build_app.py`)
  that already opens it in the browser with zero extra dependencies.

## [1.5.0] - 2026-09-28

### Added
- **Pack / keyword mode**: mark a folder as a "Pack" and, instead of dragging the whole folder
  onto one pad, assign pads by keyword — each pad only picks samples whose path (file or
  subfolder name) contains one of its keywords. Comes with a default keyword set for a classic
  16-pad drum layout ("🔑 Auto-map with default keywords"), fully editable per pad. Works
  alongside ordinary folders and the key filter on the very same pad.

## [1.4.0] - 2026-09-28

### Added
- **Standalone desktop app** (`desktop/app.py`, built with PyInstaller + pywebview): a real
  "Wavethings Kit Creator.app" for macOS and "Wavethings Kit Creator.exe" for Windows, each a
  single native window with no visible browser. Built automatically on GitHub's own macOS and
  Windows machines by `.github/workflows/build-apps.yml` whenever a release is published, and
  attached to that release — no Python installation needed to run it.
- `requirements-desktop.txt`, `desktop/wavethings.spec` and `scripts/make_desktop_icons.py` to
  build it (either locally or, normally, via that workflow).

### Changed
- `kit_creator_gui.py` now exposes `start_server()` so the desktop app can reuse the exact same
  server the browser-based interface uses, instead of duplicating it.

## [1.3.0] - 2026-09-28

### Added
- **Filter by key**: detects the root note in sample file names (e.g. "Bass_C1", "Synth_Dm",
  "Lead - F#3") and, when enabled, keeps every kit's tonal samples in the same key — fixed or
  random per kit. Folders without a detected key (drums, for example) are never filtered.
  Folder cards show a 🎵 badge with the keys found. The kit name pattern can include "{key}".
  Also available from the command line with `--match-key`.

## [1.2.1] - 2026-09-28

### Fixed
- A long folder name (e.g. "Drum - Hat Closed - One Shot") could stretch its whole pad column
  and push the grid past its panel, because CSS Grid never shrinks a column below its content's
  natural width by default. Pad names are now always truncated to a fixed-size grid.

## [1.2.0] - 2026-09-28

### Added
- **Presets**: save and load named setups (folders, pad assignments, choke groups and colors).

### Changed
- Settings and presets are now always stored in the per-user folder (macOS `~/Library/Application Support/Wavethings Kit Creator/`,
  Windows `%APPDATA%\Wavethings Kit Creator\`, Linux `~/.config/Wavethings Kit Creator/`), also when running from the Terminal, so
  personal folder paths never end up inside the project folder. Existing settings are copied automatically.

## [1.1.0] - 2026-09-28

### Added
- "Clear all" button to remove every folder (and its pad assignments) at once, with a confirmation.

## [1.0.0] - 2026-09-28

First public release as **Wavethings Kit Creator**.

### Added
- Generate classic-format MPC `.xpm` drum kits from folders of samples (one or several folders per pad).
- Browser-based interface with 8 banks (128 pads), bank copy, mix mode and diagonal color stripes for mixed pads.
- Mute groups (choke) and pad colors per folder, with a calibratable color palette.
- Built-in empty template, or bring your own `.xpm`.
- Add many folders at once (multi-select or all subfolders of a parent folder).
- macOS app builder (`build_app.py`) with icon; the app quits when the browser tab is closed.
- English / Español interface and command-line messages.
- Command-line tool and JSON configuration file.
- Automated tests (`python3 -m unittest discover -s tests`).
