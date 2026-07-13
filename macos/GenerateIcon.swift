import AppKit

private let size = 1024

private func color(_ hex: Int, alpha: CGFloat = 1) -> NSColor {
    NSColor(
        calibratedRed: CGFloat((hex >> 16) & 0xff) / 255,
        green: CGFloat((hex >> 8) & 0xff) / 255,
        blue: CGFloat(hex & 0xff) / 255,
        alpha: alpha
    )
}

guard CommandLine.arguments.count == 2 else {
    fputs("Usage: GenerateIcon output.png\n", stderr)
    exit(2)
}

guard let bitmap = NSBitmapImageRep(
    bitmapDataPlanes: nil,
    pixelsWide: size,
    pixelsHigh: size,
    bitsPerSample: 8,
    samplesPerPixel: 4,
    hasAlpha: true,
    isPlanar: false,
    colorSpaceName: .deviceRGB,
    bytesPerRow: 0,
    bitsPerPixel: 0
), let context = NSGraphicsContext(bitmapImageRep: bitmap) else {
    fatalError("Could not create icon canvas")
}

NSGraphicsContext.saveGraphicsState()
NSGraphicsContext.current = context
context.shouldAntialias = true

let canvas = NSRect(x: 0, y: 0, width: size, height: size)
NSColor.clear.setFill()
canvas.fill()

let backgroundRect = NSRect(x: 42, y: 42, width: 940, height: 940)
let background = NSBezierPath(roundedRect: backgroundRect, xRadius: 214, yRadius: 214)
let backgroundGradient = NSGradient(colors: [color(0xfffdf7), color(0xeadfc9)])!
backgroundGradient.draw(in: background, angle: -55)
background.addClip()

let center = NSPoint(x: 512, y: 502)
let outerRadius: CGFloat = 382
let glow = NSShadow()
glow.shadowColor = color(0xffd400, alpha: 0.54)
glow.shadowBlurRadius = 38
glow.shadowOffset = .zero
glow.set()
color(0xffd400).setFill()
NSBezierPath(ovalIn: NSRect(x: center.x - outerRadius, y: center.y - outerRadius, width: outerRadius * 2, height: outerRadius * 2)).fill()

NSGraphicsContext.restoreGraphicsState()
NSGraphicsContext.saveGraphicsState()
NSGraphicsContext.current = context
background.addClip()

let rimOuter = NSBezierPath(ovalIn: NSRect(x: center.x - outerRadius, y: center.y - outerRadius, width: outerRadius * 2, height: outerRadius * 2))
color(0x17140f).setFill()
rimOuter.fill()

let rimChromeRadius: CGFloat = 354
let rimChrome = NSBezierPath(ovalIn: NSRect(x: center.x - rimChromeRadius, y: center.y - rimChromeRadius, width: rimChromeRadius * 2, height: rimChromeRadius * 2))
color(0xffffff).setFill()
rimChrome.fill()

let wheelRadius: CGFloat = 337
let palette = [0xe60012, 0x0057d9, 0xffd400, 0x00843d, 0xf05a00, 0x7b2cbf, 0x00b8d9, 0xd5007f]
for index in 0..<palette.count {
    let start = 90.0 - Double(index + 1) * 45.0
    let end = 90.0 - Double(index) * 45.0
    let segment = NSBezierPath()
    segment.move(to: center)
    segment.appendArc(withCenter: center, radius: wheelRadius, startAngle: start, endAngle: end, clockwise: false)
    segment.close()
    color(palette[index]).setFill()
    segment.fill()
    color(0xffffff, alpha: 0.72).setStroke()
    segment.lineWidth = 3
    segment.stroke()
}

let shine = NSGradient(colors: [color(0xffffff, alpha: 0.25), color(0xffffff, alpha: 0)])!
let shinePath = NSBezierPath(ovalIn: NSRect(x: 220, y: 520, width: 584, height: 230))
shineGradient: do {
    shine.draw(in: shinePath, angle: -90)
}

let hubOuterRadius: CGFloat = 128
color(0x8e5d00).setFill()
NSBezierPath(ovalIn: NSRect(x: center.x - hubOuterRadius, y: center.y - hubOuterRadius, width: hubOuterRadius * 2, height: hubOuterRadius * 2)).fill()
let hubWhiteRadius: CGFloat = 113
color(0xffffff).setFill()
NSBezierPath(ovalIn: NSRect(x: center.x - hubWhiteRadius, y: center.y - hubWhiteRadius, width: hubWhiteRadius * 2, height: hubWhiteRadius * 2)).fill()
let hubRadius: CGFloat = 99
let hub = NSBezierPath(ovalIn: NSRect(x: center.x - hubRadius, y: center.y - hubRadius, width: hubRadius * 2, height: hubRadius * 2))
NSGradient(colors: [color(0xff4a55), color(0xe60012), color(0x88000b)])!.draw(in: hub, angle: -55)

let play = NSBezierPath()
play.move(to: NSPoint(x: center.x - 24, y: center.y - 38))
play.line(to: NSPoint(x: center.x + 43, y: center.y))
play.line(to: NSPoint(x: center.x - 24, y: center.y + 38))
play.close()
color(0xffffff).setFill()
play.fill()

let pointer = NSBezierPath()
pointer.move(to: NSPoint(x: center.x, y: center.y + outerRadius - 8))
pointer.line(to: NSPoint(x: center.x - 42, y: center.y + outerRadius + 62))
pointer.line(to: NSPoint(x: center.x + 42, y: center.y + outerRadius + 62))
pointer.close()
color(0xffd400).setStroke()
pointer.lineWidth = 16
pointer.stroke()
color(0xe60012).setFill()
pointer.fill()

context.flushGraphics()
NSGraphicsContext.restoreGraphicsState()

guard let png = bitmap.representation(using: .png, properties: [:]) else {
    fatalError("Could not encode icon")
}
try png.write(to: URL(fileURLWithPath: CommandLine.arguments[1]))
