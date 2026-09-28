<p align="center"><img src="assets/icon.png" width="128" alt="Wavethings Kit Creator icon"></p>

<h1 align="center">Wavethings Kit Creator</h1>

<p align="center">
Generate hundreds of MPC drum kits by assigning whole folders of samples to pads.<br>
Genera cientos de kits de batería para MPC asignando carpetas completas de samples a los pads.
</p>

<p align="center">
<a href="LICENSE"><img alt="License: MIT" src="https://img.shields.io/badge/license-MIT-blue.svg"></a>
<img alt="Python 3.9+" src="https://img.shields.io/badge/python-3.9%2B-blue.svg">
<img alt="No dependencies" src="https://img.shields.io/badge/dependencies-none-brightgreen.svg">
</p>

**English · [Español](#español)**

**Wavethings Kit Creator** is a free, open-source tool by **Wavethings** that builds MPC programs in the classic
`.xpm` format (not the newer `.xtd`) from your own sample folders: pick your folders, click the pads, and get as many
kits as you want, each with different random samples.

## Features
- **One or several folders per pad**: mix different sample packs. Each kit picks a folder at random (equal chance for
  each) and then a random sample from it.
- Add many folders at once (⌘-click, or a parent folder to add all its subfolders).
- 8 banks (A–H, 128 pads) with **copy bank → another bank / all banks**.
- **Mute groups (choke)** per folder, e.g. closed + open hi-hat.
- **Presets**: save and recall your folders, pad assignments, choke groups and colors.
- **Pad colors** per folder; pads that mix folders show diagonal stripes.
- Built-in empty template (no `.xpm` needed), or use your own as a base.
- English / Español interface: starts in your browser's language, switch at the top right.
- Pure Python standard library. No installs, no internet, no tracking. Your samples never leave your computer.

## Requirements
- Python 3.9 or newer. On macOS, if `python3` is missing, run `xcode-select --install`.
- macOS gets native folder dialogs and the `.app` builder. On Windows and Linux the interface works too, but you type
  folder paths instead of picking them.

## Quick start
1. Click **Code → Download ZIP** on this page and unzip it (or `git clone` it).
2. Open a Terminal in that folder and run:

       python3 kit_creator_gui.py

3. Your browser opens the app. Add folders, select one, click pads to assign it, choose an output folder and press
   **Generate kits**.

### Make a double-clickable macOS app
    python3 build_app.py
Drag the generated `Wavethings Kit Creator.app` to Applications. Closing the browser tab quits the app. Run
`build_app.py` again after updating the scripts. Settings and presets are kept in
`~/Library/Application Support/Wavethings Kit Creator/` (Windows: `%APPDATA%`, Linux: `~/.config`); back up `mpc_presets.json` to keep your presets.

### Command line (no interface)
    python3 mpc_kit_creator.py -o ~/MPC_Kits -n 20 \
        --pad 1=~/Samples/Kicks --pad 2=~/Samples/Snares --pad 3-4=~/Samples/Hats --mute 3-4=1
or `python3 mpc_kit_creator.py -c config_example.json`. Use `--lang en|es` to force a language and `-h` for all options.

## Loading kits on the MPC
Each kit is a folder containing the `.xpm` and its samples. Copy the kit folders to your MPC's storage (or your
computer's MPC library) and load the `.xpm` as a drum program.

## Known limits
- Only a pad's first folder decides its color and mute group.
- Colors marked "?" are estimates of the MPC palette. Use *Import colors from an .xpm* to calibrate them with a
  program you colored on your MPC.
- Audio formats: `.wav`, `.aif`, `.aiff`.
- Tested with the classic `.xpm` format (File_Version 2.1). Please report your MPC model and software version if
  something does not load.

## Contributing
Bug reports, ideas and pull requests are welcome. See [CONTRIBUTING.md](CONTRIBUTING.md). Adding a new interface
language only takes translating one block of text.

## License and brand
Code: [MIT License](LICENSE) © 2026 Wavethings. The Wavethings name and icon are not covered by the code license, see
[TRADEMARK.md](TRADEMARK.md). *Not affiliated with or endorsed by Akai Professional or inMusic. "MPC" is a trademark
of its owner.*

---

# Español

**Wavethings Kit Creator** es una herramienta gratuita y de código abierto de **Wavethings** que crea programas de MPC
en el formato clásico `.xpm` (no el nuevo `.xtd`) a partir de tus propias carpetas de samples: eliges las carpetas,
pulsas los pads y obtienes tantos kits como quieras, cada uno con muestras aleatorias distintas.

## Características
- **Una o varias carpetas por pad**: mezcla sample packs. En cada kit se elige una carpeta al azar (misma probabilidad
  para todas) y luego una muestra de ella.
- Añade muchas carpetas a la vez (⌘-clic, o una carpeta madre para añadir todas sus subcarpetas).
- 8 bancos (A–H, 128 pads) con **copia de banco → otro banco / todos**.
- **Grupos de choque (choke)** por carpeta, p. ej. hi-hat cerrado y abierto.
- **Presets**: guarda y recupera tus carpetas, asignaciones de pads, grupos de choque y colores.
- **Colores de pad** por carpeta; los pads con varias carpetas se ven con franjas diagonales.
- Plantilla vacía integrada (no hace falta ningún `.xpm`), o usa la tuya como base.
- Interfaz English / Español: empieza en el idioma del navegador y se cambia arriba a la derecha.
- Solo biblioteca estándar de Python. Sin instalaciones, sin internet, sin rastreo. Tus samples no salen de tu equipo.

## Requisitos
- Python 3.9 o superior. En macOS, si falta `python3`, ejecuta `xcode-select --install`.
- macOS tiene diálogos nativos de carpetas y el creador de la `.app`. En Windows y Linux la interfaz también funciona,
  pero hay que escribir las rutas de las carpetas en vez de elegirlas.

## Inicio rápido
1. Pulsa **Code → Download ZIP** en esta página y descomprime (o usa `git clone`).
2. Abre una Terminal en esa carpeta y ejecuta:

       python3 kit_creator_gui.py

3. Se abre la app en el navegador. Añade carpetas, selecciona una, pulsa pads para asignarla, elige la carpeta de
   salida y pulsa **Generar kits**.

### App de macOS con doble clic
    python3 build_app.py
Arrastra `Wavethings Kit Creator.app` a Aplicaciones. Al cerrar la pestaña del navegador la app se cierra sola. Vuelve
a ejecutar `build_app.py` tras actualizar los scripts. La configuración y los presets se guardan en
`~/Library/Application Support/Wavethings Kit Creator/` (Windows: `%APPDATA%`, Linux: `~/.config`); haz copia de `mpc_presets.json` para conservar tus presets.

### Línea de comandos (sin interfaz)
    python3 mpc_kit_creator.py -o ~/MPC_Kits -n 20 \
        --pad 1=~/Samples/Kicks --pad 2=~/Samples/Snares --pad 3-4=~/Samples/Hats --mute 3-4=1
o `python3 mpc_kit_creator.py -c config_example.json`. `--lang en|es` fuerza el idioma y `-h` muestra todas las opciones.

## Cargar los kits en la MPC
Cada kit es una carpeta con el `.xpm` y sus samples. Copia las carpetas de kits al almacenamiento de tu MPC (o a la
biblioteca de MPC de tu ordenador) y carga el `.xpm` como programa de batería.

## Límites conocidos
- Solo la primera carpeta de un pad decide su color y su grupo de choque.
- Los colores marcados con "?" son estimaciones de la paleta de la MPC. Usa *Importar colores de un .xpm* para
  calibrarlos con un programa que hayas coloreado en tu MPC.
- Formatos de audio: `.wav`, `.aif`, `.aiff`.
- Probado con el formato `.xpm` clásico (File_Version 2.1). Si algo no carga, cuéntanos tu modelo de MPC y versión de
  software.

## Colaborar
Se agradecen informes de errores, ideas y *pull requests*. Consulta [CONTRIBUTING.md](CONTRIBUTING.md). Añadir un
idioma nuevo solo requiere traducir un bloque de texto.

## Licencia y marca
Código: [Licencia MIT](LICENSE) © 2026 Wavethings. El nombre y el icono de Wavethings no están cubiertos por la
licencia del código, consulta [TRADEMARK.md](TRADEMARK.md). *Sin relación con Akai Professional ni inMusic. "MPC" es
una marca de su propietario.*
