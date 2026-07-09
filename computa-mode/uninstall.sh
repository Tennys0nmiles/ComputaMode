#!/usr/bin/env bash
set -euo pipefail

# ============================================
# COMPUTA-MODE UNINSTALLER
# ============================================

RED='\033[0;31m'
GREEN='\033[0;32m'
CYAN='\033[0;36m'
YELLOW='\033[0;33m'
NC='\033[0m'

log()  { echo -e "${CYAN}[computa]${NC} $1"; }
ok()   { echo -e "${GREEN}[  OK  ]${NC} $1"; }
warn() { echo -e "${YELLOW}[ WARN ]${NC} $1"; }

echo ""
echo -e "${RED}╔══════════════════════════════════════════╗${NC}"
echo -e "${RED}║  COMPUTA-MODE UNINSTALLER                ║${NC}"
echo -e "${RED}╚══════════════════════════════════════════╝${NC}"
echo ""
echo -e "${YELLOW}This will remove all computa-mode customizations.${NC}"
read -p "Continue? (y/N) " -n 1 -r
echo
if [[ ! $REPLY =~ ^[Yy]$ ]]; then
  echo "Aborted."
  exit 0
fi

# ---- Remove autostart entries ----
log "Removing autostart entries..."
rm -f "$HOME/.config/autostart/computa-listener.desktop"
rm -f "$HOME/.config/autostart/computa-login-sound.desktop"
ok "Autostart entries removed"

# ---- Remove keybinding ----
log "Removing Super+V keybinding..."
KPATH="/org/gnome/settings-daemon/plugins/media-keys/custom-keybindings/computa0/"
EXISTING=$(gsettings get org.gnome.settings-daemon.plugins.media-keys custom-keybindings 2>/dev/null || echo "[]")
# Remove our entry from the list
NEW_LIST=$(echo "$EXISTING" | sed "s|'${KPATH}', ||g" | sed "s|, '${KPATH}'||g" | sed "s|'${KPATH}'||g")
if [ "$NEW_LIST" = "[]" ] || [ "$NEW_LIST" = "['']" ]; then
  NEW_LIST="@as []"
fi
gsettings set org.gnome.settings-daemon.plugins.media-keys custom-keybindings "$NEW_LIST" 2>/dev/null || true
# Reset the keybinding settings
dconf reset -f "/org/gnome/settings-daemon/plugins/media-keys/custom-keybindings/computa0/" 2>/dev/null || true
ok "Keybinding removed"

# ---- Reset GNOME theme settings ----
log "Resetting GNOME settings..."
gsettings reset org.gnome.desktop.interface color-scheme 2>/dev/null || true
gsettings reset org.gnome.desktop.interface gtk-theme 2>/dev/null || true
gsettings reset org.gnome.desktop.interface cursor-theme 2>/dev/null || true
gsettings reset org.gnome.desktop.interface cursor-size 2>/dev/null || true
gsettings reset org.gnome.desktop.interface monospace-font-name 2>/dev/null || true
gsettings reset org.gnome.desktop.background picture-uri 2>/dev/null || true
gsettings reset org.gnome.desktop.background picture-uri-dark 2>/dev/null || true
gsettings reset org.gnome.desktop.screensaver picture-uri 2>/dev/null || true
gsettings reset org.gnome.desktop.screensaver primary-color 2>/dev/null || true
gsettings set org.gnome.shell.extensions.user-theme name '' 2>/dev/null || true
ok "GNOME settings reset"

# ---- Restore GTK CSS ----
log "Restoring GTK CSS..."
for ver in gtk-3.0 gtk-4.0; do
  dest="$HOME/.config/$ver"
  if [ -f "$dest/gtk.css.bak.computa" ]; then
    mv "$dest/gtk.css.bak.computa" "$dest/gtk.css"
  else
    rm -f "$dest/gtk.css"
  fi
done
ok "GTK CSS restored"

# ---- Remove GNOME Shell theme ----
log "Removing GNOME Shell theme..."
rm -rf "$HOME/.local/share/themes/Cyberpunk"
ok "Shell theme removed"

# ---- Remove notification sounds ----
log "Removing custom sounds..."
rm -rf "$HOME/.local/share/sounds/__custom"
ok "Sounds removed"

# ---- Remove GRUB theme ----
log "Removing GRUB theme (requires sudo)..."
if [ -f /etc/default/grub.bak.computa ]; then
  sudo cp /etc/default/grub.bak.computa /etc/default/grub
  sudo rm -f /etc/default/grub.bak.computa
  sudo update-grub 2>/dev/null || sudo grub-mkconfig -o /boot/grub/grub.cfg 2>/dev/null || true
fi
sudo rm -rf /boot/grub/themes/cyberpunk 2>/dev/null || true
ok "GRUB theme removed"

# ---- Remove Plymouth theme ----
log "Removing Plymouth theme (requires sudo)..."
PLYMOUTH_DEST="/usr/share/plymouth/themes/cyberpunk-plymouth"
if [ -d "$PLYMOUTH_DEST" ]; then
  sudo update-alternatives --remove default.plymouth "$PLYMOUTH_DEST/cyberpunk-plymouth.plymouth" 2>/dev/null || true
  sudo rm -rf "$PLYMOUTH_DEST"
  sudo update-initramfs -u 2>/dev/null || true
fi
ok "Plymouth theme removed"

# ---- Remove listener socket ----
log "Removing listener socket..."
rm -f "/run/user/$(id -u)/computa-listener.sock"
ok "Socket removed"

# ---- Note about venv and vosk model ----
echo ""
echo -e "${YELLOW}The following were NOT removed (delete manually if desired):${NC}"
echo -e "  ${CYAN}Python venv:${NC}  $HOME/computa-mode/venv/"
echo -e "  ${CYAN}Vosk model:${NC}   $HOME/.local/share/computa-mode/"
echo -e "  ${CYAN}Cursor theme:${NC} $HOME/.local/share/icons/Bibata-Modern-Ice/"
echo -e "  ${CYAN}Font:${NC}         $HOME/.local/share/fonts/ShareTechMono-Regular.ttf"
echo -e "  ${CYAN}Computa dir:${NC}  $HOME/computa-mode/"
echo ""
echo -e "${GREEN}Computa-mode uninstalled. Log out/in or reboot for full effect.${NC}"
