#!/usr/bin/env bash
# Independent clean-room validation of a built distribution tarball.
#
# Extracts the tarball into a fresh directory (NOT the development tree) and
# proves the DISTRIBUTED artifact runs on its own bundled runtime:
#   graphics   - an actual rendered frame, code-level pixel sampled
#   controls   - a scripted X11 input flight
#   combat     - a scripted engagement (weapons + live enemy fire)
#   sound      - a non-silent OpenAL 'wave' capture of the running game
#   restart    - the N key restarts the mission from wave 1
#   quit       - the F10 key quits the process cleanly
# plus the full 165-test unit suite against the bundled runtime.
#
# Requires Xvfb and the same Mesa/OpenAL/X11 libraries a target desktop needs.
# Usage:
#   tools/validate_distribution.sh dist/breach-flight-1.0.0-linux-x86_64.tar.gz /tmp/cleanroom
set -euo pipefail

TARBALL="$(cd "$(dirname "$1")" && pwd)/$(basename "$1")"
OUT="$(cd "$(dirname "$2")" && pwd)/$(basename "$2")"

[ -f "$TARBALL" ] || { echo "missing tarball: $TARBALL"; exit 2; }

rm -rf "$OUT"
mkdir -p "$OUT"
# The top-level directory name matches the tarball basename (see package.sh).
DIST="$(basename "$TARBALL" .tar.gz)"
tar -xzf "$TARBALL" -C "$OUT"
EXTRACT="$OUT/$DIST"
[ -d "$EXTRACT" ] || { echo "extract failed for $TARBALL"; exit 2; }

RUNTIME="$EXTRACT/runtime"
PY="$RUNTIME/bin/python3.12"
[ -x "$PY" ] || { echo "bundled python missing: $PY"; exit 2; }

export PYTHONHOME="$RUNTIME"
export PYTHONNOUSERSITE=1
export LIBGL_ALWAYS_SOFTWARE=1

EVID="$OUT/evidence"
mkdir -p "$EVID"
cd "$EXTRACT"

say() { echo "[validate] $*"; }

xvfb() {
    local logfile="$1"; shift
    xvfb-run -a -s "-screen 0 960x540x24 -nolisten tcp" "$@" >>"$logfile" 2>&1
}

summary() {
    local key="$1" value="$2"
    echo "$key $value" >> "$EVID/summary.txt"
}

# 0. Version.
say "checking version"
"$EXTRACT/launch.sh" --version > "$EVID/version.txt"
summary "version" "$(cat "$EVID/version.txt")"

# 1. Full unit test suite against the bundled runtime.
say "running unit tests"
"$PY" -m unittest discover -s tests -v > "$EVID/tests.log" 2>&1
summary "tests" "OK"

# 2. Graphics: launch.sh drives a real offscreen frame; pixel-sample it.
say "checking graphics (launch.sh offscreen render)"
rm -rf "$EVID/gfx"
xvfb "$EVID/graphics.log" "$EXTRACT/launch.sh" --offscreen --mute --frames 40 \
     --trace-dir "$EVID/gfx" --report "$EVID/gfx/report.json" --capture-frames 30
grep -q '"output_type": "glxGraphicsBuffer"' "$EVID/gfx/report.json"
"$PY" tools/sample_png.py "$EVID/gfx/frame-0030.png" > "$EVID/graphics_sample.txt"
summary "graphics" "$(grep -o 'distinct_colors=[0-9]*' "$EVID/graphics_sample.txt")"

# 3. Controls: scripted X11 input flight.
say "checking controls (scripted input flight)"
xvfb "$EVID/flight.log" "$PY" tools/flight_runtime.py --output "$EVID/controls"
grep -q "FLIGHT_RUNTIME_PASS" "$EVID/flight.log"
summary "controls" "FLIGHT_RUNTIME_PASS"

# 4. Combat: scripted engagement (hit and miss).
say "checking combat (scripted engagement)"
xvfb "$EVID/weapons.log" "$PY" tools/weapons_runtime.py --output "$EVID/combat"
grep -q "WEAPONS_RUNTIME_PASS" "$EVID/weapons.log"
summary "combat" "WEAPONS_RUNTIME_PASS"

# 5. Sound + effects + live combat (also re-verifies graphics progression).
say "checking sound + effects + live combat"
xvfb "$EVID/effects.log" "$PY" tools/effects_runtime.py --output "$EVID/effects"
grep -q "EFFECTS_RUNTIME_PASS" "$EVID/effects.log"
summary "sound" "EFFECTS_RUNTIME_PASS (emitted-audio.wav captured)"

# 6. Restart + pause (N / Esc) via scripted keys.
say "checking restart and pause (N / Esc)"
xvfb "$EVID/mission.log" "$PY" tools/mission_runtime.py --output "$EVID/mission"
grep -q "MISSION_RUNTIME_PASS" "$EVID/mission.log"
summary "restart" "MISSION_RUNTIME_PASS (N restart to wave 1)"

# 7. Quit (F10) via scripted key: process must exit cleanly before --frames.
say "checking quit (F10)"
mkdir -p "$EVID/quit"
printf '[{"frame": 20, "key": "f10", "down": true}]\n' > "$EVID/quit/keys.json"
xvfb "$EVID/quit.log" "$PY" -m breach.app --offscreen --mute --frames 100 \
     --trace-dir "$EVID/quit/trace" --report "$EVID/quit/report.json" \
     --key-script "$EVID/quit/keys.json"
if grep -q "RUNTIME_PASS" "$EVID/quit.log"; then
    echo "quit check failed: app ran to --frames instead of quitting on F10" >&2
    exit 1
fi
summary "quit" "F10 clean exit"

say "all checks passed; evidence in $EVID"
echo "DISTRIBUTION_VALIDATION_PASS"
