Add-Type -AssemblyName System.Speech
Add-Type -AssemblyName System.Drawing

# Wait for Windows audio to be ready after login
Start-Sleep -Seconds 5

# ---- Configuration ----
$pythonExe     = "C:\Python314\python.exe"
$verifyScript  = "C:\Users\tenny\computa-mode\speaker_verify.py"
$voiceProfile  = "C:\Users\tenny\computa-mode\voice_profile.npy"
$confidenceMin = 0.65   # speech recognition confidence threshold
$verifyThreshold = 0.65 # speaker similarity threshold (tune 0.55-0.85)

# ---- Cyberpunk Theme ----

function Set-CyberpunkTheme {
    Write-Host "[$(Get-Date -Format 'HH:mm:ss')] Applying cyberpunk theme..." -ForegroundColor Magenta

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

    # Deep dark background
    $gfx.Clear([System.Drawing.Color]::FromArgb(255, 8, 8, 24))

    # Subtle cyan grid
    $gridPen = New-Object System.Drawing.Pen([System.Drawing.Color]::FromArgb(30, 0, 255, 255), 1)
    for ($x = 0; $x -lt $width; $x += 60) { $gfx.DrawLine($gridPen, $x, 0, $x, $height) }
    for ($y = 0; $y -lt $height; $y += 60) { $gfx.DrawLine($gridPen, 0, $y, $width, $y) }

    # Brighter major grid lines
    $majorPen = New-Object System.Drawing.Pen([System.Drawing.Color]::FromArgb(60, 0, 255, 255), 1)
    for ($x = 0; $x -lt $width; $x += 300) { $gfx.DrawLine($majorPen, $x, 0, $x, $height) }
    for ($y = 0; $y -lt $height; $y += 300) { $gfx.DrawLine($majorPen, 0, $y, $width, $y) }

    # Glowing horizon line
    $horizonY = [int]($height * 0.6)
    $glowPen = New-Object System.Drawing.Pen([System.Drawing.Color]::FromArgb(200, 0, 255, 255), 2)
    $gfx.DrawLine($glowPen, 0, $horizonY, $width, $horizonY)

    # Magenta perspective grid below horizon (retrowave floor)
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

    # Save wallpaper
    $wallPath = Join-Path $env:USERPROFILE "computa-mode\cyberpunk_wallpaper.bmp"
    $bmp.Save($wallPath, [System.Drawing.Imaging.ImageFormat]::Bmp)
    $gfx.Dispose(); $bmp.Dispose()
    $gridPen.Dispose(); $majorPen.Dispose(); $glowPen.Dispose(); $perspPen.Dispose()

    # Set wallpaper style to Fill
    Set-ItemProperty -Path "HKCU:\Control Panel\Desktop" -Name "WallpaperStyle" -Value "10"
    Set-ItemProperty -Path "HKCU:\Control Panel\Desktop" -Name "TileWallpaper" -Value "0"

    # Apply wallpaper via Win32
    try {
        Add-Type @"
using System;
using System.Runtime.InteropServices;
public class CyberpunkWall {
    [DllImport("user32.dll", CharSet = CharSet.Auto)]
    public static extern int SystemParametersInfo(int uAction, int uParam, string lpvParam, int fuWinIni);
}
"@
    } catch {}  # type already loaded on repeat activations
    [CyberpunkWall]::SystemParametersInfo(0x0014, 0, $wallPath, 0x01 -bor 0x02) | Out-Null

    Write-Host "[$(Get-Date -Format 'HH:mm:ss')] Cyberpunk theme active" -ForegroundColor Magenta
}

# ---- Speaker Verification Helpers ----

function Save-RecognizedAudio {
    param(
        [System.Speech.Recognition.RecognizedAudio]$Audio,
        [string]$FilePath
    )
    $fileStream = [System.IO.FileStream]::new($FilePath, [System.IO.FileMode]::Create)
    try {
        $Audio.WriteToWaveStream($fileStream)
    } finally {
        $fileStream.Close()
    }
}

function Test-SpeakerVerification {
    param([string]$WavPath)

    # Skip verification if no voice profile is enrolled yet
    if (-not (Test-Path $voiceProfile)) {
        Write-Host "[$(Get-Date -Format 'HH:mm:ss')] No voice profile enrolled -- skipping speaker verification" -ForegroundColor Yellow
        return $true
    }

    try {
        $output = & $pythonExe $verifyScript verify --audio $WavPath --threshold $verifyThreshold 2>$null
        $json = $output | ConvertFrom-Json
        $score = [math]::Round($json.score * 100)
        if ($json.verified) {
            Write-Host "[$(Get-Date -Format 'HH:mm:ss')] Speaker VERIFIED (score: $score%)" -ForegroundColor Cyan
            return $true
        } else {
            Write-Host "[$(Get-Date -Format 'HH:mm:ss')] Speaker REJECTED (score: $score%)" -ForegroundColor Red
            return $false
        }
    } catch {
        Write-Host "[$(Get-Date -Format 'HH:mm:ss')] Speaker verification error: $_ -- rejecting" -ForegroundColor Red
        return $false
    }
}

function Invoke-WithVerification {
    param(
        [System.Speech.Recognition.RecognitionResult]$Result,
        [scriptblock]$Action
    )

    # Save recognized audio to temp WAV
    $wavPath = Join-Path $env:TEMP "computa_verify.wav"

    if ($Result.Audio -eq $null) {
        Write-Host "[$(Get-Date -Format 'HH:mm:ss')] No audio captured -- rejecting" -ForegroundColor Red
        return
    }

    Save-RecognizedAudio -Audio $Result.Audio -FilePath $wavPath

    # Verify speaker identity
    if (Test-SpeakerVerification -WavPath $wavPath) {
        & $Action
    }

    # Clean up temp file
    if (Test-Path $wavPath) { Remove-Item $wavPath -Force }
}

# ---- Speech Recognizer Setup ----

function Initialize-Listener {
    $maxRetries = 10
    for ($i = 1; $i -le $maxRetries; $i++) {
        try {
            $recognizer = New-Object System.Speech.Recognition.SpeechRecognitionEngine

            # Grammar 1: "computa activate" (original)
            $activateBuilder = New-Object System.Speech.Recognition.GrammarBuilder
            $activateBuilder.Append("computa activate")
            $activateGrammar = New-Object System.Speech.Recognition.Grammar($activateBuilder)
            $activateGrammar.Name = "activate"
            $recognizer.LoadGrammar($activateGrammar)

            $recognizer.SetInputToDefaultAudioDevice()
            return $recognizer
        } catch {
            Write-Host "[$(Get-Date -Format 'HH:mm:ss')] Audio not ready, retrying ($i/$maxRetries)..." -ForegroundColor Yellow
            Start-Sleep -Seconds 3
        }
    }
    Write-Host "Failed to initialize after $maxRetries attempts." -ForegroundColor Red
    return $null
}

$recognizer = Initialize-Listener
if ($recognizer -eq $null) { exit 1 }

# ---- Startup Banner ----

$profileExists = Test-Path $voiceProfile

Write-Host ""
Write-Host "========================================" -ForegroundColor Cyan
Write-Host "  COMPUTA LISTENER ACTIVE" -ForegroundColor Green
Write-Host "  Say 'computa activate' to launch" -ForegroundColor Green
Write-Host "  Claude Code in C:\Users\tenny" -ForegroundColor Green
Write-Host "  Confidence threshold: $([math]::Round($confidenceMin * 100))%" -ForegroundColor DarkCyan
if ($profileExists) {
    Write-Host "  Speaker verification: ON" -ForegroundColor Green
} else {
    Write-Host "  Speaker verification: OFF (no profile)" -ForegroundColor Yellow
    Write-Host "  Enroll with: python speaker_verify.py enroll" -ForegroundColor Yellow
}
Write-Host "  Press Ctrl+C to stop listening" -ForegroundColor Yellow
Write-Host "========================================" -ForegroundColor Cyan
Write-Host ""

# ---- Main Recognition Loop ----

while ($true) {
    try {
        $result = $recognizer.Recognize()
        if ($result -ne $null -and $result.Confidence -ge $confidenceMin) {
            $spoken = $result.Text
            $conf = [math]::Round($result.Confidence * 100)

            if ($spoken -eq "computa activate") {
                Write-Host "[$(Get-Date -Format 'HH:mm:ss')] Heard 'computa activate' (confidence: $conf%)" -ForegroundColor Green
                Invoke-WithVerification -Result $result -Action {
                    Set-CyberpunkTheme
                    Write-Host "[$(Get-Date -Format 'HH:mm:ss')] Launching Claude Code..." -ForegroundColor Green
                    Start-Process "wt.exe" -ArgumentList "-d", "C:\Users\tenny", "cmd", "/k", "set CLAUDECODE= && C:\Users\tenny\.local\bin\claude.exe"
                }
            }
        }
    } catch {
        Write-Host "Error: $_" -ForegroundColor Red
        # Re-initialize if the recognizer died
        $recognizer = Initialize-Listener
        if ($recognizer -eq $null) { exit 1 }
    }
}
