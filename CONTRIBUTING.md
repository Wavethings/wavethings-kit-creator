# Contributing · Colaborar

**English · [Español](#español)**

Thanks for helping improve Wavethings Kit Creator! Bug reports, ideas and pull requests are all welcome.

## Reporting a bug or suggesting something
Open an [issue](../../issues) and fill in the template. For bugs, please include your OS, Python version, MPC model and
software version, and the error message. On macOS the app log is in
`~/Library/Application Support/Wavethings Kit Creator/log.txt`.

## Working on the code
The project uses only the Python standard library (Python 3.9+): please do not add dependencies.

    python3 -m unittest discover -s tests -v     # run the tests
    python3 kit_creator_gui.py                   # run the interface

- `mpc_kit_creator.py`: the engine and command-line tool (`.xpm` generation).
- `kit_creator_gui.py`: the local web interface (Python server + one embedded HTML/JS page).
- `build_app.py`: builds the macOS `.app`.
- `tests/`: automated tests. New behavior should come with a test.

### Adding or fixing a translation
- Interface text: in `kit_creator_gui.py`, copy the `en:{ ... }` block inside `I18N`, translate the values (keep the
  keys), register the language in the `<select id="lang">` and in the language detection near the end of the script.
- Command-line messages: add a block to `MSG` in `mpc_kit_creator.py` (same keys as `en`).
- `python3 -m unittest discover -s tests` checks that all languages have the same keys.

### Pull requests
1. Fork the repository and create a branch.
2. Make your change and make sure the tests pass.
3. Open a pull request describing what changes and why.

By contributing you agree that your contribution is licensed under the [MIT License](LICENSE), like the rest of the
project. The Wavethings name and icon remain covered by [TRADEMARK.md](TRADEMARK.md).

## Español

¡Gracias por ayudar a mejorar Wavethings Kit Creator! Se agradecen informes de errores, ideas y *pull requests*.

- **Errores y sugerencias:** abre un [issue](../../issues) y rellena la plantilla (sistema, versión de Python, modelo de
  MPC y versión de software, y el mensaje de error).
- **Código:** solo biblioteca estándar de Python (3.9+), sin dependencias nuevas. Ejecuta
  `python3 -m unittest discover -s tests -v` antes de proponer cambios; el cambio de comportamiento debe llevar prueba.
- **Traducciones:** copia el bloque `en:{ ... }` de `I18N` en `kit_creator_gui.py` y traduce los valores (mantén las
  claves); para la línea de comandos añade un bloque a `MSG` en `mpc_kit_creator.py`.
- **Licencia:** al contribuir aceptas que tu aportación se publique bajo la [Licencia MIT](LICENSE). El nombre y el icono
  de Wavethings siguen cubiertos por [TRADEMARK.md](TRADEMARK.md).
