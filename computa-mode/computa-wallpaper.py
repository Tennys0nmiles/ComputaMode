#!/usr/bin/env python3
"""Generate cyberpunk visual assets: animated wallpaper, static fallback, GRUB bg, Plymouth frames."""

import math
import os
import random
import struct
import subprocess
import sys
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont, ImageFilter

COMPUTA_DIR = Path.home() / "computa-mode"
WALLPAPER_DIR = COMPUTA_DIR / "wallpaper"
GRUB_DIR = COMPUTA_DIR / "grub" / "cyberpunk-grub"
PLYMOUTH_DIR = COMPUTA_DIR / "plymouth" / "cyberpunk-plymouth" / "assets"

WIDTH, HEIGHT = 1920, 1080
FPS = 30
DURATION_S = 10
TOTAL_FRAMES = FPS * DURATION_S

# Color palette
BG = (8, 8, 24)
CYAN = (0, 255, 255)
MAGENTA = (255, 0, 255)
DARK_CYAN = (0, 80, 80)
DARK_MAGENTA = (80, 0, 80)
NEON_PINK = (255, 0, 128)
NEON_GREEN = (0, 255, 136)
WHITE = (200, 200, 220)

# Seed for reproducibility
random.seed(42)

# Pre-generate city skyline (random buildings)
BUILDINGS = []
x = 0
while x < WIDTH:
    w = random.randint(30, 120)
    h = random.randint(80, 450)
    BUILDINGS.append((x, h, w))
    x += w + random.randint(0, 15)

# Pre-generate star positions
STARS = [(random.randint(0, WIDTH), random.randint(0, int(HEIGHT * 0.45)),
          random.randint(1, 3), random.random()) for _ in range(200)]

# Pre-generate particle positions (floating dots)
PARTICLES = [(random.uniform(0, WIDTH), random.uniform(0, HEIGHT),
              random.uniform(0.3, 2.0), random.uniform(0.5, 3.0),
              random.choice([CYAN, MAGENTA, NEON_GREEN])) for _ in range(80)]

# Hex data strings for floating text
HEX_STRINGS = [
    "0xDEADBEEF", "0xCAFEBABE", "0xFF00FF", "0x00FFFF",
    "SYS.INIT", "NEURAL.LINK", "COMPUTA//", ">>ACTIVATE",
    "0x1337C0DE", "GRID.SYNC", "NET.PULSE", "ICE.BREAK",
]


def lerp_color(c1, c2, t):
    """Linearly interpolate between two RGB colors."""
    return tuple(int(a + (b - a) * t) for a, b in zip(c1, c2))


def alpha_blend(base, overlay, alpha):
    """Blend overlay color onto base with given alpha (0-255)."""
    a = alpha / 255.0
    return tuple(int(b * (1 - a) + o * a) for b, o in zip(base, overlay))


def try_load_font(size):
    """Try to load Share Tech Mono, fall back to default."""
    font_paths = [
        Path.home() / ".local" / "share" / "fonts" / "ShareTechMono-Regular.ttf",
        "/usr/share/fonts/truetype/share-tech-mono/ShareTechMono-Regular.ttf",
    ]
    for p in font_paths:
        if p.exists():
            return ImageFont.truetype(str(p), size)
    try:
        return ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSansMono.ttf", size)
    except (IOError, OSError):
        return ImageFont.load_default()


def draw_city_silhouette(draw, horizon_y, frame_t):
    """Draw neon-outlined city skyline at the horizon."""
    for bx, bh, bw in BUILDINGS:
        top = horizon_y - bh
        # Building body (very dark)
        draw.rectangle([bx, top, bx + bw, horizon_y], fill=(4, 4, 16))

        # Neon outline glow
        glow_alpha = int(20 + 15 * math.sin(frame_t * 2 + bx * 0.01))
        outline_color = (*CYAN[:2], CYAN[2], glow_alpha) if bx % 3 != 0 else (*MAGENTA, glow_alpha)
        # Top edge
        draw.line([(bx, top), (bx + bw, top)], fill=CYAN if bx % 3 != 0 else MAGENTA, width=1)
        # Side edges (subtle)
        edge_color = lerp_color(BG, CYAN if bx % 3 != 0 else MAGENTA, 0.15)
        draw.line([(bx, top), (bx, horizon_y)], fill=edge_color, width=1)
        draw.line([(bx + bw, top), (bx + bw, horizon_y)], fill=edge_color, width=1)

        # Random lit windows
        if bh > 40:
            win_rows = bh // 20
            win_cols = max(1, bw // 15)
            for wr in range(win_rows):
                for wc in range(win_cols):
                    if random.random() < 0.3:
                        wx = bx + 5 + wc * 15
                        wy = top + 10 + wr * 20
                        if wx + 8 < bx + bw and wy + 6 < horizon_y:
                            # Flicker effect
                            flicker = 0.5 + 0.5 * math.sin(frame_t * 5 + wx * 0.1 + wr)
                            wc_color = lerp_color(BG, random.choice([CYAN, MAGENTA, NEON_PINK, (255, 200, 50)]),
                                                  0.2 + 0.3 * flicker)
                            draw.rectangle([wx, wy, wx + 8, wy + 6], fill=wc_color)


def draw_grid(draw, horizon_y, frame_t):
    """Draw the subtle background grid above horizon."""
    # Vertical grid lines
    for x in range(0, WIDTH, 60):
        pulse = 0.5 + 0.5 * math.sin(frame_t * 0.5 + x * 0.005)
        alpha = int(15 + 15 * pulse)
        color = lerp_color(BG, CYAN, alpha / 255.0)
        draw.line([(x, 0), (x, horizon_y)], fill=color, width=1)

    # Horizontal grid lines
    for y in range(0, horizon_y, 60):
        pulse = 0.5 + 0.5 * math.sin(frame_t * 0.3 + y * 0.008)
        alpha = int(12 + 12 * pulse)
        color = lerp_color(BG, CYAN, alpha / 255.0)
        draw.line([(0, y), (WIDTH, y)], fill=color, width=1)

    # Major grid lines (brighter)
    for x in range(0, WIDTH, 300):
        color = lerp_color(BG, CYAN, 0.12)
        draw.line([(x, 0), (x, horizon_y)], fill=color, width=1)
    for y in range(0, horizon_y, 300):
        color = lerp_color(BG, CYAN, 0.12)
        draw.line([(0, y), (WIDTH, y)], fill=color, width=1)


def draw_retrowave_floor(draw, horizon_y, frame_t):
    """Draw the magenta perspective grid below the horizon."""
    vanish_x = WIDTH // 2

    # Perspective lines from vanishing point
    for x in range(0, WIDTH + 1, 80):
        alpha = 0.08 + 0.04 * math.sin(frame_t + x * 0.01)
        color = lerp_color(BG, MAGENTA, alpha)
        draw.line([(vanish_x, horizon_y), (x, HEIGHT)], fill=color, width=1)

    # Horizontal lines that scroll toward viewer
    scroll_offset = (frame_t * 60) % 40
    for i in range(30):
        raw_y = horizon_y + scroll_offset + i * (i + 1) * 0.8
        y = int(raw_y)
        if y >= HEIGHT:
            break
        if y <= horizon_y:
            continue
        progress = (y - horizon_y) / (HEIGHT - horizon_y)
        alpha = 0.05 + 0.2 * progress
        pulse = 0.8 + 0.2 * math.sin(frame_t * 2 + i * 0.5)
        color = lerp_color(BG, MAGENTA, alpha * pulse)
        draw.line([(0, y), (WIDTH, y)], fill=color, width=1)


def draw_horizon_glow(draw, img, horizon_y, frame_t):
    """Draw the glowing horizon line with bloom effect."""
    pulse = 0.7 + 0.3 * math.sin(frame_t * 1.5)

    # Multiple layers for glow
    for offset, alpha_mult in [(-3, 0.1), (-2, 0.15), (-1, 0.3), (0, 0.8), (1, 0.3), (2, 0.15), (3, 0.1)]:
        y = horizon_y + offset
        alpha = alpha_mult * pulse
        color = lerp_color(BG, CYAN, alpha)
        draw.line([(0, y), (WIDTH, y)], fill=color, width=1)


def draw_stars(draw, frame_t):
    """Draw twinkling stars."""
    for sx, sy, size, phase in STARS:
        twinkle = 0.3 + 0.7 * abs(math.sin(frame_t * 1.5 + phase * 6.28))
        brightness = int(80 * twinkle)
        color = (brightness, brightness, brightness + 20)
        if size <= 1:
            draw.point((sx, sy), fill=color)
        else:
            draw.ellipse([sx - 1, sy - 1, sx + 1, sy + 1], fill=color)


def draw_scanlines(draw, frame_t):
    """Draw scrolling CRT scanlines."""
    scan_y = int((frame_t * 100) % (HEIGHT + 200)) - 100
    for offset in range(-50, 50):
        y = scan_y + offset
        if 0 <= y < HEIGHT:
            dist = abs(offset)
            alpha = max(0, 0.06 - dist * 0.0012)
            color = lerp_color(BG, CYAN, alpha)
            draw.line([(0, y), (WIDTH, y)], fill=color, width=1)

    # Static scanline pattern (subtle CRT effect)
    for y in range(0, HEIGHT, 3):
        color = lerp_color(BG, (0, 0, 0), 0.05)
        draw.line([(0, y), (WIDTH, y)], fill=color, width=1)


def draw_particles(draw, frame_t):
    """Draw floating neon particles."""
    for i, (px, py, speed, size, color) in enumerate(PARTICLES):
        # Float upward with slight horizontal drift
        current_y = (py - frame_t * speed * 30) % HEIGHT
        drift = math.sin(frame_t * 2 + i) * 20
        current_x = (px + drift) % WIDTH

        # Pulse
        pulse = 0.4 + 0.6 * abs(math.sin(frame_t * 3 + i * 0.7))
        r = int(size * (1 + 0.5 * pulse))

        glow_color = lerp_color(BG, color, 0.3 * pulse)
        draw.ellipse([current_x - r * 2, current_y - r * 2, current_x + r * 2, current_y + r * 2],
                     fill=glow_color)
        bright_color = lerp_color(BG, color, 0.7 * pulse)
        draw.ellipse([current_x - r, current_y - r, current_x + r, current_y + r],
                     fill=bright_color)


def draw_glitch_strips(draw, frame_t):
    """Draw occasional horizontal glitch strips."""
    # Only show glitches periodically
    glitch_cycle = math.sin(frame_t * 0.7) + math.sin(frame_t * 1.3)
    if glitch_cycle > 1.5:
        num_strips = random.randint(2, 6)
        for _ in range(num_strips):
            y = random.randint(0, HEIGHT)
            h = random.randint(1, 4)
            x_offset = random.randint(-30, 30)
            strip_w = random.randint(100, 600)
            x_start = random.randint(0, WIDTH)
            color = random.choice([CYAN, MAGENTA, NEON_PINK])
            alpha_color = lerp_color(BG, color, 0.15 + random.random() * 0.2)
            draw.rectangle([x_start + x_offset, y, x_start + strip_w + x_offset, y + h],
                           fill=alpha_color)


def draw_hex_data(draw, frame_t, font):
    """Draw floating hex/data text."""
    for i, text in enumerate(HEX_STRINGS):
        phase = frame_t * 0.3 + i * 1.7
        x = int((i * 170 + math.sin(phase) * 50) % WIDTH)
        y = int((i * 95 + frame_t * 10 * (0.5 + i * 0.1)) % HEIGHT)

        pulse = 0.15 + 0.15 * abs(math.sin(frame_t * 2 + i))
        color = lerp_color(BG, CYAN if i % 2 == 0 else NEON_GREEN, pulse)
        draw.text((x, y), text, fill=color, font=font)


def draw_hud_corners(draw, frame_t):
    """Draw HUD-style corner brackets."""
    corner_size = 40
    pulse = 0.4 + 0.3 * math.sin(frame_t * 2)
    color = lerp_color(BG, CYAN, pulse)

    margin = 30
    # Top-left
    draw.line([(margin, margin), (margin + corner_size, margin)], fill=color, width=2)
    draw.line([(margin, margin), (margin, margin + corner_size)], fill=color, width=2)
    # Top-right
    draw.line([(WIDTH - margin, margin), (WIDTH - margin - corner_size, margin)], fill=color, width=2)
    draw.line([(WIDTH - margin, margin), (WIDTH - margin, margin + corner_size)], fill=color, width=2)
    # Bottom-left
    draw.line([(margin, HEIGHT - margin), (margin + corner_size, HEIGHT - margin)], fill=color, width=2)
    draw.line([(margin, HEIGHT - margin), (margin, HEIGHT - margin - corner_size)], fill=color, width=2)
    # Bottom-right
    draw.line([(WIDTH - margin, HEIGHT - margin), (WIDTH - margin - corner_size, HEIGHT - margin)], fill=color, width=2)
    draw.line([(WIDTH - margin, HEIGHT - margin), (WIDTH - margin, HEIGHT - margin - corner_size)], fill=color, width=2)


def generate_frame(frame_num, font_small, font_hex):
    """Generate a single wallpaper frame."""
    frame_t = frame_num / FPS  # time in seconds
    img = Image.new("RGB", (WIDTH, HEIGHT), BG)
    draw = ImageDraw.Draw(img)

    horizon_y = int(HEIGHT * 0.55)

    # Layer 1: Stars
    draw_stars(draw, frame_t)

    # Layer 2: Background grid (above horizon)
    draw_grid(draw, horizon_y, frame_t)

    # Layer 3: City silhouette
    draw_city_silhouette(draw, horizon_y, frame_t)

    # Layer 4: Horizon glow
    draw_horizon_glow(draw, img, horizon_y, frame_t)

    # Layer 5: Retrowave floor
    draw_retrowave_floor(draw, horizon_y, frame_t)

    # Layer 6: Floating particles
    draw_particles(draw, frame_t)

    # Layer 7: Scanlines
    draw_scanlines(draw, frame_t)

    # Layer 8: Hex data
    draw_hex_data(draw, frame_t, font_hex)

    # Layer 9: Glitch strips
    draw_glitch_strips(draw, frame_t)

    # Layer 10: HUD corners
    draw_hud_corners(draw, frame_t)

    return img


def generate_plymouth_frame(frame_num, total_frames, font_large, font_small):
    """Generate a Plymouth boot animation frame."""
    frame_t = frame_num / 30.0
    progress = frame_num / total_frames

    img = Image.new("RGB", (WIDTH, HEIGHT), BG)
    draw = ImageDraw.Draw(img)

    # Subtle grid
    for x in range(0, WIDTH, 80):
        color = lerp_color(BG, CYAN, 0.04)
        draw.line([(x, 0), (x, HEIGHT)], fill=color, width=1)
    for y in range(0, HEIGHT, 80):
        color = lerp_color(BG, CYAN, 0.04)
        draw.line([(0, y), (WIDTH, y)], fill=color, width=1)

    # Scanning line
    scan_y = int((frame_t * 150) % HEIGHT)
    for offset in range(-20, 20):
        y = scan_y + offset
        if 0 <= y < HEIGHT:
            alpha = max(0, 0.1 - abs(offset) * 0.005)
            color = lerp_color(BG, CYAN, alpha)
            draw.line([(0, y), (WIDTH, y)], fill=color, width=1)

    # "COMPUTA SYSTEM" text
    text = "COMPUTA SYSTEM"
    text_pulse = 0.6 + 0.4 * abs(math.sin(frame_t * 2))
    text_color = lerp_color(BG, CYAN, text_pulse)
    bbox = draw.textbbox((0, 0), text, font=font_large)
    tw, th = bbox[2] - bbox[0], bbox[3] - bbox[1]
    tx = (WIDTH - tw) // 2
    ty = (HEIGHT - th) // 2 - 40
    # Glow behind text
    glow_color = lerp_color(BG, CYAN, 0.1 * text_pulse)
    for gx in range(-3, 4):
        for gy in range(-3, 4):
            draw.text((tx + gx, ty + gy), text, fill=glow_color, font=font_large)
    draw.text((tx, ty), text, fill=text_color, font=font_large)

    # Subtitle
    sub = "INITIALIZING..."
    sub_color = lerp_color(BG, MAGENTA, 0.4 + 0.2 * math.sin(frame_t * 3))
    bbox2 = draw.textbbox((0, 0), sub, font=font_small)
    sw = bbox2[2] - bbox2[0]
    draw.text(((WIDTH - sw) // 2, ty + th + 20), sub, fill=sub_color, font=font_small)

    # Progress bar
    bar_w = 400
    bar_h = 8
    bar_x = (WIDTH - bar_w) // 2
    bar_y = HEIGHT // 2 + 60

    # Bar outline
    draw.rectangle([bar_x - 1, bar_y - 1, bar_x + bar_w + 1, bar_y + bar_h + 1],
                   outline=lerp_color(BG, CYAN, 0.3))
    # Bar fill
    fill_w = int(bar_w * progress)
    if fill_w > 0:
        # Gradient fill
        for px in range(fill_w):
            t = px / bar_w
            color = lerp_color(CYAN, MAGENTA, t)
            pulse_alpha = 0.5 + 0.5 * math.sin(frame_t * 4 + px * 0.05)
            final_color = lerp_color(BG, color, 0.4 + 0.4 * pulse_alpha)
            draw.line([(bar_x + px, bar_y), (bar_x + px, bar_y + bar_h)], fill=final_color)

    # Percentage text
    pct_text = f"{int(progress * 100)}%"
    pct_color = lerp_color(BG, WHITE, 0.5)
    bbox3 = draw.textbbox((0, 0), pct_text, font=font_small)
    pw = bbox3[2] - bbox3[0]
    draw.text(((WIDTH - pw) // 2, bar_y + bar_h + 10), pct_text, fill=pct_color, font=font_small)

    return img


def main():
    print("=== COMPUTA WALLPAPER GENERATOR ===")

    # Create output directories
    WALLPAPER_DIR.mkdir(parents=True, exist_ok=True)
    GRUB_DIR.mkdir(parents=True, exist_ok=True)
    PLYMOUTH_DIR.mkdir(parents=True, exist_ok=True)

    # Load fonts
    font_small = try_load_font(14)
    font_hex = try_load_font(11)
    font_large = try_load_font(48)
    font_medium = try_load_font(18)

    # Temporary directory for frames
    frame_dir = WALLPAPER_DIR / "frames"
    frame_dir.mkdir(parents=True, exist_ok=True)

    # --- Generate animated wallpaper frames ---
    print(f"Generating {TOTAL_FRAMES} wallpaper frames...")
    for i in range(TOTAL_FRAMES):
        if i % 30 == 0:
            print(f"  Frame {i}/{TOTAL_FRAMES} ({i * 100 // TOTAL_FRAMES}%)")
        img = generate_frame(i, font_small, font_hex)
        img.save(frame_dir / f"frame_{i:04d}.png")

    # Save static fallback (frame 0)
    print("Saving static wallpaper...")
    static = generate_frame(0, font_small, font_hex)
    static.save(WALLPAPER_DIR / "cyberpunk_static.png")

    # --- Encode to WebM video ---
    print("Encoding animated wallpaper to WebM...")
    output_webm = WALLPAPER_DIR / "cyberpunk_animated.webm"
    try:
        subprocess.run([
            "ffmpeg", "-y",
            "-framerate", str(FPS),
            "-i", str(frame_dir / "frame_%04d.png"),
            "-c:v", "libvpx-vp9",
            "-pix_fmt", "yuv420p",
            "-b:v", "2M",
            "-an",
            str(output_webm),
        ], check=True, capture_output=True)
        print(f"  Saved: {output_webm}")
    except (subprocess.CalledProcessError, FileNotFoundError) as e:
        print(f"  Warning: ffmpeg encoding failed ({e}), static wallpaper will be used")

    # --- Generate GRUB background ---
    print("Generating GRUB background...")
    grub_bg = generate_frame(0, font_small, font_hex)
    grub_bg.save(GRUB_DIR / "background.png")
    print(f"  Saved: {GRUB_DIR / 'background.png'}")

    # --- Generate Plymouth frames ---
    plymouth_frames = 60
    print(f"Generating {plymouth_frames} Plymouth boot frames...")
    for i in range(plymouth_frames):
        img = generate_plymouth_frame(i, plymouth_frames, font_large, font_medium)
        img.save(PLYMOUTH_DIR / f"frame_{i:04d}.png")
    print(f"  Saved {plymouth_frames} frames to {PLYMOUTH_DIR}")

    # Clean up wallpaper frame PNGs (keep only the video)
    if (WALLPAPER_DIR / "cyberpunk_animated.webm").exists():
        print("Cleaning up temporary frame PNGs...")
        import shutil
        shutil.rmtree(frame_dir, ignore_errors=True)

    print("\n=== DONE ===")
    print(f"Animated wallpaper: {output_webm}")
    print(f"Static wallpaper:   {WALLPAPER_DIR / 'cyberpunk_static.png'}")
    print(f"GRUB background:    {GRUB_DIR / 'background.png'}")
    print(f"Plymouth frames:    {PLYMOUTH_DIR}")


if __name__ == "__main__":
    main()
