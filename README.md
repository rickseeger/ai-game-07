# Breach Flight — G14 player-flight slice

Panda3D 1.10.16 / Python 3.12, Linux-first. Fly from inside the ship using
assisted inertial flight and a fixed 60 Hz simulation. The capital, dock and
orange fighters are stationary navigation references, not working enemies.
No combat, damage, repair progress, collisions, win/lose loop or packaged game
is delivered here. The old Space inspection camera has been removed.

## Setup and launch (repository root)

Install uv: https://docs.astral.sh/uv/getting-started/installation/ .
Debian/Ubuntu prerequisites:

    sudo apt-get install xvfb libgl1 libglx-mesa0 libx11-6 libxtst6 libopenal1
    uv python install 3.12
    uv sync --project . --frozen
    uv run --project . --frozen breach-flight --mute

Launch on a graphical Linux desktop (X11 or XWayland DISPLAY). No sound assets
are played yet. First setup can require network. Stay in this repository and
use --project; no system Python/harness dependency changes are needed.

## Displayed controls

    W/S raise/lower throttle | B brake and zero throttle
    A/D roll left/right | Arrows: nose up/down/left/right
    Mouse: aim stick (up = nose up) | C: center aim
    Hold Home: level horizon | Esc: pause/resume | F10: quit
    R: flight interlock only (brakes + locks turning; no repair yet)
    Space/LMB, Tab, 1/2/3: reserved inputs (no combat yet)

Hold W to increase throttle; releasing it retains the setting. S lowers the
setting, not reverse thrust. Idle drag slows a zero-throttle ship gradually;
B deliberately stops it and zeros throttle even if W remains held. Arrows
point the nose, A/D bank; opposite keys cancel, different axes combine.

Mouse is a bounded virtual aiming stick, not free look: moving right/up sets
right/up turning, and the orange circle shows stick deflection. It stays there
until moved back or centered with C. The camera always points with the ship.
Hold Home to recover pitch and roll to the horizon while retaining compass
heading; this overrides turn axes and centers mouse aim. Hold B too to stop.
Static stars in every direction and fixed cockpit sills provide motion cues.

Escape pauses/resumes and releases/captures the pointer. Losing window focus
clears held inputs and mouse aim, freezes flight, and requires Escape after
focus returns. Resuming retains prior velocity/throttle; press B to stop.
R currently only demonstrates the flight-side interlock, NOT healing anything.

Options: --mouse-sensitivity 0.006 (default 0.012), --invert-y,
--keyboard-only (no pointer capture), --render-hz 30 (15–240; simulation always
60 Hz). The HUD updates its mouse instructions for inversion/keyboard-only.
Bindings live in src/breach/input.py; no remapping UI or controller support.

## Automated and real-runtime validation

    uv run --project . --frozen python -m unittest discover -s tests -v
    python3 tools/headless.py --venv .venv-verify --output artifacts/local

Use a fresh output directory on every run; flight evidence refuses overwrite.
This health-checks a private Xvfb, installs the lock into the requested venv,
runs unit tests, captures window/offscreen GLX, injects a 520-frame XTest flight,
then independently pursues a reference fighter through XTest keys at 30/144 Hz
render limiters. Actual receipt, simulation ticks, poses, input decisions,
renderer reports, PNGs and hashes are retained. Exit nonzero means failure.

Optional visible-instructions check (install tesseract-ocr first):

    .venv-verify/bin/python tools/verify_controls.py --capture artifacts/local/input-flight/flight/frame-0020.png --output artifacts/local/controls-default

For a trace of your own desktop flight:

    uv run --project . --frozen breach-flight --mute --frames 900 --trace-dir artifacts/my-flight --capture-frames 60,180,360,600,900 --report artifacts/my-flight-report.json

F10 quits the unlimited interactive run. --capture/--report/--trace-dir require
positive --frames; --offscreen uses real GLX but still needs DISPLAY and cannot
validate window input. The test host uses llvmpipe, not physical GPU evidence.

## Evidence and handoff

Current flight: docs/FLIGHT.md and artifacts/flight-release/.
Foundation history: docs/EVIDENCE.md and artifacts/initial, independent.
Interfaces/ownership: docs/INTEGRATION.md. Asset policy: docs/ASSETS.md.
No human desktop playtest, audible playback, laptop performance, native Wayland,
Windows support, final game quality or mission-tree completion is claimed.
