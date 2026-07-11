#!/usr/bin/env bash
# setup_assistant.sh — one-time Stage 3 setup
# Downloads the Piper voice model and checks Ollama is ready.
#
# Usage:
#   ./setup_assistant.sh                      # lessac-high (default, ~109 MB)
#   ./setup_assistant.sh --voice amy-medium   # smaller, faster (~11 MB)

set -e
REPO="$(cd "$(dirname "$0")" && pwd)"
VOICE="lessac-high"

for arg in "$@"; do
  case $arg in
    --voice) shift ;;
    amy-medium|lessac-high) VOICE="$arg" ;;
  esac
done

# ── Piper voice model ────────────────────────────────────────────────────────
MODEL_DIR="$REPO/models/piper"
mkdir -p "$MODEL_DIR"

case "$VOICE" in
  lessac-high)
    BASE_URL="https://huggingface.co/rhasspy/piper-voices/resolve/v1.0.0/en/en_US/lessac/high"
    FNAME="en_US-lessac-high"
    ;;
  amy-medium)
    BASE_URL="https://huggingface.co/rhasspy/piper-voices/resolve/v1.0.0/en/en_US/amy/medium"
    FNAME="en_US-amy-medium"
    ;;
  *)
    echo "Unknown voice '$VOICE'. Choose: lessac-high | amy-medium"
    exit 1 ;;
esac

ONNX="$MODEL_DIR/$FNAME.onnx"
JSON="$MODEL_DIR/$FNAME.onnx.json"

if [[ -f "$ONNX" && -f "$JSON" ]]; then
  echo "Voice model already downloaded: $FNAME"
else
  echo "Downloading Piper voice: $FNAME ..."
  wget -q --show-progress -O "$ONNX" "$BASE_URL/$FNAME.onnx"
  wget -q -O "$JSON" "$BASE_URL/$FNAME.onnx.json"
  echo "Saved to $MODEL_DIR/"
fi

# Update voice_commands.yaml model_path if different from default
if [[ "$VOICE" != "lessac-high" ]]; then
  sed -i "s|en_US-lessac-high.onnx|$FNAME.onnx|g" "$REPO/voice_commands.yaml"
  echo "Updated voice_commands.yaml to use $FNAME"
fi

# ── Ollama ───────────────────────────────────────────────────────────────────
if ! command -v ollama &>/dev/null; then
  echo ""
  echo "Ollama not found. Install it with:"
  echo "  curl -fsSL https://ollama.com/install.sh | sh"
  echo "Then pull the model:"
  echo "  ollama pull qwen3:4b"
  exit 1
fi

MODEL=$(python3 -c "
import yaml, sys
cfg = yaml.safe_load(open('$REPO/voice_commands.yaml'))
print(cfg.get('assistant',{}).get('llm',{}).get('model','qwen3:4b'))
" 2>/dev/null || echo "qwen3:4b")

if ollama list 2>/dev/null | grep -q "^${MODEL}"; then
  echo "Ollama model already pulled: $MODEL"
else
  echo "Pulling Ollama model: $MODEL (this may take a while)..."
  ollama pull "$MODEL"
fi

echo ""
echo "Stage 3 setup complete."
echo "  Voice : $FNAME"
echo "  Model : $MODEL"
echo "  Run   : ./run.sh"
