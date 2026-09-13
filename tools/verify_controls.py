"""OCR actual HUD pixels, not application metadata. Requires tesseract."""
import argparse
import hashlib
import json
from pathlib import Path
import subprocess
from panda3d.core import PNMImage


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--capture", type=Path, required=True)
    p.add_argument("--output", type=Path, required=True)
    p.add_argument("--keyboard-only", action="store_true")
    args = p.parse_args()
    args.output.mkdir(parents=True, exist_ok=False)
    image = PNMImage()
    assert image.read(str(args.capture.resolve()))
    assert (image.getXSize(), image.getYSize()) == (960, 540)
    crop = PNMImage(960, 155)
    crop.copySubImage(image, 0, 0, 0, 385, 960, 155)
    large = PNMImage(2880, 465)
    large.quickFilterFrom(crop)
    path = args.output / "hud-crop-3x.png"
    assert large.write(str(path.resolve()))
    command = ["tesseract", str(path), "stdout", "--psm", "6"]
    result = subprocess.run(command, capture_output=True, text=True, check=True)
    (args.output / "ocr.txt").write_text(result.stdout)
    (args.output / "ocr.log").write_text(result.stderr)
    text = result.stdout.lower()
    phrases = ["raise/lower throttle", "brake and zero throttle", "roll", "arrows",
               "level horizon", "pause/resume", "quit", "flight interlock",
               "locks turning", "no repair yet", "reserved inputs", "no combat yet"]
    phrases += ["disabled", "arrow keys"] if args.keyboard_only else ["aim stick", "nose up", "center aim"]
    for phrase in phrases:
        assert phrase in text, (phrase, result.stdout)
    report = dict(status="passed", source=str(args.capture),
                  source_sha256=hashlib.sha256(args.capture.read_bytes()).hexdigest(),
                  command=command, recognized_phrases=phrases,
                  method="Only crop and 3x resample of the genuine framebuffer; no synthesized/replaced text. Original PNG retained.",
                  limitation="OCR verifies visible instructions; runtime traces and unit sign tests verify their behavior. Not a human readability review.")
    (args.output / "summary.json").write_text(json.dumps(report, indent=2) + "\n")
    print("CONTROLS_OCR_PASS " + json.dumps(report))

if __name__ == "__main__": main()
