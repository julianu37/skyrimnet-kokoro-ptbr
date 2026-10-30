"""Verify the Kokoro model loads and the pt-BR voices exist."""
from pathlib import Path
import sys

from kokoro_onnx import Kokoro

ROOT = Path(__file__).resolve().parents[1]
MODEL = ROOT / "models" / "kokoro-v1.0.onnx"
VOICES = ROOT / "models" / "voices-v1.0.bin"

for path in (MODEL, VOICES):
    if not path.exists():
        sys.exit(f"missing: {path}")

kokoro = Kokoro(str(MODEL), str(VOICES))
available = set(kokoro.get_voices())
required = {"pf_dora", "pm_alex", "pm_santa"}
missing = required - available
if missing:
    sys.exit(f"missing pt-BR voices: {sorted(missing)}\navailable: {sorted(available)}")

print(f"model ok, {len(available)} voices, pt-BR: {sorted(required)}")
