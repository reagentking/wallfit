#!/usr/bin/env bash
# Remove what install.sh added. Pass --purge to also delete the thumbnail cache and saved settings.
set -euo pipefail

APPS="${XDG_DATA_HOME:-$HOME/.local/share}/applications"
rm -fv "$APPS/wallfit.desktop" "$HOME/.local/bin/wallfit"
command -v update-desktop-database >/dev/null && update-desktop-database "$APPS" 2>/dev/null || true
command -v kbuildsycoca6 >/dev/null && kbuildsycoca6 >/dev/null 2>&1 || true

if [ "${1:-}" = "--purge" ]; then
  rm -rfv "${XDG_CACHE_HOME:-$HOME/.cache}/wallfit" \
          "${XDG_DATA_HOME:-$HOME/.local/share}/wallfit" \
          "${XDG_CONFIG_HOME:-$HOME/.config}/wallfit"
fi
echo "Wallfit uninstalled. (Cropped wallpapers in ~/Pictures/Wallpapers were left alone.)"
