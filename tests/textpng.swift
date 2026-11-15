// Test fixture only: renders white text on a transparent PNG.  swift textpng.swift "TEXT" out.png size
import CoreGraphics
import CoreText
import Foundation
import ImageIO
import UniformTypeIdentifiers

let a = CommandLine.arguments
let text = a[1], out = a[2], size = CGFloat(Double(a[3]) ?? 22)
let font = CTFontCreateWithName("Helvetica-Bold" as CFString, size, nil)
let attrs: [NSAttributedString.Key: Any] = [
    NSAttributedString.Key(kCTFontAttributeName as String): font,
    NSAttributedString.Key(kCTForegroundColorAttributeName as String): CGColor(srgbRed: 1, green: 1, blue: 1, alpha: 1),
]
let line = CTLineCreateWithAttributedString(NSAttributedString(string: text, attributes: attrs))
let b = CTLineGetBoundsWithOptions(line, [])
let w = Int(b.width) + 8, h = Int(size * 1.4)
let ctx = CGContext(data: nil, width: w, height: h, bitsPerComponent: 8, bytesPerRow: 0,
                    space: CGColorSpaceCreateDeviceRGB(), bitmapInfo: CGImageAlphaInfo.premultipliedLast.rawValue)!
ctx.textPosition = CGPoint(x: 4, y: size * 0.35)
CTLineDraw(line, ctx)
let dest = CGImageDestinationCreateWithURL(URL(fileURLWithPath: out) as CFURL, UTType.png.identifier as CFString, 1, nil)!
CGImageDestinationAddImage(dest, ctx.makeImage()!, nil)
CGImageDestinationFinalize(dest)
