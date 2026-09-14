# Breach Flight — G14 player-flight slice

Panda3D 1.10.16 / Python 3.12, Linux-first. Fly from inside the ship using
assisted inertial flight and a fixed 60 Hz simulation. Enemy AI and the
dangerous capital ship ARE delivered (node 5): three fighters pursue and attack
under the exact same flight/weapon/impairment/repair rules as the player, and an
anchored capital with a turret battery fights back and needs ~40–50 s of
sustained fire to destroy. Win/lose loop and a packaged game are not yet
delivered. Combat weapons and hit resolution ARE delivered (node 4): the player
fires three distinct weapons in real time, projectiles resolve hits/misses
against ship hitboxes, and every hit routes through the shared DamageSystem.
Accumulated hull/sub-system damage and repair ARE delivered (node 3): the
player's condition drives live thrust/turning/weapons performance through the
shared DamageSystem, and enemies degrade/repair under the same model.

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
    R: hold to repair selected subsystem (brakes + locks)
    1/2/3 engine/weapons/sensors | Space/LMB fire | Q cycle weapon
    Tab: target lock | reticle lights when the locked target is in range

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
Hold R to repair the selected subsystem (1/2/3 choose engine/weapons/sensors):
flight brakes to a stop and locks turning; once below 2 m/s the subsystem is
restored toward 70% over about 4 s. Any hit interrupts repair without undoing
already-restored HP, so repairing is a deliberate, exposed choice.

Space/LMB fires the selected weapon; Q cycles cannon -> scatter -> torpedo.
The cannon is a rapid low-damage stream, scatter is a short-range shotgun, and
the torpedo is a slow heavy hit. Heat builds with sustained fire and cools
between shots. Tab cycles target lock; the reticle turns green when the locked
target is a fire solution. Damage to your weapons subsystem lowers damage per
shot and a destroyed weapons subsystem stops firing; repair blocks firing too.

Enemy fighters steer toward you, throttle down to turn, open fire inside range
and a nose cone, and — when crippled — brake to a stop and try a vulnerable,
interruptible repair you can punish. Their engine damage slows them, their
weapons damage weakens their shots, and a destroyed weapons subsystem silences
them (no hidden recovery). The capital anchors in place and returns fire from a
turret battery; damage its weapons to blunt it, or keep up fire to stop it
repairing. Enemy meshes darken from healthy to smoking to burning as they take
hull damage, then drop out when destroyed.

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
independently pursues a reference fighter through XTest keys at 30/144 Hz
render limiters, drives a scripted damage/repair timeline through the live
DamageSystem, runs the deterministic combat simulation (miss/track/sustain/
no-one-hit-kill/frame-interval checks) and two real offscreen combat captures
(hit and miss), then runs the enemy AI simulation (pursuit/attack/impairment/
repair entry-exit-abort/destruction/capital) and two real offscreen enemy
encounters (live opposition + a scripted repair window) validated from the
trace and code-level PNG sampling. Actual receipt, simulation ticks, poses,
input decisions, renderer reports, health/state transitions, weapon/hit and
enemy telemetry, PNGs and hashes are retained.
Exit nonzero means failure.

Optional visible-instructions check (install tesseract-ocr first):

    .venv-verify/bin/python tools/verify_controls.py --capture artifacts/local/input-flight/flight/frame-0020.png --output artifacts/local/controls-default

For a trace of your own desktop flight:

    uv run --project . --frozen breach-flight --mute --frames 900 --trace-dir artifacts/my-flight --capture-frames 60,180,360,600,900 --report artifacts/my-flight-report.json

F10 quits the unlimited interactive run. --capture/--report/--trace-dir require
positive --frames; --offscreen uses real GLX but still needs DISPLAY and cannot
validate window input. The test host uses llvmpipe, not physical GPU evidence.

## Evidence and handoff

Current flight: docs/FLIGHT.md and artifacts/flight-release/.
Damage/repair: docs/DAMAGE.md and artifacts/damage-release/.
Weapons/hit resolution: docs/WEAPONS.md and artifacts/weapons-release/.
Foundation history: docs/EVIDENCE.md and artifacts/initial, independent.
Interfaces/ownership: docs/INTEGRATION.md. Asset policy: docs/ASSETS.md.
No human desktop playtest, audible playback, laptop performance, native Wayland,
Windows support, final game quality or mission-tree completion is claimed.
