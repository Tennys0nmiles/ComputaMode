#!/usr/bin/env bash
# setup.sh — one-time full setup for ComputaMode
#
# What this does:
#   1. Checks system dependencies (xdotool, portaudio, ollama)
#   2. Detects CPU cores + free RAM + GPU to recommend a model
#   3. Pulls the chosen Ollama model and writes it to voice_commands.yaml
#   4. Downloads the Piper TTS voice model
#
# Safe to re-run (idempotent — skips steps already done).
#
# Usage:
#   ./setup.sh                          # interactive, auto-detects hardware
#   ./setup.sh --model llama3.2:3b      # skip detection, force a model
#   ./setup.sh --voice amy-medium       # use smaller/faster Piper voice

set -e
REPO="$(cd "$(dirname "$0")" && pwd)"

# ── Argument parsing ──────────────────────────────────────────────────────────
FORCE_MODEL=""

while [[ $# -gt 0 ]]; do
  case "$1" in
    --model)   FORCE_MODEL="$2"; shift 2 ;;
    --model=*) FORCE_MODEL="${1#--model=}"; shift ;;
    *) echo "Unknown option: $1"; exit 1 ;;
  esac
done

# ── Helpers ───────────────────────────────────────────────────────────────────
info()    { echo "  $*"; }
ok()      { echo "  [ok] $*"; }
warn()    { echo "  [!]  $*"; }
header()  { echo ""; echo "── $* ──────────────────────────────────────────────"; }

# ── 1. System dependencies ────────────────────────────────────────────────────
header "System dependencies"

MISSING=()
command -v xdotool   &>/dev/null && ok "xdotool found"    || MISSING+=("xdotool")
command -v ollama    &>/dev/null && ok "ollama found"      || MISSING+=("ollama")
command -v espeak-ng &>/dev/null && ok "espeak-ng found"   || MISSING+=("espeak-ng")
python3 -c "import sounddevice" &>/dev/null 2>&1           || true  # checked later via pip

if dpkg -l portaudio19-dev &>/dev/null 2>&1; then
  ok "portaudio19-dev installed"
else
  MISSING+=("portaudio19-dev")
fi

if [[ ${#MISSING[@]} -gt 0 ]]; then
  echo ""
  warn "Missing dependencies: ${MISSING[*]}"
  echo ""
  for dep in "${MISSING[@]}"; do
    case "$dep" in
      xdotool|portaudio19-dev|espeak-ng)
        echo "  Install with: sudo apt install $dep" ;;
      ollama)
        echo "  Install Ollama: curl -fsSL https://ollama.com/install.sh | sh" ;;
    esac
  done
  echo ""
  read -rp "  Continue anyway? [y/N] " yn
  [[ "$yn" =~ ^[Yy]$ ]] || exit 1
fi

# ── 2. Hardware detection + model recommendation ──────────────────────────────
header "Hardware detection"

CPU_CORES=$(nproc 2>/dev/null || echo 4)
# Total RAM in MB
TOTAL_RAM_MB=$(awk '/MemTotal/ {print int($2/1024)}' /proc/meminfo 2>/dev/null || echo 4096)
TOTAL_RAM_GB=$(( TOTAL_RAM_MB / 1024 ))
# Available RAM (free + buffers/cache) in MB
FREE_RAM_MB=$(awk '/MemAvailable/ {print int($2/1024)}' /proc/meminfo 2>/dev/null || echo 2048)
FREE_RAM_GB=$(( FREE_RAM_MB / 1024 ))

info "CPU cores    : $CPU_CORES"
info "Total RAM    : ${TOTAL_RAM_GB} GB"
info "Free RAM     : ${FREE_RAM_GB} GB"

# GPU detection
GPU_VRAM_GB=0
GPU_NAME="none detected"

if command -v nvidia-smi &>/dev/null; then
  VRAM_MB=$(nvidia-smi --query-gpu=memory.total --format=csv,noheader,nounits 2>/dev/null | head -1 | tr -d ' ')
  if [[ -n "$VRAM_MB" && "$VRAM_MB" =~ ^[0-9]+$ ]]; then
    GPU_VRAM_GB=$(( VRAM_MB / 1024 ))
    GPU_NAME=$(nvidia-smi --query-gpu=name --format=csv,noheader 2>/dev/null | head -1)
    info "GPU          : $GPU_NAME (${GPU_VRAM_GB} GB VRAM)"
  fi
elif command -v rocm-smi &>/dev/null; then
  # AMD GPU via ROCm — rough VRAM read
  VRAM_MB=$(rocm-smi --showmeminfo vram 2>/dev/null | awk '/Total Memory/ {print int($NF/1048576)}' | head -1)
  if [[ -n "$VRAM_MB" && "$VRAM_MB" =~ ^[0-9]+$ ]]; then
    GPU_VRAM_GB=$(( VRAM_MB / 1024 ))
    GPU_NAME="AMD GPU (ROCm)"
    info "GPU          : $GPU_NAME (${GPU_VRAM_GB} GB VRAM)"
  fi
else
  info "GPU          : CPU-only (no nvidia-smi / rocm-smi found)"
fi

# ── Model recommendation ──────────────────────────────────────────────────────
header "Model recommendation"

echo "  Hardware → recommended Ollama model:"
echo ""
echo "  ┌─────────────────────────────────┬────────────────────┬─────────────────────┐"
echo "  │ Hardware                        │ Model              │ Size / notes        │"
echo "  ├─────────────────────────────────┼────────────────────┼─────────────────────┤"
echo "  │ GPU ≥ 8 GB VRAM                 │ qwen3:4b           │ ~2.6 GB, best       │"
echo "  │ CPU-only, ≥ 10 GB free RAM      │ qwen3:4b           │ ~2.6 GB, good       │"
echo "  │ CPU-only,  6–9 GB free RAM      │ llama3.2:3b        │ ~2.0 GB, fast       │"
echo "  │ CPU-only, ≤ 5 GB free RAM       │ llama3.2:1b        │ ~1.3 GB, minimal    │"
echo "  └─────────────────────────────────┴────────────────────┴─────────────────────┘"
echo ""

if [[ -n "$FORCE_MODEL" ]]; then
  RECOMMENDED="$FORCE_MODEL"
  info "Using --model override: $RECOMMENDED"
elif [[ "$GPU_VRAM_GB" -ge 8 ]]; then
  RECOMMENDED="qwen3:4b"
  info "GPU with ${GPU_VRAM_GB} GB VRAM → recommending qwen3:4b"
elif [[ "$FREE_RAM_GB" -ge 10 ]]; then
  RECOMMENDED="qwen3:4b"
  info "CPU-only, ${FREE_RAM_GB} GB free RAM → recommending qwen3:4b"
elif [[ "$FREE_RAM_GB" -ge 6 ]]; then
  RECOMMENDED="llama3.2:3b"
  info "CPU-only, ${FREE_RAM_GB} GB free RAM → recommending llama3.2:3b"
else
  RECOMMENDED="llama3.2:1b"
  info "CPU-only, ${FREE_RAM_GB} GB free RAM → recommending llama3.2:1b (low-RAM mode)"
fi

echo ""
read -rp "  Model to use [$RECOMMENDED]: " USER_MODEL
MODEL="${USER_MODEL:-$RECOMMENDED}"
echo "  Selected: $MODEL"

# ── 3. Pull Ollama model ──────────────────────────────────────────────────────
header "Ollama model"

if ! command -v ollama &>/dev/null; then
  warn "ollama not installed — skipping model pull. Install it and re-run."
else
  if ollama list 2>/dev/null | grep -q "^${MODEL}"; then
    ok "Model already pulled: $MODEL"
  else
    echo "  Pulling $MODEL (this may take a few minutes)..."
    ollama pull "$MODEL"
    ok "Pulled: $MODEL"
  fi

  # Write chosen model back to voice_commands.yaml
  if command -v python3 &>/dev/null; then
    python3 - "$REPO/voice_commands.yaml" "$MODEL" <<'PYEOF'
import sys, re

yaml_path, new_model = sys.argv[1], sys.argv[2]
with open(yaml_path) as f:
    text = f.read()

# Replace  model: <anything>  under the assistant.llm block
# We target the line that starts with optional whitespace, "model:", and
# follows after the "llm:" header (which appears before "tts:").
# Simple line-by-line replacement: find the line inside the llm block.
lines = text.splitlines(keepends=True)
in_llm = False
result = []
for line in lines:
    stripped = line.lstrip()
    if stripped.startswith("llm:"):
        in_llm = True
    elif in_llm and stripped.startswith("tts:"):
        in_llm = False
    if in_llm and re.match(r'\s+model:\s+\S', line):
        indent = len(line) - len(line.lstrip())
        line = " " * indent + "model: " + new_model + "\n"
        in_llm = False  # only replace the first match
    result.append(line)

with open(yaml_path, "w") as f:
    f.writelines(result)
print(f"  Updated voice_commands.yaml: assistant.llm.model = {new_model}")
PYEOF
  else
    warn "python3 not found — please manually set 'model: $MODEL' in voice_commands.yaml under assistant.llm"
  fi
fi

# ── 4. Kokoro TTS model (kokoro-onnx, CPU-only, 24 kHz) ──────────────────────
header "Kokoro TTS model"

# Pinned versions — update here if you need a newer model release.
KOKORO_MODEL_VERSION="v1.0"
KOKORO_MODEL_FILE="kokoro-${KOKORO_MODEL_VERSION}.onnx"
KOKORO_VOICES_FILE="voices-${KOKORO_MODEL_VERSION}.bin"
KOKORO_BASE_URL="https://github.com/thewh1teagle/kokoro-onnx/releases/download/model-files-v1.0"

KOKORO_DIR="$REPO/models/kokoro"
mkdir -p "$KOKORO_DIR"

if [[ -f "$KOKORO_DIR/$KOKORO_MODEL_FILE" ]]; then
  ok "$KOKORO_MODEL_FILE already present"
else
  echo "  Downloading $KOKORO_MODEL_FILE (~311 MB) ..."
  wget -q --show-progress -O "$KOKORO_DIR/$KOKORO_MODEL_FILE" \
    "$KOKORO_BASE_URL/$KOKORO_MODEL_FILE"
  ok "Saved to $KOKORO_DIR/$KOKORO_MODEL_FILE"
fi

if [[ -f "$KOKORO_DIR/$KOKORO_VOICES_FILE" ]]; then
  ok "$KOKORO_VOICES_FILE already present"
else
  echo "  Downloading $KOKORO_VOICES_FILE (~27 MB) ..."
  wget -q --show-progress -O "$KOKORO_DIR/$KOKORO_VOICES_FILE" \
    "$KOKORO_BASE_URL/$KOKORO_VOICES_FILE"
  ok "Saved to $KOKORO_DIR/$KOKORO_VOICES_FILE"
fi

# ── Legacy Piper (kept for reference; uncomment to restore) ───────────────────
# PIPER_DIR="$REPO/models/piper"
# mkdir -p "$PIPER_DIR"
# PIPER_BASE="https://huggingface.co/rhasspy/piper-voices/resolve/v1.0.0/en/en_US/lessac/high"
# PIPER_FNAME="en_US-lessac-high"
# wget -q --show-progress -O "$PIPER_DIR/$PIPER_FNAME.onnx" "$PIPER_BASE/$PIPER_FNAME.onnx"
# wget -q -O "$PIPER_DIR/$PIPER_FNAME.onnx.json" "$PIPER_BASE/$PIPER_FNAME.onnx.json"

# ── 5. V2 voice interpreter dependencies ─────────────────────────────────────
header "V2 voice interpreter (rapidfuzz + sentence-transformers)"

pip install rapidfuzz sentence-transformers --quiet

python3 -c "
from sentence_transformers import SentenceTransformer
m = SentenceTransformer('all-MiniLM-L6-v2')
assert m.encode(['test']).shape == (1, 384)
print('[ok] all-MiniLM-L6-v2 ready')
"

PYTHONPATH="${REPO}" python3 -c "
from src.voice.registry import Registry
r = Registry('${REPO}/voice_commands.yaml')
r.precompute_embeddings()
print('[ok] embedding cache written')
"

# ── Done ──────────────────────────────────────────────────────────────────────
echo ""
echo "────────────────────────────────────────────────────────────────────────"
echo "  Setup complete."
echo ""
echo "  LLM   : $MODEL"
echo "  Voice : Kokoro $KOKORO_MODEL_VERSION (default: af_nicole)"
echo "          Edit  assistant.tts.voice  in voice_commands.yaml to change."
echo ""
echo "  Next steps:"
echo "    source venv/bin/activate"
echo "    ./run.sh              # full assistant"
echo "    ./run.sh --no-assistant  # voice commands only, no LLM"
echo "────────────────────────────────────────────────────────────────────────"
