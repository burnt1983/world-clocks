# World Clocks

Four round 12-hour faces in one transparent row. The city name sits under each face.

The first four cities are London, New York, Dubai, and Tokyo. Right-click a face to pick another city.

## Install

Needs Python 3, GTK 3, PyGObject, and cairo (`python3-gi`, `python3-gi-cairo`, `gir1.2-gtk-3.0`). Time zones come from the system `tzdata`.

```bash
git clone https://github.com/burnt1983/world-clocks.git
cd world-clocks
bash install.sh
worldclock
```

Cities, colours, and the window place are saved in `~/.config/lee-worldclock/clocks.json`. It does not start at login until you tick Open at login.

## How to use it

| Action | What happens |
|---|---|
| Drag the row | Move it |
| Scroll one face | Resize that face |
| Drag an edge | Resize all four together |
| Right-click a face | City, colour, opacity, size, second hands, open at login |

## Uninstall

```bash
bash uninstall.sh
```

## Licence

MIT. See [LICENSE](LICENSE).
