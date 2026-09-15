#!/usr/bin/env bash
# Launch the bundled Breach Flight distribution (self-contained).
#
#   ./launch.sh                  run the game on your X11/Wayland desktop
#   ./launch.sh --mute           run without sound
#   ./launch.sh --version        print the distribution version
#   ./launch.sh --help           list all gameplay/launch options
#
# The bundled runtime (runtime/) is self-contained: no system Python, no
# virtualenv, and no network are required. This wrapper forces the interpreter
# to use the bundled standard library so the artifact behaves identically
# wherever it is unpacked.
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
RUNTIME="$HERE/runtime"

if [ "${1:-}" = "--version" ] || [ "${1:-}" = "version" ]; then
    cat "$HERE/VERSION"
    exit 0
fi

export PYTHONHOME="$RUNTIME"
export PYTHONNOUSERSITE=1

exec "$RUNTIME/bin/python3.12" -m breach.app "$@"
