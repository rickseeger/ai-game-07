# Breach Flight 1.0.0 — self-contained Linux distribution

Breach Flight is a first-person 3D space-combat sortie built on Panda3D 1.10.16
(Python 3.12). Fly from inside the ship, fight three escalating waves of enemy
fighters and a capital ship, manage accumulated hull/subsystem damage with an
exposed repair loop, and win or lose on a clear objective banner. This directory
is a self-contained, versioned Linux distribution: it bundles its own CPython
runtime, Panda3D, and every asset, so it runs outside any development
environment.

## Requirements

- Linux, x86_64 (glibc). The build targets the same architecture as this host.
- A graphical session for the windowed game (X11 or XWayland).
- Mesa (or a vendor OpenGL driver) and the standard runtime libraries the game
  links: libGL/libGLX, libX11, libXtst, and libopenal1. On Debian/Ubuntu/Pop!_OS:

      sudo apt-get install libgl1 libglx-mesa0 libx11-6 libxtst6 libopenal1

- No Python, no pip, no network, and no virtualenv are needed. Everything ships
  in `runtime/`.

## Install

Unpack the tarball once (no install step required):

    tar -xzf breach-flight-1.0.0-linux-x86_64.tar.gz
    cd breach-flight-1.0.0-linux-x86_64

## Launch

    ./launch.sh                     # start the game
    ./launch.sh --mute              # start without sound
    ./launch.sh --version           # print the distribution version
    ./launch.sh --help              # list every launch option

The first launch may take a few seconds while Panda3D initializes the renderer
and loads assets. Quit with F10 or by closing the window.

## Controls

    W/S            raise / lower throttle | B: brake and zero throttle
    A/D            roll left / right | Arrows: nose up/down/left/right
    Mouse          aim stick (up = nose up) | C: center aim
    Home (hold)    level the horizon | Esc: pause/resume | F10: quit
    R (hold)       repair the selected subsystem (brakes + locks)
    1/2/3          select engine / weapons / sensors subsystem
    Space / LMB    fire the selected weapon | Q: cycle weapon (cannon -> scatter -> torpedo)
    Tab            cycle target lock (reticle lights green on a fire solution)
    N              restart the mission after victory or defeat

Holding W raises the throttle; releasing it retains the setting. S lowers it
(no reverse thrust). B deliberately stops and zeros throttle even if W is held.
Mouse is a bounded virtual aim stick, not free look; move it back or press C to
re-center. The camera always points with the ship.

Space/LMB fires the selected weapon; Q cycles cannon -> scatter -> torpedo. Heat
builds with sustained fire and cools between shots. Damage to the weapons
subsystem lowers damage per shot, and a destroyed weapons subsystem stops
firing. Hold R below 2 m/s to repair the selected subsystem toward 70% over
about 4 s; a hit interrupts the current repair interval without undoing progress
already restored. Escape pauses and resumes (releasing/capturing the pointer).
After victory or defeat press N to restart from wave 1 at full health.

## Reproducible rebuild

The tarball is produced by the committed build script (requires `uv` and network
only at build time):

    tools/package.sh

which yields `dist/breach-flight-1.0.0-linux-x86_64.tar.gz` and its
`.sha256`. The archive is normalized (fixed mtime, numeric owner, sorted
entries, no gzip timestamp) so the same inputs give the same bytes.

## Automated validation

The full 165-test unit suite and the deterministic runtime validators (graphics
pixel sampling, scripted input/controls, scripted combat, a non-silent OpenAL
audio capture, restart, and quit) run against the extracted artifact with:

    tools/validate_distribution.sh dist/breach-flight-1.0.0-linux-x86_64.tar.gz /tmp/cleanroom

See the script for exactly what is checked; evidence is written into the output
directory.

## Licensing

- Project code, meshes and audio: MIT (see LICENSE).
- CPython 3.12: PSF-2.0 (see docs/CPYTHON-LICENSE.txt).
- Panda3D 1.10.16 and its bundled libraries/font: Modified BSD (see
  docs/PANDA3D-LICENSE.txt).
- Full inventory: NOTICE.

The procedural meshes and sound effects are original, self-generated content;
no third-party art or recordings are included.
