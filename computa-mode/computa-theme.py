#!/usr/bin/env python3
"""Apply cyberpunk theme to GNOME desktop - Computa Mode."""

import os
import subprocess
import sys
import uuid
from pathlib import Path

COMPUTA_DIR = Path.home() / "computa-mode"


def run(cmd, check=True):
    """Run a shell command."""
    return subprocess.run(cmd, shell=True, check=check, capture_output=True, text=True)


def gsettings_set(schema, key, value):
    """Set a gsettings value."""
    run(f"gsettings set {schema} {key} {value!r}")


def dconf_write(path, value):
    """Write a dconf value."""
    run(f"dconf write {path} {value!r}")


def apply_dark_mode():
    """Enable GNOME dark mode."""
    print("[theme] Setting dark mode...")
    gsettings_set("org.gnome.desktop.interface", "color-scheme", "prefer-dark")
    # Try Yaru-dark first (Ubuntu), fall back to Adwaita-dark
    try:
        run("gsettings set org.gnome.desktop.interface gtk-theme 'Yaru-dark'")
    except subprocess.CalledProcessError:
        run("gsettings set org.gnome.desktop.interface gtk-theme 'Adwaita-dark'", check=False)


def apply_cursor():
    """Set cyberpunk cursor theme."""
    print("[theme] Setting cursor theme...")
    # Check if Bibata is installed
    bibata_path = Path.home() / ".local" / "share" / "icons" / "Bibata-Modern-Ice"
    if bibata_path.exists():
        gsettings_set("org.gnome.desktop.interface", "cursor-theme", "Bibata-Modern-Ice")
        gsettings_set("org.gnome.desktop.interface", "cursor-size", "24")
    else:
        print("  Warning: Bibata-Modern-Ice not found, skipping cursor theme")


def apply_font():
    """Set cyberpunk monospace font."""
    print("[theme] Setting fonts...")
    font_path = Path.home() / ".local" / "share" / "fonts" / "ShareTechMono-Regular.ttf"
    if font_path.exists():
        gsettings_set("org.gnome.desktop.interface", "monospace-font-name", "Share Tech Mono 11")
    else:
        print("  Warning: Share Tech Mono not found, skipping font")


def apply_wallpaper():
    """Set cyberpunk wallpaper."""
    print("[theme] Setting wallpaper...")
    wallpaper = Path.home() / "Downloads" / "Dyson_Sphere.jpg"
    if wallpaper.exists():
        uri = f"file://{wallpaper}"
        gsettings_set("org.gnome.desktop.background", "picture-uri", uri)
        gsettings_set("org.gnome.desktop.background", "picture-uri-dark", uri)
        gsettings_set("org.gnome.desktop.background", "picture-options", "zoom")
    else:
        print("  Warning: ~/Downloads/Dyson_Sphere.jpg not found")


def apply_shell_theme():
    """Apply GNOME Shell CSS theme."""
    print("[theme] Installing GNOME Shell theme...")
    theme_dest = Path.home() / ".local" / "share" / "themes" / "Cyberpunk" / "gnome-shell"
    theme_dest.mkdir(parents=True, exist_ok=True)

    src_css = COMPUTA_DIR / "theme" / "gnome-shell" / "gnome-shell.css"
    src_meta = COMPUTA_DIR / "theme" / "gnome-shell" / "metadata.json"

    if src_css.exists():
        import shutil
        shutil.copy2(src_css, theme_dest / "gnome-shell.css")
        if src_meta.exists():
            shutil.copy2(src_meta, theme_dest.parent / "metadata.json")

    # Enable user-themes extension and apply
    run("gnome-extensions enable user-theme@gnome-shell-extensions.gcampax.github.com", check=False)
    try:
        gsettings_set("org.gnome.shell.extensions.user-theme", "name", "Cyberpunk")
    except subprocess.CalledProcessError:
        print("  Warning: Could not set shell theme (user-themes extension may not be installed)")
        print("  Install with: sudo apt install gnome-shell-extension-user-themes")


def apply_gtk_overrides():
    """Install GTK CSS overrides."""
    print("[theme] Installing GTK CSS overrides...")
    import shutil

    for version in ["gtk-3.0", "gtk-4.0"]:
        src = COMPUTA_DIR / "theme" / version / "gtk.css"
        dest_dir = Path.home() / ".config" / version
        dest_dir.mkdir(parents=True, exist_ok=True)
        dest = dest_dir / "gtk.css"

        if src.exists():
            # Backup existing
            if dest.exists():
                backup = dest.with_suffix(".css.bak.computa")
                if not backup.exists():
                    shutil.copy2(dest, backup)
            shutil.copy2(src, dest)


def setup_terminal():
    """Create a Cyberpunk terminal profile."""
    print("[theme] Setting up terminal profile...")

    profile_uuid = str(uuid.uuid4())
    profile_path = f"/org/gnome/terminal/legacy/profiles:/:{profile_uuid}/"

    # Get existing profiles list
    result = run("dconf read /org/gnome/terminal/legacy/profiles:/list", check=False)
    existing = result.stdout.strip()

    if existing and existing != "":
        # Parse existing list and append
        profiles_str = existing.strip("[]' ")
        if profiles_str:
            profiles = [p.strip().strip("'\"") for p in profiles_str.split(",")]
        else:
            profiles = []
        profiles.append(profile_uuid)
        profiles_val = "[" + ", ".join(f"'{p}'" for p in profiles) + "]"
    else:
        profiles_val = f"['{profile_uuid}']"

    dconf_write("/org/gnome/terminal/legacy/profiles:/list", profiles_val)
    dconf_write(f"/org/gnome/terminal/legacy/profiles:/default", f"'{profile_uuid}'")

    # Profile settings
    settings = {
        "visible-name": "'Cyberpunk'",
        "use-theme-colors": "false",
        "foreground-color": "'#00ffcc'",
        "background-color": "'#080818'",
        "cursor-foreground-color": "'#ff00ff'",
        "cursor-background-color": "'#00ffff'",
        "cursor-colors-set": "true",
        "bold-color": "'#00ffff'",
        "bold-color-same-as-fg": "false",
        "use-transparent-background": "true",
        "background-transparency-percent": "8",
        "use-system-font": "false",
        "scrollback-unlimited": "true",
        "audible-bell": "false",
    }

    # Check if font is available
    font_path = Path.home() / ".local" / "share" / "fonts" / "ShareTechMono-Regular.ttf"
    if font_path.exists():
        settings["font"] = "'Share Tech Mono 12'"
    else:
        settings["font"] = "'Monospace 12'"

    # Cyberpunk color palette
    palette = [
        "'#0a0a1a'",  # 0  black
        "'#ff0055'",  # 1  red
        "'#00ff88'",  # 2  green
        "'#ffcc00'",  # 3  yellow
        "'#0088ff'",  # 4  blue
        "'#ff00ff'",  # 5  magenta
        "'#00ffff'",  # 6  cyan
        "'#c0c0d0'",  # 7  white
        "'#404060'",  # 8  bright black
        "'#ff3377'",  # 9  bright red
        "'#00ffaa'",  # 10 bright green
        "'#ffdd44'",  # 11 bright yellow
        "'#33aaff'",  # 12 bright blue
        "'#ff44ff'",  # 13 bright magenta
        "'#44ffff'",  # 14 bright cyan
        "'#ffffff'",  # 15 bright white
    ]
    settings["palette"] = "[" + ", ".join(palette) + "]"

    for key, value in settings.items():
        dconf_write(f"{profile_path}{key}", value)

    print(f"  Terminal profile 'Cyberpunk' created (UUID: {profile_uuid})")


def apply_misc():
    """Apply misc GNOME settings."""
    print("[theme] Applying misc settings...")
    # Enable event sounds
    gsettings_set("org.gnome.desktop.sound", "event-sounds", "true")
    # Dark lock screen background
    gsettings_set("org.gnome.desktop.screensaver", "picture-uri", "")
    gsettings_set("org.gnome.desktop.screensaver", "primary-color", "#080818")


def apply_all():
    """Apply the complete cyberpunk theme."""
    print("=== APPLYING CYBERPUNK THEME ===")
    apply_dark_mode()
    apply_cursor()
    apply_font()
    apply_wallpaper()
    apply_shell_theme()
    apply_gtk_overrides()
    apply_misc()
    print("=== THEME APPLIED ===")
    print("Note: GNOME Shell theme changes require logout/login to fully take effect.")


def main():
    if len(sys.argv) < 2:
        print("Usage: computa-theme.py [apply|setup-terminal]")
        sys.exit(1)

    cmd = sys.argv[1]
    if cmd == "apply":
        apply_all()
    elif cmd == "setup-terminal":
        setup_terminal()
    elif cmd == "all":
        apply_all()
        setup_terminal()
    else:
        print(f"Unknown command: {cmd}")
        sys.exit(1)


if __name__ == "__main__":
    main()
