#!/usr/bin/env bash
# Build the pinned official LGPL-only FFmpeg source; never use wheel binaries.
set -euo pipefail
ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PYTHON_BIN="${PYTHON_BIN:-python3}"
export SJTU_FFMPEG_STAGE="${SJTU_FFMPEG_STAGE:-$ROOT_DIR/dist/ffmpeg}"
SOURCE_COMMIT=f46e514491172d15bd74b4abb1814cd2f05a763e
SOURCE_SHA256=1fa39b5a6ae9ac02c2cf280ec5cc8321a0dd0b9ab34b6c73133cafccaf5dfa79
SOURCE_URL="https://codeload.github.com/FFmpeg/FFmpeg/tar.gz/$SOURCE_COMMIT"

# A reused stage is still subjected to every native/source/codec verification.
if [[ "$(uname -s)" == MINGW* || "$(uname -s)" == MSYS* ]]; then
  PYTHON_BIN="$(cygpath -u "$PYTHON_BIN")"
  export SJTU_FFMPEG_STAGE="$(cygpath -u "$SJTU_FFMPEG_STAGE")"
  if [[ -n "${SJTU_FFMPEG_SOURCE_ARCHIVE:-}" ]]; then
    SJTU_FFMPEG_SOURCE_ARCHIVE="$(cygpath -u "$SJTU_FFMPEG_SOURCE_ARCHIVE")"
  fi
fi
if [[ "${SJTU_FFMPEG_SKIP_BUILD:-0}" == 1 ]]; then
  exec "$PYTHON_BIN" "$ROOT_DIR/scripts/verify_ffmpeg.py" --stage "$SJTU_FFMPEG_STAGE"
fi

case "$(uname -s)" in
  Darwin)
    export MACOSX_DEPLOYMENT_TARGET=12.0
    CC=clang
    PLATFORM_FLAGS=(--cc=clang --extra-cflags='-mmacosx-version-min=12.0 -ffile-prefix-map=../source=FFmpeg -ffile-prefix-map=.=build' --extra-ldflags=-mmacosx-version-min=12.0)
    JOBS="${SJTU_FFMPEG_JOBS:-$(sysctl -n hw.ncpu)}"
    EXE=ffmpeg
    ;;
  MINGW*|MSYS*)
    if [[ "${MSYSTEM:-}" != MINGW64 ]]; then
      printf '%s\n' 'FFmpeg requires the MSYS2 MINGW64 shell and native x64 GCC toolchain.' >&2
      exit 1
    fi
    CC=gcc
    PLATFORM_FLAGS=(--cc=gcc --target-os=mingw32 --arch=x86_64 --disable-x86asm --extra-cflags='-ffile-prefix-map=../source=FFmpeg -ffile-prefix-map=.=build' --extra-ldflags='-static -static-libgcc -static-libstdc++')
    JOBS="${SJTU_FFMPEG_JOBS:-$(getconf _NPROCESSORS_ONLN)}"
    EXE=ffmpeg.exe
    ;;
  *) printf '%s\n' 'Supported build hosts: macOS or MSYS2 MINGW64.' >&2; exit 1 ;;
esac
for tool in "$CC" make curl tar strip; do
  command -v "$tool" >/dev/null || { printf 'Missing build tool: %s\n' "$tool" >&2; exit 1; }
done

# Keep scratch outside the checkout so version.sh cannot discover the app's git tag.
WORK="$(mktemp -d "${TMPDIR:-/tmp}/sjtu-ffmpeg-build.XXXXXXXX")"
trap 'rm -rf "$WORK"' EXIT
ARCHIVE="$WORK/FFmpeg-n7.1.3.tar.gz"
if [[ -n "${SJTU_FFMPEG_SOURCE_ARCHIVE:-}" ]]; then
  cp "$SJTU_FFMPEG_SOURCE_ARCHIVE" "$ARCHIVE"
else
  curl --fail --location --retry 3 --proto '=https' --tlsv1.2 "$SOURCE_URL" --output "$ARCHIVE"
fi
"$PYTHON_BIN" - "$ARCHIVE" "$SOURCE_SHA256" <<'PY'
import hashlib
import sys
from pathlib import Path
archive = Path(sys.argv[1])
if archive.stat().st_size != 15926127 or hashlib.sha256(archive.read_bytes()).hexdigest() != sys.argv[2]:
    raise SystemExit('Pinned FFmpeg source archive size/SHA256 mismatch')
PY
mkdir -p "$WORK/source" "$WORK/build"
tar -xzf "$ARCHIVE" --strip-components=1 -C "$WORK/source"
FLAGS=(
  --prefix=/ffmpeg --disable-gpl --disable-nonfree --disable-version3
  --disable-autodetect --disable-shared --enable-static --disable-network
  --disable-debug --disable-doc --disable-ffplay --disable-ffprobe
  --disable-protocols --enable-protocol=file,pipe --pkg-config=false
)
# No --disable-everything: retain all built-in LGPL codecs, demuxers and filters.
(
  cd "$WORK/build"
  ../source/configure "${FLAGS[@]}" "${PLATFORM_FLAGS[@]}"
  make -j "$JOBS" "$EXE"
)
mkdir -p "$SJTU_FFMPEG_STAGE/bin" "$SJTU_FFMPEG_STAGE/licenses" "$SJTU_FFMPEG_STAGE/source"
cp "$WORK/build/$EXE" "$SJTU_FFMPEG_STAGE/bin/$EXE"
strip "$SJTU_FFMPEG_STAGE/bin/$EXE"
chmod +x "$SJTU_FFMPEG_STAGE/bin/$EXE"
cp "$WORK/source/COPYING.LGPLv2.1" "$SJTU_FFMPEG_STAGE/licenses/COPYING.LGPLv2.1"
cp "$ARCHIVE" "$SJTU_FFMPEG_STAGE/source/FFmpeg-n7.1.3.tar.gz"
cp "$ROOT_DIR/scripts/build_ffmpeg.sh" "$SJTU_FFMPEG_STAGE/source/build_ffmpeg.sh"
cp "$ROOT_DIR/scripts/verify_ffmpeg.py" "$SJTU_FFMPEG_STAGE/source/verify_ffmpeg.py"
"$PYTHON_BIN" "$ROOT_DIR/scripts/verify_ffmpeg.py" --stage "$SJTU_FFMPEG_STAGE" --write-manifest
printf 'Built and verified official FFmpeg n7.1.3 in %s\n' "$SJTU_FFMPEG_STAGE"
