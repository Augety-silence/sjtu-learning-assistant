#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"

if [[ "$(uname -s)" != "Darwin" ]]; then
  printf '%s\n' "错误：macOS .app 只能在 macOS 构建。" >&2
  exit 1
fi

PYTHON_BIN="${PYTHON_BIN:-$ROOT_DIR/.venv/bin/python}"
if [[ ! -x "$PYTHON_BIN" ]]; then
  python3 -m venv "$ROOT_DIR/.venv"
  PYTHON_BIN="$ROOT_DIR/.venv/bin/python"
fi

APP_PATH="$ROOT_DIR/dist/SJTU Learning Assistant.app"

clean_packaging_xattrs() {
  local path
  for path in \
    "$ROOT_DIR/packaging" \
    "$ROOT_DIR/dashboard-web/dist"; do
    if [[ -e "$path" ]]; then
      /usr/bin/xattr -cr "$path"
    fi
  done
  for path in "$ROOT_DIR/LICENSE" "$ROOT_DIR/THIRD_PARTY_NOTICES.md"; do
    if [[ -e "$path" ]]; then
      /usr/bin/xattr -c "$path"
    fi
  done
}

"$PYTHON_BIN" -m pip install --upgrade pip
"$PYTHON_BIN" -m pip install -r requirements-dev.txt

npm --prefix dashboard-web ci
npm --prefix dashboard-web run test
npm --prefix dashboard-web run lint
npm --prefix dashboard-web run build
"$PYTHON_BIN" -m unittest discover -s tests -v
"$PYTHON_BIN" scripts/check_licenses.py
node scripts/check_licenses.mjs
"$PYTHON_BIN" scripts/scan_secrets.py
"$PYTHON_BIN" scripts/generate_macos_icon.py

clean_bundle_xattrs() {
  local attribute
  for attribute in \
    'com.apple.fileprovider.fpfs#P' \
    com.apple.ResourceFork \
    com.apple.FinderInfo; do
    /usr/bin/xattr -dr "$attribute" "$APP_PATH" 2>/dev/null || true
    /usr/bin/xattr -drs "$attribute" "$APP_PATH" 2>/dev/null || true
  done
}

adhoc_sign_bundle() {
  local attempt
  /usr/bin/xattr -cr "$APP_PATH"
  /bin/sleep 1
  for attempt in 1 2 3 4 5; do
    clean_bundle_xattrs
    if /usr/bin/codesign --force --deep --sign - "$APP_PATH"; then
      return 0
    fi
    /bin/sleep 1
  done
  printf '%s\n' "错误：清理 bundle xattr 后仍无法完成 ad-hoc 签名。" >&2
  return 1
}

clean_packaging_xattrs
rm -rf build dist
PYINSTALLER_STRICT_BUNDLE_CODESIGN_ERROR=1 \
  "$PYTHON_BIN" -m PyInstaller --noconfirm --clean packaging/desktop.spec

# The Desktop path may be managed by File Provider, which can immediately
# recreate FinderInfo on bundles. Prefer ad-hoc signing when the attributes
# remain clear; otherwise keep a valid unsigned bundle instead of a broken one.
/usr/bin/xattr -cr "$APP_PATH"
/bin/sleep 5
if /usr/bin/xattr -lr "$APP_PATH" 2>/dev/null | /usr/bin/grep -E \
  'com\.apple\.(FinderInfo|ResourceFork)' >/dev/null; then
  /usr/bin/codesign --remove-signature "$APP_PATH" 2>/dev/null || true
  rm -rf "$APP_PATH/Contents/_CodeSignature"
  printf '%s\n' "提示：目标目录会恢复 FinderInfo；已保留未签名应用。"
else
  set +e
  adhoc_sign_bundle
  sign_status=$?
  set -e
  if test "$sign_status" -eq 0; then
    /usr/bin/codesign --verify --deep --strict --verbose=2 "$APP_PATH"
  else
    /usr/bin/codesign --remove-signature "$APP_PATH" 2>/dev/null || true
    rm -rf "$APP_PATH/Contents/_CodeSignature"
    printf '%s\n' "提示：ad-hoc 签名未完成；已保留未签名应用。"
  fi
fi

"$PYTHON_BIN" scripts/verify_macos_bundle.py "$APP_PATH"
/usr/bin/plutil -lint "$APP_PATH/Contents/Info.plist"

printf '%s\n' "已生成并验证应用：dist/SJTU Learning Assistant.app"
