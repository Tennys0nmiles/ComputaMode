#!/usr/bin/env bash
set -euo pipefail

# ============================================
# COMPUTA-MODE INSTALLER - Linux/GNOME Edition
# ============================================

COMPUTA_DIR="$(cd "$(dirname "$0")" && pwd)"
VENV_DIR="$COMPUTA_DIR/venv"
VOSK_MODEL_DIR="$HOME/.local/share/computa-mode"
VOSK_MODEL_NAME="vosk-model-small-en-us-0.15"

RED='\033[0;31m'
GREEN='\033[0;32m'
CYAN='\033[0;36m'
MAGENTA='\033[0;35m'
YELLOW='\033[0;33m'
NC='\033[0m'

log()  { echo -e "${CYAN}[computa]${NC} $1"; }
ok()   { echo -e "${GREEN}[  OK  ]${NC} $1"; }
warn() { echo -e "${YELLOW}[ WARN ]${NC} $1"; }
err()  { echo -e "${RED}[ERROR ]${NC} $1"; }

echo ""
echo -e "${CYAN}╔══════════════════════════════════════════╗${NC}"
echo -e "${CYAN}║${NC}  ${MAGENTA}COMPUTA-MODE INSTALLER${NC}                  ${CYAN}║${NC}"
echo -e "${CYAN}║${NC}  ${GREEN}Linux / GNOME / Wayland Edition${NC}          ${CYAN}║${NC}"
echo -e "${CYAN}╚══════════════════════════════════════════╝${NC}"
echo ""

# ---- Step 1: System packages ----
log "Installing system packages (requires sudo)..."
if sudo -n true 2>/dev/null; then
  sudo apt update -qq
  sudo apt install -y \
    python3-venv python3-dev \
    portaudio19-dev libsndfile1-dev \
    ffmpeg \
    gnome-shell-extensions gnome-shell-extension-prefs \
    gnome-tweaks \
    wget unzip uuid-runtime \
    libnotify-bin \
    2>&1 | tail -1
  ok "System packages installed"
else
  warn "No sudo access -- run this first in a terminal:"
  echo "  sudo apt install -y python3-venv python3-dev portaudio19-dev libsndfile1-dev ffmpeg gnome-shell-extensions gnome-shell-extension-prefs gnome-tweaks wget unzip uuid-runtime libnotify-bin"
  echo ""
  read -p "Press Enter after installing packages (or Ctrl+C to abort)... " || true
fi

# ---- Step 2: Python virtual environment ----
log "Setting up Python virtual environment..."
if [ ! -d "$VENV_DIR" ]; then
  python3 -m venv "$VENV_DIR"
fi
source "$VENV_DIR/bin/activate"
pip install --upgrade pip -q
pip install -r "$COMPUTA_DIR/requirements.txt" -q
ok "Python venv ready ($VENV_DIR)"

# ---- Step 3: Download Vosk model ----
log "Checking Vosk speech model..."
if [ ! -d "$VOSK_MODEL_DIR/$VOSK_MODEL_NAME" ]; then
  mkdir -p "$VOSK_MODEL_DIR"
  log "Downloading Vosk model (~40MB)..."
  wget -qO /tmp/vosk-model.zip \
    "https://alphacephei.com/vosk/models/${VOSK_MODEL_NAME}.zip"
  unzip -qo /tmp/vosk-model.zip -d "$VOSK_MODEL_DIR"
  rm -f /tmp/vosk-model.zip
  ok "Vosk model downloaded"
else
  ok "Vosk model already present"
fi

# ---- Step 4: Install fonts ----
log "Installing Share Tech Mono font..."
FONT_DIR="$HOME/.local/share/fonts"
mkdir -p "$FONT_DIR"
if [ ! -f "$FONT_DIR/ShareTechMono-Regular.ttf" ]; then
  wget -qO "$FONT_DIR/ShareTechMono-Regular.ttf" \
    "https://github.com/google/fonts/raw/main/ofl/sharetechmono/ShareTechMono-Regular.ttf" \
    || warn "Failed to download font (non-critical)"
  fc-cache -f 2>/dev/null
fi
ok "Fonts installed"

# ---- Step 5: Install cursor theme ----
log "Installing Bibata-Modern-Ice cursor..."
CURSOR_DIR="$HOME/.local/share/icons/Bibata-Modern-Ice"
if [ ! -d "$CURSOR_DIR" ]; then
  wget -qO /tmp/bibata.tar.xz \
    "https://github.com/ful1e5/Bibata_Cursor/releases/download/v2.0.6/Bibata-Modern-Ice.tar.xz" \
    || warn "Failed to download cursor theme (non-critical)"
  if [ -f /tmp/bibata.tar.xz ]; then
    mkdir -p "$HOME/.local/share/icons"
    tar -xf /tmp/bibata.tar.xz -C "$HOME/.local/share/icons/" 2>/dev/null || true
    rm -f /tmp/bibata.tar.xz
  fi
fi
ok "Cursor theme installed"

# ---- Step 6: Generate visual assets ----
log "Generating cyberpunk wallpaper and visual assets..."
"$VENV_DIR/bin/python3" "$COMPUTA_DIR/computa-wallpaper.py"
ok "Visual assets generated"

# ---- Step 7: Generate sound effects ----
log "Generating cyberpunk sound effects..."
"$VENV_DIR/bin/python3" "$COMPUTA_DIR/computa-sounds.py"
ok "Sound effects generated"

# ---- Step 8: Install GNOME Shell theme ----
log "Installing GNOME Shell theme..."
THEME_DEST="$HOME/.local/share/themes/Cyberpunk/gnome-shell"
mkdir -p "$THEME_DEST"
cp "$COMPUTA_DIR/theme/gnome-shell/gnome-shell.css" "$THEME_DEST/"
if [ -f "$COMPUTA_DIR/theme/gnome-shell/metadata.json" ]; then
  cp "$COMPUTA_DIR/theme/gnome-shell/metadata.json" "$HOME/.local/share/themes/Cyberpunk/"
fi
ok "GNOME Shell theme installed"

# ---- Step 9: Install GTK CSS overrides ----
log "Installing GTK CSS overrides..."
for ver in gtk-3.0 gtk-4.0; do
  dest="$HOME/.config/$ver"
  mkdir -p "$dest"
  src="$COMPUTA_DIR/theme/$ver/gtk.css"
  if [ -f "$src" ]; then
    # Backup existing
    if [ -f "$dest/gtk.css" ] && [ ! -f "$dest/gtk.css.bak.computa" ]; then
      cp "$dest/gtk.css" "$dest/gtk.css.bak.computa"
    fi
    cp "$src" "$dest/gtk.css"
  fi
done
ok "GTK CSS overrides installed"

# ---- Step 10: Register Super+V keybinding ----
log "Registering Super+V hotkey..."
KPATH="/org/gnome/settings-daemon/plugins/media-keys/custom-keybindings/computa0/"

# Get existing custom keybindings and preserve them
EXISTING=$(gsettings get org.gnome.settings-daemon.plugins.media-keys custom-keybindings 2>/dev/null || echo "@as []")
if echo "$EXISTING" | grep -q "computa0"; then
  : # Already registered
else
  # Add our keybinding to the list
  if [ "$EXISTING" = "@as []" ] || [ "$EXISTING" = "[]" ]; then
    NEW_LIST="['${KPATH}']"
  else
    # Remove trailing ] and append
    NEW_LIST=$(echo "$EXISTING" | sed "s/]$/, '${KPATH}']/" | sed "s/\[, /[/")
  fi
  gsettings set org.gnome.settings-daemon.plugins.media-keys custom-keybindings "$NEW_LIST"
fi

gsettings set org.gnome.settings-daemon.plugins.media-keys.custom-keybinding:${KPATH} \
  name "Computa Activate"
gsettings set org.gnome.settings-daemon.plugins.media-keys.custom-keybinding:${KPATH} \
  command "$VENV_DIR/bin/python3 $COMPUTA_DIR/computa-trigger.py"
gsettings set org.gnome.settings-daemon.plugins.media-keys.custom-keybinding:${KPATH} \
  binding "<Super>v"
ok "Super+V hotkey registered"

# ---- Step 11: Apply GNOME theme settings ----
log "Applying GNOME theme settings..."
"$VENV_DIR/bin/python3" "$COMPUTA_DIR/computa-theme.py" all
ok "Theme applied"

# ---- Step 12: Install animated wallpaper player (Hidamari) ----
log "Checking for Flatpak/Hidamari (animated wallpaper)..."
if command -v flatpak &>/dev/null; then
  flatpak remote-add --if-not-exists flathub https://dl.flathub.org/repo/flathub.flatpakrepo 2>/dev/null || true
  if flatpak install -y --noninteractive flathub io.github.jeffshee.Hidamari 2>/dev/null; then
    ok "Hidamari installed (animated wallpaper)"
    echo -e "  ${CYAN}To enable: Open Hidamari and select ${COMPUTA_DIR}/wallpaper/cyberpunk_animated.webm${NC}"
  else
    warn "Hidamari install failed -- using static wallpaper (still looks great)"
  fi
else
  warn "Flatpak not available -- using static wallpaper"
fi

# ---- Step 13: Install GRUB theme ----
log "Installing GRUB boot theme (requires sudo)..."
GRUB_THEME_DIR="/boot/grub/themes/cyberpunk"
if sudo -n true 2>/dev/null && sudo mkdir -p "$GRUB_THEME_DIR" 2>/dev/null; then
  sudo cp -r "$COMPUTA_DIR/grub/cyberpunk-grub/"* "$GRUB_THEME_DIR/"

  # Generate GRUB fonts
  if command -v grub-mkfont &>/dev/null; then
    FONT_FILE="$HOME/.local/share/fonts/ShareTechMono-Regular.ttf"
    if [ -f "$FONT_FILE" ]; then
      sudo grub-mkfont -s 24 -o "$GRUB_THEME_DIR/ShareTechMono24.pf2" "$FONT_FILE" 2>/dev/null || true
      sudo grub-mkfont -s 16 -o "$GRUB_THEME_DIR/ShareTechMono16.pf2" "$FONT_FILE" 2>/dev/null || true
      sudo grub-mkfont -s 14 -o "$GRUB_THEME_DIR/ShareTechMono14.pf2" "$FONT_FILE" 2>/dev/null || true
      sudo grub-mkfont -s 12 -o "$GRUB_THEME_DIR/ShareTechMono12.pf2" "$FONT_FILE" 2>/dev/null || true
      sudo grub-mkfont -s 10 -o "$GRUB_THEME_DIR/ShareTechMono10.pf2" "$FONT_FILE" 2>/dev/null || true
    fi
  fi

  # Backup and update GRUB config
  if [ ! -f /etc/default/grub.bak.computa ]; then
    sudo cp /etc/default/grub /etc/default/grub.bak.computa
  fi
  sudo sed -i '/^GRUB_THEME=/d' /etc/default/grub
  echo "GRUB_THEME=\"$GRUB_THEME_DIR/theme.txt\"" | sudo tee -a /etc/default/grub >/dev/null
  sudo update-grub 2>/dev/null || sudo grub-mkconfig -o /boot/grub/grub.cfg 2>/dev/null || true
  ok "GRUB theme installed"
else
  warn "Could not install GRUB theme (sudo required)"
fi

# ---- Step 14: Install Plymouth theme ----
log "Installing Plymouth boot animation (requires sudo)..."
PLYMOUTH_DEST="/usr/share/plymouth/themes/cyberpunk-plymouth"
if sudo -n true 2>/dev/null && sudo mkdir -p "$PLYMOUTH_DEST/assets" 2>/dev/null; then
  sudo cp "$COMPUTA_DIR/plymouth/cyberpunk-plymouth/cyberpunk-plymouth.plymouth" "$PLYMOUTH_DEST/"
  sudo cp "$COMPUTA_DIR/plymouth/cyberpunk-plymouth/cyberpunk-plymouth.script" "$PLYMOUTH_DEST/"
  sudo cp "$COMPUTA_DIR/plymouth/cyberpunk-plymouth/assets/"*.png "$PLYMOUTH_DEST/assets/" 2>/dev/null || true

  sudo update-alternatives --install /usr/share/plymouth/themes/default.plymouth \
    default.plymouth "$PLYMOUTH_DEST/cyberpunk-plymouth.plymouth" 200 2>/dev/null || true
  sudo update-alternatives --set default.plymouth \
    "$PLYMOUTH_DEST/cyberpunk-plymouth.plymouth" 2>/dev/null || true
  sudo update-initramfs -u 2>/dev/null || true
  ok "Plymouth theme installed"
else
  warn "Could not install Plymouth theme (sudo required)"
fi

# ---- Step 15: Install autostart entries ----
log "Installing autostart entries..."
mkdir -p "$HOME/.config/autostart"
# Fill in paths from templates
sed "s|__VENV_PYTHON__|$VENV_DIR/bin/python3|g; s|__COMPUTA_DIR__|$COMPUTA_DIR|g" \
  "$COMPUTA_DIR/autostart/computa-listener.desktop" \
  > "$HOME/.config/autostart/computa-listener.desktop"
sed "s|__COMPUTA_DIR__|$COMPUTA_DIR|g" \
  "$COMPUTA_DIR/autostart/computa-login-sound.desktop" \
  > "$HOME/.config/autostart/computa-login-sound.desktop"
ok "Autostart entries installed"

# ---- Step 16: Install notification sounds ----
log "Installing notification sounds..."
SOUND_DEST="$HOME/.local/share/sounds/__custom"
mkdir -p "$SOUND_DEST"
if [ -d "$COMPUTA_DIR/assets/sounds" ]; then
  cp "$COMPUTA_DIR/assets/sounds/"*.ogg "$SOUND_DEST/" 2>/dev/null || \
  cp "$COMPUTA_DIR/assets/sounds/"*.wav "$SOUND_DEST/" 2>/dev/null || true
fi
ok "Sounds installed"

# ---- Done ----
echo ""
echo -e "${CYAN}╔══════════════════════════════════════════╗${NC}"
echo -e "${CYAN}║${NC}  ${GREEN}COMPUTA-MODE INSTALLED${NC}                   ${CYAN}║${NC}"
echo -e "${CYAN}╠══════════════════════════════════════════╣${NC}"
echo -e "${CYAN}║${NC}                                            ${CYAN}║${NC}"
echo -e "${CYAN}║${NC}  ${MAGENTA}Super+V${NC} = Push-to-talk trigger           ${CYAN}║${NC}"
echo -e "${CYAN}║${NC}  Say ${GREEN}'computa activate'${NC} to launch        ${CYAN}║${NC}"
echo -e "${CYAN}║${NC}  Claude Code with cyberpunk theme         ${CYAN}║${NC}"
echo -e "${CYAN}║${NC}                                            ${CYAN}║${NC}"
echo -e "${CYAN}║${NC}  ${YELLOW}Re-enroll voice on Linux:${NC}                 ${CYAN}║${NC}"
echo -e "${CYAN}║${NC}  ${CYAN}cd $COMPUTA_DIR && source venv/bin/activate${NC}${CYAN}║${NC}"
echo -e "${CYAN}║${NC}  ${CYAN}python3 speaker_verify.py enroll${NC}           ${CYAN}║${NC}"
echo -e "${CYAN}║${NC}                                            ${CYAN}║${NC}"
echo -e "${CYAN}║${NC}  ${YELLOW}Reboot to see GRUB + Plymouth themes${NC}      ${CYAN}║${NC}"
echo -e "${CYAN}║${NC}  ${YELLOW}Log out/in for full GNOME Shell theme${NC}     ${CYAN}║${NC}"
echo -e "${CYAN}║${NC}                                            ${CYAN}║${NC}"
echo -e "${CYAN}╚══════════════════════════════════════════╝${NC}"
echo ""
