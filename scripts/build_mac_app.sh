#!/bin/zsh
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
APP_NAME="Colab Canvas Studio"
VERSION="${1:-1.0.0}"
ARCH="$(uname -m)"
BUNDLE_ID="com.alan0x.colab-canvas-studio"
DIST_DIR="$REPO_ROOT/dist"
APP_BUNDLE="$DIST_DIR/$APP_NAME.app"
CONTENTS="$APP_BUNDLE/Contents"
MACOS_DIR="$CONTENTS/MacOS"
RES_DIR="$CONTENTS/Resources"
ICONSET_DIR="$DIST_DIR/AppIcon.iconset"
DMG_STAGING="$DIST_DIR/dmg_staging"
DMG_NAME="Colab-Canvas-Studio-v${VERSION}-macOS-${ARCH}.dmg"
DMG_PATH="$DIST_DIR/$DMG_NAME"

echo "🔨 Building $APP_NAME v$VERSION ($ARCH) ..."
rm -rf "$APP_BUNDLE" "$ICONSET_DIR" "$DMG_STAGING" "$DMG_PATH" "${DMG_PATH}.sha256"
mkdir -p "$MACOS_DIR" "$RES_DIR" "$ICONSET_DIR"

# 1. Generate high-res vector-rendered macOS App Icon via Swift CoreGraphics
ICON_GEN="$DIST_DIR/gen_icon.swift"
cat > "$ICON_GEN" <<'SWIFT'
import Cocoa

let size: CGFloat = 1024
let rep = NSBitmapImageRep(
    bitmapDataPlanes: nil,
    pixelsWide: Int(size),
    pixelsHigh: Int(size),
    bitsPerSample: 8,
    samplesPerPixel: 4,
    hasAlpha: true,
    isPlanar: false,
    colorSpaceName: .deviceRGB,
    bytesPerRow: 0,
    bitsPerPixel: 0
)!

NSGraphicsContext.saveGraphicsState()
let ctx = NSGraphicsContext(bitmapImageRep: rep)!
NSGraphicsContext.current = ctx
let cg = ctx.cgContext

// Rounded macOS squircle background
let rect = CGRect(x: 64, y: 64, width: 896, height: 896)
let path = CGPath(roundedRect: rect, cornerWidth: 210, cornerHeight: 210, transform: nil)
cg.saveGState()
cg.addPath(path)
cg.clip()

// Deep obsidian + indigo/violet ambient gradient
let cs = CGColorSpaceCreateDeviceRGB()
let bgColors = [
    CGColor(red: 0.05, green: 0.06, blue: 0.11, alpha: 1.0),
    CGColor(red: 0.11, green: 0.12, blue: 0.24, alpha: 1.0),
    CGColor(red: 0.19, green: 0.11, blue: 0.29, alpha: 1.0),
] as CFArray
let grad = CGGradient(colorsSpace: cs, colors: bgColors, locations: [0.0, 0.55, 1.0])!
cg.drawLinearGradient(grad, start: CGPoint(x: 80, y: 940), end: CGPoint(x: 940, y: 80), options: [])

// Subtle infinite canvas dot grid
cg.setFillColor(CGColor(red: 1, green: 1, blue: 1, alpha: 0.14))
for gx in stride(from: 140.0, through: 884.0, by: 62.0) {
    for gy in stride(from: 140.0, through: 884.0, by: 62.0) {
        cg.fillEllipse(in: CGRect(x: gx - 3, y: gy - 3, width: 6, height: 6))
    }
}

// Glowing indigo-purple-emerald orb behind emblem
let orbColors = [
    CGColor(red: 0.45, green: 0.46, blue: 0.98, alpha: 0.55),
    CGColor(red: 0.66, green: 0.33, blue: 0.97, alpha: 0.25),
    CGColor(red: 0.06, green: 0.72, blue: 0.50, alpha: 0.0),
] as CFArray
let orbGrad = CGGradient(colorsSpace: cs, colors: orbColors, locations: [0.0, 0.5, 1.0])!
cg.drawRadialGradient(orbGrad, startCenter: CGPoint(x: 512, y: 512), startRadius: 20, endCenter: CGPoint(x: 512, y: 512), endRadius: 380, options: [])

// Stylized 4-point AI creative star
let star = CGMutablePath()
let cx: CGFloat = 512, cy: CGFloat = 512, rOut: CGFloat = 250, rIn: CGFloat = 68
star.move(to: CGPoint(x: cx, y: cy + rOut))
star.addQuadCurve(to: CGPoint(x: cx + rOut, y: cy), control: CGPoint(x: cx + rIn, y: cy + rIn))
star.addQuadCurve(to: CGPoint(x: cx, y: cy - rOut), control: CGPoint(x: cx + rIn, y: cy - rIn))
star.addQuadCurve(to: CGPoint(x: cx - rOut, y: cy), control: CGPoint(x: cx - rIn, y: cy - rIn))
star.addQuadCurve(to: CGPoint(x: cx, y: cy + rOut), control: CGPoint(x: cx - rIn, y: cy + rIn))
star.closeSubpath()

cg.saveGState()
cg.addPath(star)
cg.clip()
let starColors = [
    CGColor(red: 0.98, green: 0.99, blue: 1.0, alpha: 1.0),
    CGColor(red: 0.65, green: 0.71, blue: 0.99, alpha: 1.0),
    CGColor(red: 0.75, green: 0.52, blue: 0.99, alpha: 1.0),
] as CFArray
let starGrad = CGGradient(colorsSpace: cs, colors: starColors, locations: [0.0, 0.55, 1.0])!
cg.drawLinearGradient(starGrad, start: CGPoint(x: 340, y: 740), end: CGPoint(x: 680, y: 280), options: [])
cg.restoreGState()

// Secondary small emerald accent star
let sm = CGMutablePath()
let sx: CGFloat = 710, sy: CGFloat = 700, sr: CGFloat = 68, si: CGFloat = 18
sm.move(to: CGPoint(x: sx, y: sy + sr))
sm.addQuadCurve(to: CGPoint(x: sx + sr, y: sy), control: CGPoint(x: sx + si, y: sy + si))
sm.addQuadCurve(to: CGPoint(x: sx, y: sy - sr), control: CGPoint(x: sx + si, y: sy - si))
sm.addQuadCurve(to: CGPoint(x: sx - sr, y: sy), control: CGPoint(x: sx - si, y: sy - si))
sm.addQuadCurve(to: CGPoint(x: sx, y: sy + sr), control: CGPoint(x: sx - si, y: sy + si))
sm.closeSubpath()
cg.setFillColor(CGColor(red: 0.20, green: 0.85, blue: 0.65, alpha: 0.95))
cg.addPath(sm)
cg.fillPath()

// Subtle glass rim
cg.restoreGState()
cg.setStrokeColor(CGColor(red: 1, green: 1, blue: 1, alpha: 0.18))
cg.setLineWidth(6)
cg.addPath(path)
cg.strokePath()

NSGraphicsContext.restoreGraphicsState()
let pngData = rep.representation(using: .png, properties: [:])!
try! pngData.write(to: URL(fileURLWithPath: CommandLine.arguments[1]))
SWIFT

BASE_PNG="$DIST_DIR/icon_1024.png"
swift "$ICON_GEN" "$BASE_PNG"
rm -f "$ICON_GEN"

for sz in 16 32 128 256 512; do
  sips -z $sz $sz "$BASE_PNG" --out "$ICONSET_DIR/icon_${sz}x${sz}.png" >/dev/null
  dbl=$((sz * 2))
  sips -z $dbl $dbl "$BASE_PNG" --out "$ICONSET_DIR/icon_${sz}x${sz}@2x.png" >/dev/null
done
iconutil -c icns "$ICONSET_DIR" -o "$RES_DIR/AppIcon.icns"
rm -rf "$ICONSET_DIR" "$BASE_PNG"

# 2. Compile native Swift WKWebView host app
swiftc -O \
  -framework Cocoa \
  -framework WebKit \
  "$REPO_ROOT/macos/StudioApp.swift" \
  -o "$MACOS_DIR/ColabCanvasStudio"

# 3. Bundle local_app backend & frontend inside .app Resources
cp -R "$REPO_ROOT/local_app" "$RES_DIR/local_app"
rm -rf "$RES_DIR/local_app/__pycache__"

# 4. Write Info.plist
cat > "$CONTENTS/Info.plist" <<PLIST
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>CFBundleName</key>
  <string>$APP_NAME</string>
  <key>CFBundleDisplayName</key>
  <string>$APP_NAME</string>
  <key>CFBundleIdentifier</key>
  <string>$BUNDLE_ID</string>
  <key>CFBundleVersion</key>
  <string>$VERSION</string>
  <key>CFBundleShortVersionString</key>
  <string>$VERSION</string>
  <key>CFBundleExecutable</key>
  <string>ColabCanvasStudio</string>
  <key>CFBundleIconFile</key>
  <string>AppIcon</string>
  <key>CFBundlePackageType</key>
  <string>APPL</string>
  <key>LSMinimumSystemVersion</key>
  <string>13.0</string>
  <key>NSHighResolutionCapable</key>
  <true/>
  <key>NSAppTransportSecurity</key>
  <dict>
    <key>NSAllowsLocalNetworking</key>
    <true/>
  </dict>
</dict>
</plist>
PLIST

# 5. Ad-hoc sign so macOS launches cleanly
codesign --force --deep --sign - "$APP_BUNDLE"

# 6. Install to ~/Applications for Spotlight & Dock convenience
mkdir -p "$HOME/Applications"
rm -rf "$HOME/Applications/$APP_NAME.app"
cp -R "$APP_BUNDLE" "$HOME/Applications/$APP_NAME.app"

# 7. Create drag-to-install macOS .dmg installer + SHA256 checksum
mkdir -p "$DMG_STAGING"
cp -R "$APP_BUNDLE" "$DMG_STAGING/$APP_NAME.app"
ln -s /Applications "$DMG_STAGING/Applications"
cat > "$DMG_STAGING/安装说明.txt" <<'NOTE'
Colab Canvas Studio 安装说明 (macOS)
====================================

1. 将「Colab Canvas Studio.app」拖入右侧「Applications」文件夹即可完成安装。
2. 首次运行前请确保系统已安装 Python 3 与 Google 官方 Colab CLI：
     uv tool install google-colab-cli
   （或 pip3 install google-colab-cli）
3. 如果首次双击时 macOS 提示“无法验证开发者”（因为是开源自签名包）：
   在终端运行一次即可放行：
     xattr -cr "/Applications/Colab Canvas Studio.app"
4. 打开 App 后点击右上角「登录 Google 账号」完成一次授权，即可免开 Colab 网页直接生图、生视频并自动同步 Google Drive！
NOTE

hdiutil create \
  -volname "$APP_NAME v$VERSION" \
  -srcfolder "$DMG_STAGING" \
  -ov \
  -format UDZO \
  "$DMG_PATH" >/dev/null
rm -rf "$DMG_STAGING"

(cd "$DIST_DIR" && shasum -a 256 "$DMG_NAME" > "${DMG_NAME}.sha256")

echo "✅ Packaged successfully:"
echo "   - App: $APP_BUNDLE"
echo "   - Installed: $HOME/Applications/$APP_NAME.app"
echo "   - Installer DMG: $DMG_PATH"
echo "   - Checksum: ${DMG_PATH}.sha256"
