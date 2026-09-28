# Changelog

All notable changes to this project are documented here. Format based on
[Keep a Changelog](https://keepachangelog.com/), versions follow [Semantic Versioning](https://semver.org/).

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
