#!/usr/bin/env bash
# Install Wallfit for the current user: a start-menu entry and a `wallfit` command.
# Nothing is copied; both point at this folder, so `git pull` updates the app.
set -euo pipefail

DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
APPS="${XDG_DATA_HOME:-$HOME/.local/share}/applications"
BIN="$HOME/.local/bin"

echo "Checking dependencies…"
missing=0
python3 -c 'import PIL' 2>/dev/null || { echo "  ✗ Pillow (python-pillow / python3-pil)"; missing=1; }
python3 -c 'import PyQt6.QtWebEngineWidgets' 2>/dev/null || {
  echo "  ✗ PyQt6 + Qt WebEngine (python-pyqt6-webengine / python3-pyqt6.qtwebengine)"
  echo "    (needed for the desktop window; 'python3 wallfit.py' still works in a browser without it)"
  missing=1
}
if [ "$missing" = 1 ]; then
  echo
  echo "Install the missing packages first, for example:"
  echo "  Arch / CachyOS / Manjaro:  sudo pacman -S python-pillow python-pyqt6-webengine"
  echo "  Debian / Ubuntu:           sudo apt install python3-pil python3-pyqt6.qtwebengine"
  echo "  Fedora:                    sudo dnf install python3-pillow python3-pyqt6 python3-pyqt6-webengine"
  echo "  Any distro (pip):          pip install --user -r requirements.txt"
  exit 1
fi
echo "  ✓ all found"

chmod +x "$DIR/wallfit.py" "$DIR/wallfit_app.py"

mkdir -p "$APPS"
cat > "$APPS/wallfit.desktop" <<EOF
[Desktop Entry]
Type=Application
Name=Wallfit
GenericName=Wallpaper Browser
Comment=Find pictures big enough for your monitors and set them as wallpaper
Exec="$DIR/wallfit_app.py" %f
Icon=preferences-desktop-wallpaper
Terminal=false
Categories=Graphics;Viewer;Qt;KDE;
Keywords=wallpaper;background;desktop;images;pictures;monitor;
StartupNotify=true
StartupWMClass=wallfit
EOF
echo "Installed start-menu entry: $APPS/wallfit.desktop"

mkdir -p "$BIN"
ln -sf "$DIR/wallfit_app.py" "$BIN/wallfit"
echo "Installed command:          $BIN/wallfit"
case ":$PATH:" in *":$BIN:"*) ;; *) echo "  (note: $BIN is not on your PATH, so the 'wallfit' command won't be found until you add it)";; esac

command -v update-desktop-database >/dev/null && update-desktop-database "$APPS" 2>/dev/null || true
command -v kbuildsycoca6 >/dev/null && kbuildsycoca6 >/dev/null 2>&1 || true

echo
echo "Done. Find Wallfit in your app menu, or run: wallfit [FOLDER]"
