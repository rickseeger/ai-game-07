# Breach Flight — G14 Linux foundation

A real Panda3D 1.10.16 / Python 3.12 application, not yet a game. Original
procedural, lit 3D meshes stage a capital ship, fighters and a repair/dock frame.
Space changes the inspection camera; Escape exits. Flight, combat, cockpit,
HUD/radar, effects, tuning and distribution belong to the remaining nodes.

## Setup and launch (from repository root)

Install uv using https://docs.astral.sh/uv/getting-started/installation/ .
On Debian/Ubuntu the runtime/test OS prerequisites are:

    sudo apt-get install xvfb libgl1 libglx-mesa0 libx11-6 libxtst6 libopenal1
    uv python install 3.12
    uv sync --project . --frozen
    uv run --project . --frozen breach-flight

Launch on a Linux graphical desktop (DISPLAY provided by X11 or XWayland).
Use --mute when no audio output is configured. No sound assets are played yet.
The source launch path requires network on first setup; the lock pins dependencies
and wheel hashes. No system Python packages or harness dependencies are needed.
Do not run uv until pyproject.toml is present; always stay in this repository.

## Tests, real rendering, and independent setup

    uv run --project . --frozen python -m unittest discover -s tests -v
    python3 tools/headless.py --venv .venv-verify --output artifacts/local

The second command starts a private Xvfb, verifies its X11 connection, installs
from uv.lock into the specified environment, runs unit smoke tests, then opens a
real GLX window and a separate GLX offscreen buffer. It sends an actual XTest
Space event through X11 to Panda, asserts receipt and a changed framebuffer,
and checks the central 3D viewport has nontrivial geometry pixels. It writes
setup/tests/runtime/Xvfb logs, environment inventory, reports and PNG captures.
It exits nonzero on failures and always tears down its X server. Use a new --venv
path for a clean environment rerun. Xvfb is software rendering, not a GPU test.

On an existing desktop, one deterministic capture command is:

    uv run --project . --frozen breach-flight --mute --frames 180 --capture artifacts/local/manual.png --report artifacts/local/manual.json

Add --offscreen to render to an actual GLX buffer instead of opening a window;
it still needs DISPLAY in this GLX configuration. The default interactive run
continues until Escape. Capture/report options require a positive --frames.

## Evidence and handoff

See docs/INTEGRATION.md for interfaces, design and ownership; docs/EVIDENCE.md
for measured capabilities and limitations; docs/ASSETS.md for asset policy.
Committed artifacts/initial and artifacts/independent contain successful runs.
artifacts/audio-probe records the genuine audio-device failure and graceful
fallback; that exit code does NOT mean audible sound was verified.

No binary distribution, playability, human-input, audible playback, or final
mission completion is claimed by this foundation.
