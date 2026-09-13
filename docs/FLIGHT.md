# Node 2: accessible flight and measured evidence

## Recovery and scope

Inspected /opt/g-harness/workspace/G14/node_2_step_120/repo before editing.
Local and fetched remote history contained only foundation efa7a44. Recovered
uncommitted flight/input/camera/timing modules, tests, integration and external
pilot rather than discarding them. Copied work here without its environment;
the original workspace is untouched. artifacts/flight-recovery.tar.gz preserves
the original tracked diff, untracked source/tests/evidence and current failed runs.

Recovered code already contained the core mechanics. This dispatch fixed an
incorrect expected key count (now derived from the injection schedule), a
window-mapping race that disabled mouse aim on frame 1, and sparse pixel sampling
that missed real stars. Added all-direction static navigation references, larger
opaque-backed controls, settings-correct instructions, an independent feedback
pilot and pixel-based OCR. Failure history is retained, not relabeled as success:
recovered-validation failed the input count; flight-final failed initial pointer
availability; flight-accepted failed sparse viewport sampling. Only flight-release
is accepted. A documentation-writing command also failed with a quoting SyntaxError
before writing; it was corrected, without any application/environment changes.

## Implementation and integration

input.py: engine-independent held keys, aliases, opposed-axis cancellation,
edge consumption, bounded virtual mouse stick, soft deadzone, sensitivity,
inversion, focus clearing and pause. Full keyboard flight is available.
flight.py: assisted momentum, signed throttle-rate requests, acceleration/speed
limits, body-local quaternion turns, brake override, bounded horizon recovery,
injectable performance and repair-request interlock; detached frozen ShipView.
timing.py: 60 Hz fixed ticks, real_dt clamp 0.1 s, six-step budget, explicit drop
counters, interpolation and paused backlog clearing. At stalls above 100 ms it
intentionally drops time; it does not silently simulate an arbitrarily long stall.
camera.py: interpolated ship presentation parent; local eye (0,0,0.65), identity
local orientation, inside hull bounds (-1.2,-2,-0.5)..(1.2,2,1.4). No chase offset,
free-look, shake or spring. Camera-attached sills provide cockpit reference.
See INTEGRATION.md for the constructor-injected damage-service adapter boundary.
R only demonstrates the flight-side lock, not repair progress or healing.

## Exact accepted commands (repository root)

    python3 tools/headless.py --venv .venv-verify --output artifacts/flight-release
    .venv-verify/bin/python tools/verify_controls.py --capture artifacts/flight-release/input-flight/flight/frame-0020.png --output artifacts/flight-release/controls-default
    .venv-verify/bin/python tools/verify_controls.py --capture artifacts/flight-release/independent-pilot/30/flight/frame-0030.png --output artifacts/flight-release/controls-keyboard --keyboard-only

All returned exit 0. .venv-verify was absent, installed from uv.lock. The wrapper
health-checks private Xvfb, runs uv sync --project <absolute repo> --frozen, then
that environment Python runs unittest discover -s tests -v, runtime_smoke.py,
flight_runtime.py and independent_flight.py. Exact child commands are retained
in setup/tests/runtime logs and pilot command JSON. OCR requires tesseract-ocr.

28 unit tests passed: all directions, simultaneous/opposed keys, aliases, edges,
deadzone/mouse partitioning/inversion/remap/settings help, focus/pause clearing,
quaternion signs/body-local axes, thrust/pursuit/brake, normalized combined turns,
upright recovery including inverted/vertical starts, injected performance,
repair-request interlock, frozen snapshots, dt validation, accumulator budgets,
interpolation and camera anchor. 30/60/144 Hz and jittered .005/.025/.01/.06 s
intervals yield exactly the same 360-tick ShipView with identical per-tick inputs.
A .5 s stall executes six ticks and explicitly records .4 s dropped.

## Real runtime and independent flight

Actual Linux X11/GLX, Panda3D 1.10.16, Mesa llvmpipe, 960x540. Window and separate
offscreen buffer each ran 180 frames. XTest Right-arrow reached Panda and changed
the framebuffer. No mocked renders, images or synthesized input receipt.

The scripted 520-frame pilot sends XTest key/button/mouse/focus events through
X11 -> Panda -> InputAdapter -> fixed flight. Every received tick is additionally
replayed against FlightSystem, not as a substitute for the runtime test. Validated
W pursuit, S reduction, retained throttle, combined yaw/pitch/roll/W, braking,
held-control focus loss, freeze through focus return until Esc, explicit pause,
mouse up/right steering, C center, Home upright recovery, opposed throttle,
reserved Space/LMB and flight-side R lock/release. Every rendered camera pose
matches ship pose plus rotated local eye, with identity local camera rotation.

Eight original PNGs in input-flight/flight at frames 20,100,140,190,240,300,410,470.
Trace correlates controls/ticks/poses/camera/capture. Stopped final two views
correctly have identical hashes. Full central viewport pixels are checked;
a four-pixel sampling grid missed tiny genuine stars in earlier validation.

A second independent external pilot does not reuse the regression schedule. It
yaws/rolls away, holds Home, then reads published pose and sends ONLY XTest arrows
and W to align with and approach the orange reference fighter at (21,63,-5),
finally braking near it. Target is stationary staging geometry, NOT enemy AI.
Two actual render intervals were exercised: 30 and 144 Hz limiters. Both pilots
recovered upright, aligned within 3 degrees in pitch and yaw during pursuit,
reduced range by over 20 m and stopped. Their different final ranges are normal
input-event sampling differences; exact tick-input equivalence is covered by
unit tests, not falsely inferred from wall-time X11 events.

The following measurements are copied programmatically from successful reports:

{
  "scripted": {
    "status": "passed",
    "frames": 520,
    "simulation_ticks": 495,
    "seed": 14,
    "input_events": 23,
    "mouse_events": 1,
    "mouse_modes": [
      0
    ],
    "peak_speed": 26.58799855026832,
    "stop_speed": 0.0,
    "camera_max_anchor_error": 0.0,
    "renderer": "llvmpipe (LLVM 21.1.8, 256 bits)",
    "dropped_seconds": 0.7139490000000001
  },
  "independent": [
    {
      "status": "passed",
      "render_hz": 30,
      "frames": 270,
      "ticks": 551,
      "median_frame_dt": 0.033333999999999975,
      "initial_range": 81.27730560302734,
      "final_range": 16.584430694580078,
      "final_speed": 0.0
    },
    {
      "status": "passed",
      "render_hz": 144,
      "frames": 1296,
      "ticks": 560,
      "median_frame_dt": 0.006944999999999979,
      "initial_range": 81.27730560302734,
      "final_range": 16.758073806762695,
      "final_speed": 0.0
    }
  ]
}

## Displayed instructions and artifacts

controls-default and controls-keyboard contain genuine HUD crop/3x-resample PNGs,
Tesseract raw OCR, logs and reports. These are explicitly derived inspection aids;
original renderer PNGs remain untouched. OCR recognizes throttle/brake, roll/arrows,
mouse aim/center (or keyboard-only disabled), level, pause/quit, R interlock and
reserved/no-combat instructions. Behavior is backed by runtime traces and unit
sign tests, not merely matching application metadata to itself. HUD help changes
for inversion/keyboard-only; default strings are documented verbatim in README.
This is an automated readability/content check, not a human comfort assessment.

All accepted evidence lives under artifacts/flight-release: environment/setup,
tests, window/offscreen images/reports/logs, input-flight trace/injections/summary,
independent-pilot/30 and /144 traces/decisions/summaries/five PNGs each, and OCR.
Per-capture SHA256 hashes are in the respective summaries. Artifact manifest
artifacts/flight-work-products.json enumerates the delivered file set.

## Honest limitations

Xvfb lacks XF86DGA relative mouse mode. Real mouse steering succeeded in mode 0
(absolute pointer with center-warp fallback); true relative capture on a physical
Linux desktop remains unverified. No hardware GPU, physical human keyboard/mouse
playtest, audible output, native Wayland behavior, target-laptop performance or
Windows claim. Pausing freezes and clears inputs, not throttle/velocity: resume
retains them and B stops. Home preserves heading while leveling pitch/roll.
These are flight tests, not combat/playability approval. No working weapons,
enemy AI, health/repair progress, collisions, mission loop or distribution.
No durable mission-node state was changed by this worker session.
