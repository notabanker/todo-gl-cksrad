#!/usr/bin/env bash
# Build an Apple Silicon TYCHE.app with a native WKWebView shell and bundled backend.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")" && pwd)"
BUILD_ROOT="$ROOT/build/macos"
BACKEND_DIST="$BUILD_ROOT/backend-dist"
BACKEND_WORK="$BUILD_ROOT/backend-work"
BUILD_VENV="$ROOT/.build-venv"
APP="$ROOT/dist/TYCHE.app"
ZIP="$ROOT/dist/TYCHE-macOS-arm64.zip"

command -v uv >/dev/null || { echo "uv is required to build TYCHE.app" >&2; exit 1; }
command -v xcrun >/dev/null || { echo "Apple Command Line Tools are required" >&2; exit 1; }

mkdir -p "$BUILD_ROOT" "$ROOT/dist"

if [ ! -x "$BUILD_VENV/bin/python" ]; then
  echo "Creating the isolated desktop build environment..."
  uv venv --python 3.11 "$BUILD_VENV"
fi

echo "Installing desktop build dependencies..."
uv pip install --python "$BUILD_VENV/bin/python" \
  -r "$ROOT/requirements-desktop.txt" \
  -r "$ROOT/requirements-build.txt"

rm -rf "$BACKEND_DIST" "$BACKEND_WORK" "$BUILD_ROOT/spec" "$APP" "$ZIP"
mkdir -p "$BACKEND_DIST" "$BACKEND_WORK" "$BUILD_ROOT/spec"

echo "Bundling the FastAPI backend..."
"$BUILD_VENV/bin/python" -m PyInstaller \
  --noconfirm \
  --clean \
  --onedir \
  --name tyche-backend \
  --target-arch arm64 \
  --add-data "$ROOT/templates:templates" \
  --collect-submodules uvicorn \
  --hidden-import sqlalchemy.dialects.sqlite \
  --distpath "$BACKEND_DIST" \
  --workpath "$BACKEND_WORK" \
  --specpath "$BUILD_ROOT/spec" \
  "$ROOT/desktop_backend.py"

echo "Compiling the native macOS window..."
xcrun swiftc \
  -O \
  -parse-as-library \
  -swift-version 6 \
  -target arm64-apple-macos13.0 \
  -framework AppKit \
  -framework WebKit \
  "$ROOT/macos/TycheApp.swift" \
  -o "$BUILD_ROOT/TYCHE"

echo "Generating the application icon..."
xcrun swiftc "$ROOT/macos/GenerateIcon.swift" -framework AppKit -o "$BUILD_ROOT/GenerateIcon"
"$BUILD_ROOT/GenerateIcon" "$BUILD_ROOT/AppIcon-1024.png"

ICONSET="$BUILD_ROOT/AppIcon.iconset"
mkdir -p "$ICONSET"
sips -z 16 16 "$BUILD_ROOT/AppIcon-1024.png" --out "$ICONSET/icon_16x16.png" >/dev/null
sips -z 32 32 "$BUILD_ROOT/AppIcon-1024.png" --out "$ICONSET/icon_16x16@2x.png" >/dev/null
sips -z 32 32 "$BUILD_ROOT/AppIcon-1024.png" --out "$ICONSET/icon_32x32.png" >/dev/null
sips -z 64 64 "$BUILD_ROOT/AppIcon-1024.png" --out "$ICONSET/icon_32x32@2x.png" >/dev/null
sips -z 128 128 "$BUILD_ROOT/AppIcon-1024.png" --out "$ICONSET/icon_128x128.png" >/dev/null
sips -z 256 256 "$BUILD_ROOT/AppIcon-1024.png" --out "$ICONSET/icon_128x128@2x.png" >/dev/null
sips -z 256 256 "$BUILD_ROOT/AppIcon-1024.png" --out "$ICONSET/icon_256x256.png" >/dev/null
sips -z 512 512 "$BUILD_ROOT/AppIcon-1024.png" --out "$ICONSET/icon_256x256@2x.png" >/dev/null
sips -z 512 512 "$BUILD_ROOT/AppIcon-1024.png" --out "$ICONSET/icon_512x512.png" >/dev/null
cp "$BUILD_ROOT/AppIcon-1024.png" "$ICONSET/icon_512x512@2x.png"
iconutil -c icns "$ICONSET" -o "$BUILD_ROOT/AppIcon.icns"

echo "Assembling TYCHE.app..."
mkdir -p "$APP/Contents/MacOS" "$APP/Contents/Resources"
cp "$BUILD_ROOT/TYCHE" "$APP/Contents/MacOS/TYCHE"
ditto "$BACKEND_DIST/tyche-backend" "$APP/Contents/Resources/Backend"
cp "$BUILD_ROOT/AppIcon.icns" "$APP/Contents/Resources/AppIcon.icns"
cp "$ROOT/macos/Info.plist" "$APP/Contents/Info.plist"
if [ -f "$ROOT/tyche.db" ]; then
  cp "$ROOT/tyche.db" "$APP/Contents/Resources/starter.sqlite3"
fi
chmod 755 "$APP/Contents/MacOS/TYCHE" "$APP/Contents/Resources/Backend/tyche-backend"

plutil -lint "$APP/Contents/Info.plist" >/dev/null

echo "Applying a local ad-hoc signature..."
codesign --force --deep --sign - "$APP"
codesign --verify --deep --strict --verbose=2 "$APP"

ditto -c -k --sequesterRsrc --keepParent "$APP" "$ZIP"

echo
echo "Built: $APP"
echo "Zip:   $ZIP"
echo "Data:  ~/Library/Application Support/TYCHE/tyche.sqlite3"
