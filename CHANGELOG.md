# Changelog

All notable changes to this project are documented here. Format based on
[Keep a Changelog](https://keepachangelog.com/), versions follow [Semantic Versioning](https://semver.org/).

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
