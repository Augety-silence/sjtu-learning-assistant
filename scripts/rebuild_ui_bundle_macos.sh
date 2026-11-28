#!/usr/bin/env bash
# 在已完成完整门禁的隔离 bundle 上纳入最后的纯前端修正，重新验包。
set -euo pipefail
ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"
PYTHON_BIN="${PYTHON_BIN:-$ROOT_DIR/.venv/bin/python}"
BUNDLE_PATH="$ROOT_DIR/dist/SJTU Learning Assistant.app"
if [[ "$(uname -s)" != Darwin ]]; then exit 1; fi
"$PYTHON_BIN" scripts/verify_macos_bundle.py "$BUNDLE_PATH"
npm --prefix dashboard-web run lint
npm --prefix dashboard-web run build
rm -rf "$BUNDLE_PATH/Contents/Resources/dashboard-web/dist"
/usr/bin/ditto dashboard-web/dist "$BUNDLE_PATH/Contents/Resources/dashboard-web/dist"
/usr/bin/xattr -cr "$BUNDLE_PATH"
/usr/bin/codesign --force --deep --sign - "$BUNDLE_PATH"
/usr/bin/codesign --verify --deep --strict "$BUNDLE_PATH"
"$PYTHON_BIN" scripts/verify_macos_bundle.py "$BUNDLE_PATH"
"$PYTHON_BIN" scripts/verify_ffmpeg.py --bundle "$BUNDLE_PATH"
