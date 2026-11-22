# Wallfit

**Find the pictures in a folder that will actually look good as your wallpaper, then set them on exactly the monitors you want, cropped the way you want.**

Wallfit is a small desktop app for Linux, built for KDE Plasma. Point it at a folder of pictures and it shows you which ones are high-resolution enough for your screen, previews how each one will be cropped, and lets you set a picture on a single monitor, including vertical (portrait) monitors.

## Features

- **Resolution filter:** shows only the pictures that have enough pixels for your screen once they're cropped to fill it. Each thumbnail gets a green, yellow or red size badge.
- **Shape matching:** optionally keep only pictures whose shape matches the screen (widescreen, ultrawide, 16:10, vertical…), with an adjustable tolerance.
- **Your real monitors as presets:** on KDE, your connected monitors appear in the screen menu, alongside common landscape, ultrawide and vertical resolutions. **⟲ Rotate** flips any of them between landscape and portrait.
- **Crop-accurate thumbnails:** thumbnails are cropped to your screen's shape, so you see what you'll actually get.
- **Full-screen viewer:** use arrow keys to browse, `S` to shortlist, `C` to toggle the crop preview and `W` to open the wallpaper panel.
- **Shortlist (★):** star the candidates and filter down to just those. Saved for each folder.
- **Per-monitor wallpaper (KDE Plasma 6):**
  - Pick monitors on a map of your actual screen layout.
  - Choose KDE's positioning mode (Scaled and cropped, Scaled, Keep proportions, Centered, Tiled, optionally with a blurred background) with a live preview shaped like the monitor.
  - **Custom crop:** KDE always crops around the centre. Wallfit lets you drag, resize or scroll-zoom a crop box locked to the monitor's shape. A cropped copy is saved to `~/Pictures/Wallpapers/` and the original is never modified.
- **Folder browser:** use KDE's folder dialog or the built-in browser, with recent folders, and optionally include subfolders.
- **Fast:** thumbnails are generated in parallel and cached in `~/.cache/wallfit`, so reopening a folder is instant.

## Requirements

- Linux, with Python 3.9 or newer
- [Pillow](https://python-pillow.org/)
- PyQt6 and PyQt6-WebEngine (for the desktop window)
- **KDE Plasma 6** for per-monitor wallpapers and monitor detection (uses `qdbus6` and `kscreen-doctor`, both included with Plasma). On other desktops, browsing and filtering work fully, and "Set wallpaper" falls back to setting all monitors (GNOME via `gsettings`).

| Distro | Command |
|---|---|
| Arch / CachyOS / Manjaro | `sudo pacman -S python-pillow python-pyqt6-webengine` |
| Debian / Ubuntu | `sudo apt install python3-pil python3-pyqt6.qtwebengine` |
| Fedora | `sudo dnf install python3-pillow python3-pyqt6 python3-pyqt6-webengine` |
| Any (pip) | `pip install --user -r requirements.txt` |

## Install

```bash
git clone https://github.com/reagentking/wallfit.git
cd wallfit
./install.sh
```

This adds **Wallfit** to your application menu (under Graphics) and a `wallfit` command in `~/.local/bin`. Nothing is copied: both point at the cloned folder, so `git pull` updates the app. If you move the folder, run `./install.sh` again.

To remove it, run `./uninstall.sh`. Add `--purge` to also delete the thumbnail cache and saved settings.

## Usage

Launch **Wallfit** from the menu, or:

```bash
wallfit                     # reopens the last folder you used
wallfit ~/Pictures/Walls    # open a specific folder
```

Launching it again while it's open brings the existing window forward (and switches to the folder, if you gave one).

**Browser mode (no Qt needed):** `python3 wallfit.py [FOLDER]` serves the same interface and opens it in your web browser. Press Ctrl+C to stop it.

| Key | Action |
|---|---|
| `←` `→` | Previous / next picture |
| `S` | Add to / remove from shortlist |
| `C` | Toggle crop preview |
| `W` | Open the wallpaper panel |
| `Esc` | Close the viewer |
| `F11` | Full screen (app) |

## How it works

Wallfit's interface is a single web page ([`index.html`](index.html)), served by a small Python server ([`wallfit.py`](wallfit.py)) that reads image sizes, makes thumbnails and talks to KDE. The desktop app ([`wallfit_app.py`](wallfit_app.py)) runs that server privately and shows the page in a Qt WebEngine window.

Wallpapers are set through Plasma's desktop scripting interface (`org.kde.PlasmaShell.evaluateScript`). It writes the same per-monitor settings that *Configure Desktop and Wallpaper…* does, so KDE's own settings always reflect what you picked.

### Security

The server listens on `127.0.0.1` only and accepts requests only from the Wallfit page itself. Each run generates a random token that's delivered in a `SameSite=Strict` cookie and required on every API call. Requests with a foreign `Host` or `Origin` are rejected. This stops websites open in your browser from talking to it.

## Licence

[MIT](LICENSE)
