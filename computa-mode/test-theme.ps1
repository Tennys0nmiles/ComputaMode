Add-Type -AssemblyName System.Drawing

# Quick test: apply the cyberpunk theme without voice activation
Write-Host "Testing cyberpunk theme..." -ForegroundColor Magenta

# --- Dark Mode ---
$themePath = "HKCU:\SOFTWARE\Microsoft\Windows\CurrentVersion\Themes\Personalize"
Set-ItemProperty -Path $themePath -Name "AppsUseLightTheme" -Value 0
Set-ItemProperty -Path $themePath -Name "SystemUsesLightTheme" -Value 0

# --- Neon Accent Color (Cyan #00FFFF in ABGR) ---
$dwmPath = "HKCU:\SOFTWARE\Microsoft\Windows\DWM"
$neonCyan = 0x00FFFF00
Set-ItemProperty -Path $dwmPath -Name "AccentColor" -Value $neonCyan -Type DWord
Set-ItemProperty -Path $dwmPath -Name "ColorizationColor" -Value $neonCyan -Type DWord
Set-ItemProperty -Path $dwmPath -Name "ColorizationAfterglow" -Value $neonCyan -Type DWord
Set-ItemProperty -Path $dwmPath -Name "ColorPrevalence" -Value 1 -Type DWord
Set-ItemProperty -Path $themePath -Name "ColorPrevalence" -Value 1 -Type DWord

# --- Generate Cyberpunk Wallpaper ---
$width = 1920; $height = 1080
$bmp = New-Object System.Drawing.Bitmap($width, $height)
$gfx = [System.Drawing.Graphics]::FromImage($bmp)

$gfx.Clear([System.Drawing.Color]::FromArgb(255, 8, 8, 24))

$gridPen = New-Object System.Drawing.Pen([System.Drawing.Color]::FromArgb(30, 0, 255, 255), 1)
for ($x = 0; $x -lt $width; $x += 60) { $gfx.DrawLine($gridPen, $x, 0, $x, $height) }
for ($y = 0; $y -lt $height; $y += 60) { $gfx.DrawLine($gridPen, 0, $y, $width, $y) }

$majorPen = New-Object System.Drawing.Pen([System.Drawing.Color]::FromArgb(60, 0, 255, 255), 1)
for ($x = 0; $x -lt $width; $x += 300) { $gfx.DrawLine($majorPen, $x, 0, $x, $height) }
for ($y = 0; $y -lt $height; $y += 300) { $gfx.DrawLine($majorPen, 0, $y, $width, $y) }

$horizonY = [int]($height * 0.6)
$glowPen = New-Object System.Drawing.Pen([System.Drawing.Color]::FromArgb(200, 0, 255, 255), 2)
$gfx.DrawLine($glowPen, 0, $horizonY, $width, $horizonY)

$vanishX = $width / 2
$perspPen = New-Object System.Drawing.Pen([System.Drawing.Color]::FromArgb(50, 255, 0, 255), 1)
for ($x = 0; $x -lt $width; $x += 120) {
    $gfx.DrawLine($perspPen, $vanishX, $horizonY, $x, $height)
}
for ($y = $horizonY; $y -lt $height; $y += 40) {
    $a = [Math]::Min(120, 30 + ($y - $horizonY) * 0.3)
    $hp = New-Object System.Drawing.Pen([System.Drawing.Color]::FromArgb([int]$a, 255, 0, 255), 1)
    $gfx.DrawLine($hp, 0, $y, $width, $y)
    $hp.Dispose()
}

$wallPath = Join-Path $env:USERPROFILE "computa-mode\cyberpunk_wallpaper.bmp"
$bmp.Save($wallPath, [System.Drawing.Imaging.ImageFormat]::Bmp)
$gfx.Dispose(); $bmp.Dispose()
$gridPen.Dispose(); $majorPen.Dispose(); $glowPen.Dispose(); $perspPen.Dispose()

Set-ItemProperty -Path "HKCU:\Control Panel\Desktop" -Name "WallpaperStyle" -Value "10"
Set-ItemProperty -Path "HKCU:\Control Panel\Desktop" -Name "TileWallpaper" -Value "0"

Add-Type @"
using System;
using System.Runtime.InteropServices;
public class CyberpunkWall {
    [DllImport("user32.dll", CharSet = CharSet.Auto)]
    public static extern int SystemParametersInfo(int uAction, int uParam, string lpvParam, int fuWinIni);
}
"@
[CyberpunkWall]::SystemParametersInfo(0x0014, 0, $wallPath, 0x01 -bor 0x02) | Out-Null

Write-Host "Cyberpunk theme applied!" -ForegroundColor Magenta
