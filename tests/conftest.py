"""Make the repo root importable so  from src.assistant.tts import ...  works."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
