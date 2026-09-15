#!/usr/bin/env bash
# Reproducible build of the self-contained Linux distribution for Breach Flight.
#
# Produces  dist/breach-flight-<version>-linux-<arch>.tar.gz  plus its sha256,
# a tarball that starts OUTSIDE this repository without the developer
# environment: it bundles a pinned CPython 3.12 interpreter, Panda3D 1.10.16,
# and this package (built from the committed pyproject.toml, embedding the
# version and the procedural audio assets).
#
# Run from anywhere (the script resolves the repository root):
#     tools/package.sh
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
VERSION="1.0.0"               # keep in sync with pyproject.toml + src/breach/__init__.py
ARCH="$(uname -m)"            # x86_64 on the supported target
DIST_NAME="breach-flight-${VERSION}-linux-${ARCH}"
DIST_DIR="$ROOT/dist"
STAGE="$DIST_DIR/stage/$DIST_NAME"

cd "$ROOT"

echo "==> Building $DIST_NAME.tar.gz"

# 1. Pinned CPython 3.12 runtime (uv-managed standalone build).
uv python install 3.12 >/dev/null
UV_PY_BIN="$(uv python find 3.12)"
UV_PY="$(readlink -f "$(dirname "$(dirname "$UV_PY_BIN")")")"
echo "==> Runtime interpreter: $UV_PY"

# 2. Fresh staging area.
rm -rf "$DIST_DIR/stage"
mkdir -p "$STAGE"

# 3. Bundle the full interpreter + stdlib, then install dependencies into it:
#    Panda3D (pinned) and this package (built from the committed source).
cp -r "$UV_PY" "$STAGE/runtime"
uv pip install --python "$STAGE/runtime/bin/python3.12" --break-system-packages --no-cache \
    "panda3d==1.10.16" "$ROOT"

# 4. Drop the package manager and bytecode caches for a lean, reproducible artifact.
rm -rf "$STAGE/runtime/lib/python3.12/site-packages/pip" \
       "$STAGE/runtime/lib/python3.12/site-packages/pip"-*.dist-info 2>/dev/null || true
rm -f "$STAGE/runtime/bin/pip" "$STAGE/runtime/bin/pip3" "$STAGE/runtime/bin/pip3.12" 2>/dev/null || true

# 5. Distribution payload: source, tests, docs, data, launch entry point, and
#    the license/notice inventory.
cp launch.sh "$STAGE/"
chmod +x "$STAGE/launch.sh"
cp -r src data tests tools docs "$STAGE/"
cp LICENSE NOTICE pyproject.toml uv.lock .python-version "$STAGE/"
cp docs/DISTRIBUTION.md "$STAGE/README.md"
printf '%s\n' "$VERSION" > "$STAGE/VERSION"

# 6. Remove any bytecode caches introduced while installing.
find "$STAGE" -name '__pycache__' -type d -prune -exec rm -rf {} + 2>/dev/null || true
find "$STAGE" -name '*.pyc' -delete 2>/dev/null || true

# 7. Reproducible archive: fixed mtime, numeric owner, sorted entries, and a
#    gzip header with no embedded name/timestamp.
mkdir -p "$DIST_DIR"
TARBALL="$DIST_DIR/$DIST_NAME.tar.gz"
tar --sort=name --owner=0 --group=0 --numeric-owner \
    --mtime='2026-01-01 00:00:00 UTC' --format=posix \
    -C "$DIST_DIR/stage" -cf - "$DIST_NAME" | gzip -n -9 > "$TARBALL"

# 8. Checksum.
sha256sum "$TARBALL" > "$TARBALL.sha256"

echo "==> Built $TARBALL"
echo "==> SHA256: $(cut -d' ' -f1 "$TARBALL.sha256")"
echo "==> Size:   $(du -h "$TARBALL" | cut -f1)"
