#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"

if [[ "$(uname -s)" != "Darwin" ]]; then
  printf '%s\n' "错误：DMG 只能在 macOS 构建。" >&2
  exit 1
fi

PYTHON_BIN="${PYTHON_BIN:-$ROOT_DIR/.venv/bin/python}"
if [[ ! -x "$PYTHON_BIN" ]]; then
  printf '%s\n' "错误：未找到项目 Python 环境，请先运行 scripts/build_macos_app.sh。" >&2
  exit 1
fi

APP_PATH="$ROOT_DIR/dist/SJTU Learning Assistant.app"
if [[ ! -d "$APP_PATH" ]]; then
  printf '%s\n' "错误：未找到应用包，请先运行 scripts/build_macos_app.sh。" >&2
  exit 1
fi

VERSION="$($PYTHON_BIN -c 'from sjtu_learning_assistant import __version__; print(__version__)')"
ARCH="$(uname -m)"
DMG_PATH="$ROOT_DIR/dist/SJTU-Learning-Assistant-${VERSION}-macOS-${ARCH}.dmg"
STAGE_DIR="$(mktemp -d "${TMPDIR:-/tmp}/sjtu-learning-assistant-dmg.XXXXXX")"

cleanup() {
  rm -rf "$STAGE_DIR"
}
trap cleanup EXIT

/usr/bin/ditto "$APP_PATH" "$STAGE_DIR/SJTU Learning Assistant.app"
/bin/ln -s /Applications "$STAGE_DIR/Applications"
rm -f "$DMG_PATH"
/usr/bin/hdiutil create \
  -volname "SJTU Learning Assistant ${VERSION}" \
  -srcfolder "$STAGE_DIR" \
  -ov \
  -format UDZO \
  "$DMG_PATH"

/usr/bin/shasum -a 256 "$DMG_PATH" > "$DMG_PATH.sha256"
printf '%s\n' "已生成 DMG：$DMG_PATH"
printf '%s\n' "SHA-256：$(/usr/bin/shasum -a 256 "$DMG_PATH" | /usr/bin/cut -d ' ' -f 1)"
