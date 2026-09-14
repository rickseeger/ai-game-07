"""Regenerate the redistributable sound assets from src/breach/synth.py.

Writes every synthesized sound to src/breach/audio/<name>.wav (16-bit mono
48 kHz PCM) and a manifest of lengths/peak levels for provenance. Deterministic:
re-running produces byte-identical files.
"""
import hashlib
import json
from pathlib import Path

from breach.synth import SAMPLE_RATE, synth_all, write_wav

ROOT = Path(__file__).resolve().parents[1]
AUDIO_DIR = ROOT / "src" / "breach" / "audio"


def main():
    AUDIO_DIR.mkdir(parents=True, exist_ok=True)
    manifest = {}
    for name, samples in synth_all().items():
        path = AUDIO_DIR / f"{name}.wav"
        write_wav(path, samples)
        raw = path.read_bytes()
        peak = max(abs(s) for s in samples)
        manifest[name] = {
            "path": f"src/breach/audio/{name}.wav",
            "duration_s": round(len(samples) / SAMPLE_RATE, 4),
            "samples": len(samples),
            "peak": round(peak, 4),
            "bytes": len(raw),
            "sha256": hashlib.sha256(raw).hexdigest(),
        }
    (AUDIO_DIR / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")
    total = sum(m["bytes"] for m in manifest.values())
    print(f"GENERATED {len(manifest)} sounds -> {AUDIO_DIR} ({total} bytes)")


if __name__ == "__main__":
    main()
