# Measured runtime evidence and limitations

Worktree: /opt/g-harness/workspace/G14/node_1_step_118/repo
Remote: git@github.com:rickseeger/ai-game-07.git (initially empty).

## Actual environment

Ubuntu 26.04.1 LTS x86_64; system Python 3.14.4, project Python 3.12.14 via uv.
Original session had no DISPLAY, WAYLAND_DISPLAY or XDG_RUNTIME_DIR. No /dev/dri
was exposed. libGL/X11/OpenAL/ALSA/Pulse libraries and Xvfb/FFmpeg were available;
libEGL was not found by the library probe. FFmpeg reported version 8.0.1.
Inventory in each run records the test DISPLAY rather than the original unset
value. The headless wrapper creates a private X server, health-checks it via
XOpenDisplay and cleans it up. No physical display/GPU is claimed.

/dev/input contained virtual keyboard/VMware mouse device nodes; the application
does not read them directly. The smoke test verifies one XTest Space event through
X11 and the actual Panda event loop, moving the camera. It is not a human keyboard,
mouse, relative-pointer, focus-loss, or gamepad validation.

/dev/snd had only seq/timer, no PCM playback device; /proc/asound/cards was absent.
The actual unmuted initialization in artifacts/audio-probe/runtime.log failed to
open Pulse/ALSA default playback and Panda fell back to NullAudioManager. Audible
playback cannot be validated on this host as configured; a real audio endpoint
and a listening human are needed later. No tone/music was played or fabricated.

## Independent exercise

From repository root:

    uv python install 3.12
    uv sync --project . --frozen
    uv run --project . --frozen python -m unittest discover -s tests -v
    python3 tools/headless.py --venv .venv-verify --output artifacts/local

For this session the final command used --output artifacts/independent and an
absent .venv-verify, creating a fresh environment from the lock; downloads could
use the uv cache. This is independent setup/launch, not an offline-install claim.
artifacts/initial is the earlier successful setup/test/window/offscreen run.
Both runs: six tests passed, both render modes ran 180 frames, process exit 0.
Actual renderer: llvmpipe (LLVM 21.1.8, 256 bits), Mesa OpenGL 4.5 Compatibility
Profile, Mesa 26.0.8-1ubuntu0.3. Outputs were 960x540 glxGraphicsWindow and
separately glxGraphicsBuffer, not a mocked renderer. The harmless Xvfb warning
about window decorations/fixed size is retained in the window log.

Capture uses GraphicsOutput.saveScreenshot only after graphicsEngine.renderFrame.
PNMImage then reads that PNG solely for validation; no code draws or composites
a replacement capture. The central viewport check excludes the overlay text,
requires real color variation and foreground pixels. The two views have distinct
hashes after camera input; the reruns reproduced both hashes exactly on this host.
These tests verify real rendering, not artistic quality or gameplay readiness.

window:
  PNG: artifacts/independent/window.png
  SHA256: ffebf9decd3ffcd132b4a1812435a4bf123b8d3bbee9c022568f968bacf0ce15
  Central sampled colors: 13
  Foreground fraction: 0.11985571405148385
  Input events: 1

offscreen:
  PNG: artifacts/independent/offscreen.png
  SHA256: 44f4fb4ebf30c77faaa5edbd1a31485e7abe726c2380bbd84768e1d8535c4a06
  Central sampled colors: 14
  Foreground fraction: 0.12096245286112478
  Input events: 0

Logs: artifacts/independent/setup.log, tests.log, runtime.log, window.log,
offscreen.log, xvfb.log. Renderer reports: window.json/offscreen.json;
runtime-summary.json combines machine-checkable frame/hash/input evidence.
artifacts/window.png and window.json are the preliminary 90-frame window launch,
before adding native window-ID diagnostics; use independent/ for final evidence.

## Runtime evidence strategy for sibling nodes

Keep this actual-renderer smoke path. Add deterministic simulation replays with
seed, commands, tick, damage/repair outcomes and telemetry; capture from the live
framebuffer at named combat milestones. For motion proof, record the real X11
window with FFmpeg x11grab or write a real renderer frame sequence; do not use a
slideshow, mocked image or diagram as runtime evidence. FFmpeg availability was
inspected, but video recording has not been exercised in this node. New gameplay
must add real aim/hit/impairment/repair-under-fire/capital/restart checks and target
Linux desktop human playtests. Validate sound separately on a real output device.
Do not infer 60 fps gameplay performance from the configured 60 Hz frame limiter.

## Execution incident (recovered, not G14 evidence)

An initial source-writing Python command failed with a quoting SyntaxError.
Because subsequent shell commands were not fail-fast, uv searched upward before
the project existed and synchronized the parent harness environment, removing
pygame-ce 2.5.8; the unrelated harness test suite then ran. That output is NOT
G14 smoke evidence. pygame-ce 2.5.8 was immediately restored in that environment.
All G14 work thereafter used the repository project, explicit --project or its
own virtualenv Python; tools/verify.py resolves an absolute project root. No
mission control commands or durable node-state changes were made. This incident
is disclosed rather than treating the unrelated successful tests as validation.

## Remaining limitations

Only the Linux 3D foundation is delivered. Gameplay, simulation loop integration,
repair/damage enforcement, weapons/enemies/capital behavior, cockpit HUD/radar,
effects/audio assets, accessibility, mission tuning and packaging are not yet
implemented. Hardware acceleration, physical desktop input, audible sound,
Wayland-native behavior, target-laptop performance and Windows remain unverified.
There is no foundation-blocking graphics issue: real software GLX works. Audio
and physical-device validation need a suitable desktop but do not block the
scoped source/rendering/integration foundation.
