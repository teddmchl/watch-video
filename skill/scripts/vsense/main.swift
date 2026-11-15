// vsense: on-device perception helpers for the watch-video skill.
// Uses only Apple frameworks (Vision, SoundAnalysis, CoreGraphics), so nothing is downloaded.
//
//   vsense sheet  --out o.jpg [--cols 4] [--tile 384] [--title T] label=path ...
//   vsense plot   --out o.png --spec spec.json
//   vsense strobe --out o.jpg [--threshold 70] frame...   (motion trail in one image)
//   ffmpeg ...gray raw... | vsense motion --w W --h H --fps F   (camera-compensated motion)
//   vsense color --before b.jpg --after a.jpg --box x,y,w,h      (colour of what changed)
//   vsense ocr    img...            -> JSON
//   vsense tags   img...            -> JSON (scene labels, people, faces)
//   vsense saliency img             -> JSON (salient boxes, top-left origin, normalized)
//   vsense sounds audio.wav         -> JSON (sound classes per 1s window)
//   faces, pose, voices, stack: see people.swift

import AVFoundation
import CoreGraphics
import CoreText
import Foundation
import ImageIO
import SoundAnalysis
import UniformTypeIdentifiers
import Vision

// MARK: - helpers

struct Failure: Error, CustomStringConvertible {
    let description: String
    init(_ d: String) { description = d }
}

func loadImage(_ path: String) throws -> CGImage {
    let url = URL(fileURLWithPath: path) as CFURL
    guard let src = CGImageSourceCreateWithURL(url, nil),
          let img = CGImageSourceCreateImageAtIndex(src, 0, nil)
    else { throw Failure("cannot read image \(path)") }
    return img
}

func save(_ img: CGImage, to path: String) throws {
    let isPNG = path.lowercased().hasSuffix(".png")
    let type = (isPNG ? UTType.png : UTType.jpeg).identifier as CFString
    let url = URL(fileURLWithPath: path) as CFURL
    guard let dest = CGImageDestinationCreateWithURL(url, type, 1, nil) else {
        throw Failure("cannot write \(path)")
    }
    let opts = [kCGImageDestinationLossyCompressionQuality: 0.82] as CFDictionary
    CGImageDestinationAddImage(dest, img, isPNG ? nil : opts)
    guard CGImageDestinationFinalize(dest) else { throw Failure("cannot finalize \(path)") }
}

func makeContext(_ w: Int, _ h: Int) throws -> CGContext {
    guard let ctx = CGContext(
        data: nil, width: w, height: h, bitsPerComponent: 8, bytesPerRow: 0,
        space: CGColorSpaceCreateDeviceRGB(),
        bitmapInfo: CGImageAlphaInfo.premultipliedLast.rawValue)
    else { throw Failure("cannot create \(w)x\(h) canvas") }
    return ctx
}

func rgb(_ r: CGFloat, _ g: CGFloat, _ b: CGFloat, _ a: CGFloat = 1) -> CGColor {
    CGColor(srgbRed: r, green: g, blue: b, alpha: a)
}

func hexColor(_ hex: String) -> CGColor {
    var s = hex.trimmingCharacters(in: .whitespaces)
    if s.hasPrefix("#") { s.removeFirst() }
    let v = UInt32(s, radix: 16) ?? 0xFFFFFF
    return rgb(CGFloat((v >> 16) & 0xFF) / 255, CGFloat((v >> 8) & 0xFF) / 255, CGFloat(v & 0xFF) / 255)
}

/// Draws text with its baseline at (x, y) in CoreGraphics (bottom-left) coordinates.
func drawText(_ ctx: CGContext, _ s: String, x: CGFloat, y: CGFloat, size: CGFloat,
              color: CGColor, bold: Bool = true) {
    let font = CTFontCreateWithName((bold ? "Menlo-Bold" : "Menlo-Regular") as CFString, size, nil)
    let attrs: [NSAttributedString.Key: Any] = [
        NSAttributedString.Key(kCTFontAttributeName as String): font,
        NSAttributedString.Key(kCTForegroundColorAttributeName as String): color,
    ]
    let line = CTLineCreateWithAttributedString(NSAttributedString(string: s, attributes: attrs))
    ctx.textPosition = CGPoint(x: x, y: y)
    CTLineDraw(line, ctx)
}

func printJSON(_ obj: Any) throws {
    let data = try JSONSerialization.data(withJSONObject: obj, options: [.sortedKeys])
    FileHandle.standardOutput.write(data)
    FileHandle.standardOutput.write("\n".data(using: .utf8)!)
}

func flagValue(_ args: inout [String], _ name: String) -> String? {
    guard let i = args.firstIndex(of: name), i + 1 < args.count else { return nil }
    let v = args[i + 1]
    args.removeSubrange(i...(i + 1))
    return v
}

func round3(_ v: Double) -> Double { (v * 1000).rounded() / 1000 }

// MARK: - sheet: labelled contact sheet

func sheet(_ argv: [String]) throws {
    var args = argv
    guard let out = flagValue(&args, "--out") else { throw Failure("sheet needs --out") }
    let cols = max(1, Int(flagValue(&args, "--cols") ?? "4") ?? 4)
    let tileW = max(64, Int(flagValue(&args, "--tile") ?? "384") ?? 384)
    let title = flagValue(&args, "--title")
    let items: [(String, CGImage)] = try args.map { item in
        guard let eq = item.firstIndex(of: "=") else { throw Failure("expected label=path, got \(item)") }
        return (String(item[..<eq]), try loadImage(String(item[item.index(after: eq)...])))
    }
    guard let first = items.first?.1 else { throw Failure("sheet needs at least one image") }
    let aspect = CGFloat(first.height) / CGFloat(first.width)
    let tileH = Int((CGFloat(tileW) * aspect).rounded())
    let labelH = 20, pad = 4, titleH = title == nil ? 0 : 26
    let rows = (items.count + cols - 1) / cols
    let W = cols * tileW + (cols + 1) * pad
    let H = titleH + rows * (tileH + labelH) + (rows + 1) * pad
    let ctx = try makeContext(W, H)
    ctx.setFillColor(rgb(0.08, 0.08, 0.09))
    ctx.fill(CGRect(x: 0, y: 0, width: W, height: H))
    if let title {
        drawText(ctx, title, x: CGFloat(pad + 2), y: CGFloat(H - titleH + 7), size: 15, color: rgb(1, 0.85, 0.3))
    }
    for (i, (label, img)) in items.enumerated() {
        let r = i / cols, c = i % cols
        let x = pad + c * (tileW + pad)
        let topY = titleH + pad + r * (tileH + labelH + pad)  // distance from top
        let imgY = H - topY - tileH
        ctx.interpolationQuality = .high
        ctx.draw(img, in: CGRect(x: x, y: imgY, width: tileW, height: tileH))
        ctx.setFillColor(rgb(0, 0, 0, 0.85))
        ctx.fill(CGRect(x: x, y: imgY - labelH, width: tileW, height: labelH))
        drawText(ctx, label, x: CGFloat(x + 4), y: CGFloat(imgY - labelH + 5), size: 13, color: rgb(1, 1, 1))
    }
    guard let img = ctx.makeImage() else { throw Failure("render failed") }
    try save(img, to: out)
}

// MARK: - plot: signal chart with event markers

func plot(_ argv: [String]) throws {
    var args = argv
    guard let out = flagValue(&args, "--out"), let specPath = flagValue(&args, "--spec") else {
        throw Failure("plot needs --out and --spec")
    }
    let data = try Data(contentsOf: URL(fileURLWithPath: specPath))
    guard let spec = try JSONSerialization.jsonObject(with: data) as? [String: Any],
          let duration = spec["duration"] as? Double, duration > 0
    else { throw Failure("bad plot spec") }
    let series = spec["series"] as? [[String: Any]] ?? []
    let markers = spec["markers"] as? [[String: Any]] ?? []
    let W = spec["width"] as? Int ?? 1536
    let laneH = 70, top = 30, bottom = 28, left = 110, right = 12
    let H = top + bottom + max(1, series.count) * laneH
    let ctx = try makeContext(W, H)
    ctx.setFillColor(rgb(0.07, 0.07, 0.08))
    ctx.fill(CGRect(x: 0, y: 0, width: W, height: H))
    let plotW = CGFloat(W - left - right)
    func xOf(_ t: Double) -> CGFloat { CGFloat(left) + CGFloat(t / duration) * plotW }

    if let title = spec["title"] as? String {
        drawText(ctx, title, x: 8, y: CGFloat(H - 20), size: 14, color: rgb(1, 0.85, 0.3))
    }
    // time grid
    let step = [1.0, 2, 5, 10, 15, 30, 60, 120, 300, 600, 900, 1800].first { duration / $0 <= 14 } ?? 3600
    var t = 0.0
    while t <= duration + 0.001 {
        let x = xOf(t)
        ctx.setStrokeColor(rgb(1, 1, 1, 0.12)); ctx.setLineWidth(1)
        ctx.move(to: CGPoint(x: x, y: CGFloat(bottom))); ctx.addLine(to: CGPoint(x: x, y: CGFloat(H - top)))
        ctx.strokePath()
        let m = Int(t) / 60, s = Int(t) % 60
        drawText(ctx, String(format: "%d:%02d", m, s), x: x - 14, y: 8, size: 11, color: rgb(0.8, 0.8, 0.8), bold: false)
        t += step
    }
    // lanes
    for (i, s) in series.enumerated() {
        let laneTop = H - top - i * laneH
        let base = CGFloat(laneTop - laneH + 6), height = CGFloat(laneH - 14)
        let color = hexColor(s["color"] as? String ?? "#4FC3F7")
        drawText(ctx, s["name"] as? String ?? "", x: 8, y: base + height / 2 - 4, size: 12, color: color)
        ctx.setStrokeColor(rgb(1, 1, 1, 0.2))
        ctx.stroke(CGRect(x: CGFloat(left), y: base, width: plotW, height: height))
        let pts = (s["points"] as? [[Double]]) ?? []
        guard !pts.isEmpty else { continue }
        let path = CGMutablePath()
        path.move(to: CGPoint(x: xOf(pts[0][0]), y: base))
        for p in pts where p.count >= 2 {
            path.addLine(to: CGPoint(x: xOf(p[0]), y: base + CGFloat(min(1, max(0, p[1]))) * height))
        }
        path.addLine(to: CGPoint(x: xOf(pts[pts.count - 1][0]), y: base))
        path.closeSubpath()
        ctx.addPath(path); ctx.setFillColor(color.copy(alpha: 0.55)!); ctx.fillPath()
    }
    // markers
    for (i, m) in markers.enumerated() {
        guard let mt = m["t"] as? Double else { continue }
        let x = xOf(mt)
        let color = hexColor(m["color"] as? String ?? "#FFFFFF")
        ctx.setStrokeColor(color.copy(alpha: 0.8)!); ctx.setLineWidth(1.5)
        ctx.move(to: CGPoint(x: x, y: CGFloat(bottom))); ctx.addLine(to: CGPoint(x: x, y: CGFloat(H - top)))
        ctx.strokePath()
        if let label = m["label"] as? String {
            let yOff = CGFloat(H - top + 2 - (i % 2) * 12)
            drawText(ctx, label, x: x + 2, y: yOff, size: 10, color: color)
        }
    }
    guard let img = ctx.makeImage() else { throw Failure("render failed") }
    try save(img, to: out)
}

// MARK: - strobe: one image showing a whole movement (chronophotography)

struct RGBA {
    let w: Int, h: Int
    var px: [UInt8]
}

func rasterize(_ img: CGImage, _ w: Int, _ h: Int) throws -> RGBA {
    var px = [UInt8](repeating: 0, count: w * h * 4)
    try px.withUnsafeMutableBytes { buf in
        guard let ctx = CGContext(
            data: buf.baseAddress, width: w, height: h, bitsPerComponent: 8, bytesPerRow: w * 4,
            space: CGColorSpaceCreateDeviceRGB(), bitmapInfo: CGImageAlphaInfo.premultipliedLast.rawValue)
        else { throw Failure("raster failed") }
        ctx.draw(img, in: CGRect(x: 0, y: 0, width: w, height: h))
    }
    return RGBA(w: w, h: h, px: px)
}

/// Background = per-pixel median of all frames. Every pixel that differs strongly from the
/// background is painted from its frame, later frames on top, so a moving thing leaves a trail.
func strobe(_ argv: [String]) throws {
    var args = argv
    guard let out = flagValue(&args, "--out") else { throw Failure("strobe needs --out") }
    let threshold = Int(flagValue(&args, "--threshold") ?? "70") ?? 70
    let images = try args.map(loadImage)
    guard let first = images.first, images.count >= 2 else { throw Failure("strobe needs 2+ frames") }
    let w = min(first.width, 960), h = Int(Double(first.height) * Double(w) / Double(first.width))
    let frames = try images.map { try rasterize($0, w, h) }
    let n = frames.count, count = w * h
    var bg = [UInt8](repeating: 255, count: count * 4)
    var column = [UInt8](repeating: 0, count: n)
    for i in 0..<count {
        for c in 0..<3 {
            for f in 0..<n { column[f] = frames[f].px[i * 4 + c] }
            column.sort()
            bg[i * 4 + c] = column[n / 2]
        }
    }
    // Earlier copies are faint, later ones solid: the brightest copy is where it ended up.
    var outPx = bg
    for (fi, f) in frames.enumerated() {
        let alpha = 0.25 + 0.75 * Double(fi) / Double(max(1, n - 1))
        for i in 0..<count {
            let o = i * 4
            let d = abs(Int(f.px[o]) - Int(bg[o])) + abs(Int(f.px[o + 1]) - Int(bg[o + 1]))
                + abs(Int(f.px[o + 2]) - Int(bg[o + 2]))
            if d > threshold {
                for c in 0..<3 {
                    let mixed = Double(bg[o + c]) * (1 - alpha) + Double(f.px[o + c]) * alpha
                    outPx[o + c] = UInt8(max(0, min(255, mixed.rounded())))
                }
            }
        }
    }
    let ctx: CGContext? = outPx.withUnsafeMutableBytes { buf in
        CGContext(data: buf.baseAddress, width: w, height: h, bitsPerComponent: 8, bytesPerRow: w * 4,
                  space: CGColorSpaceCreateDeviceRGB(),
                  bitmapInfo: CGImageAlphaInfo.premultipliedLast.rawValue)
    }
    guard let base = ctx?.makeImage(), let canvas = try? makeContext(w, h) else {
        throw Failure("strobe render failed")
    }
    canvas.draw(base, in: CGRect(x: 0, y: 0, width: w, height: h))
    // Number each moment at the centre of what changed in it: 1 = first, so up-and-back
    // movements and reversals stay readable.
    for (fi, f) in frames.enumerated() {
        var sx = 0.0, sy = 0.0, k = 0.0
        for i in Swift.stride(from: 0, to: count, by: 3) {
            let o = i * 4
            let d = abs(Int(f.px[o]) - Int(bg[o])) + abs(Int(f.px[o + 1]) - Int(bg[o + 1]))
                + abs(Int(f.px[o + 2]) - Int(bg[o + 2]))
            if d > threshold { sx += Double(i % w); sy += Double(i / w); k += 1 }
        }
        guard k >= 6 else { continue }
        let cx = CGFloat(sx / k), cy = CGFloat(Double(h) - sy / k)   // memory rows top-down, CG origin bottom-left
        canvas.setFillColor(rgb(0, 0, 0, 0.75))
        canvas.fillEllipse(in: CGRect(x: cx - 9, y: cy - 9, width: 18, height: 18))
        drawText(canvas, "\(fi + 1)", x: cx - (fi + 1 >= 10 ? 7 : 4), y: cy - 4, size: 11, color: rgb(1, 1, 0.4))
    }
    guard let img = canvas.makeImage() else { throw Failure("strobe render failed") }
    try save(img, to: out)
}

// MARK: - motion: camera-compensated motion from raw grey frames on stdin
//
// ffmpeg ... -vf fps=F,scale=W:H,format=gray -f rawvideo - | vsense motion --w W --h H --fps F
// For each frame: estimate the camera shift (global translation) against the previous frame,
// then measure what still changed after undoing it. That residual is motion IN the scene
// (a ball, a person) rather than the camera panning. Output: one JSON object per frame.

func meanAbsDiff(_ a: [UInt8], _ b: [UInt8], _ w: Int, _ h: Int, _ dx: Int, _ dy: Int,
                 margin: Int, step: Int) -> Double {
    var total = 0, count = 0
    var y = margin
    while y < h - margin {
        let yb = y + dy
        if yb >= 0 && yb < h {
            var x = margin
            while x < w - margin {
                let xb = x + dx
                if xb >= 0 && xb < w {
                    total += abs(Int(a[y * w + x]) - Int(b[yb * w + xb]))
                    count += 1
                }
                x += step
            }
        }
        y += step
    }
    return count > 0 ? Double(total) / Double(count) : 255
}

/// Shift (dx, dy) such that cur(x, y) ≈ prev(x + dx, y + dy). Coarse search on every 2nd pixel, then refine.
func cameraShift(_ prev: [UInt8], _ cur: [UInt8], _ w: Int, _ h: Int, range r: Int) -> (Int, Int, Double, Double) {
    let margin = r + 1
    let still = meanAbsDiff(cur, prev, w, h, 0, 0, margin: margin, step: 2)
    // A camera move changes most of the picture. If only a small part changed, something moved
    // in front of a still camera; aligning to it would wrongly "explain away" that object.
    var moved = 0, sampled = 0
    for y in Swift.stride(from: margin, to: h - margin, by: 2) {
        for x in Swift.stride(from: margin, to: w - margin, by: 2) {
            sampled += 1
            if abs(Int(cur[y * w + x]) - Int(prev[y * w + x])) > 12 { moved += 1 }
        }
    }
    if Double(moved) < Double(max(1, sampled)) * 0.25 { return (0, 0, still, still) }
    var best = (0, 0, still)
    var dy = -r
    while dy <= r {
        var dx = -r
        while dx <= r {
            let d = meanAbsDiff(cur, prev, w, h, dx, dy, margin: margin, step: 2)
            if d < best.2 { best = (dx, dy, d) }
            dx += 2
        }
        dy += 2
    }
    var fine = best
    for fy in (best.1 - 1)...(best.1 + 1) {
        for fx in (best.0 - 1)...(best.0 + 1) where abs(fx) <= r && abs(fy) <= r {
            let d = meanAbsDiff(cur, prev, w, h, fx, fy, margin: margin, step: 1)
            if d < fine.2 { fine = (fx, fy, d) }
        }
    }
    // Only trust a shift that clearly beats "no camera movement".
    if fine.2 > still * 0.85 { return (0, 0, still, still) }
    return (fine.0, fine.1, fine.2, still)
}

func motion(_ argv: [String]) throws {
    var args = argv
    guard let w = Int(flagValue(&args, "--w") ?? ""), let h = Int(flagValue(&args, "--h") ?? ""),
          let fps = Double(flagValue(&args, "--fps") ?? "") else {
        throw Failure("motion needs --w --h --fps")
    }
    let cols = Int(flagValue(&args, "--cols") ?? "32") ?? 32
    let rows = Int(flagValue(&args, "--rows") ?? "18") ?? 18
    let threshold = Int(flagValue(&args, "--threshold") ?? "24") ?? 24
    let range = max(2, w / 20)                       // up to 5% of the width per frame
    let frameSize = w * h
    let input = FileHandle.standardInput
    var prev: [UInt8]? = nil
    var index = 0
    var buffer = Data()
    let out = FileHandle.standardOutput
    while true {
        while buffer.count < frameSize {
            let chunk = input.readData(ofLength: max(65536, frameSize))
            if chunk.isEmpty { break }
            buffer.append(chunk)
        }
        if buffer.count < frameSize { break }
        let cur = [UInt8](buffer.prefix(frameSize))
        buffer.removeFirst(frameSize)
        defer { prev = cur; index += 1 }
        guard let p = prev else { continue }
        let (dx, dy, _, _) = cameraShift(p, cur, w, h, range: range)
        var cells = [Int](repeating: 0, count: cols * rows)
        var cellPx = [Int](repeating: 0, count: cols * rows)
        var changed = 0, valid = 0
        for y in 0..<h {
            let yb = y + dy
            for x in 0..<w {
                let cell = min(rows - 1, y * rows / h) * cols + min(cols - 1, x * cols / w)
                let xb = x + dx
                guard xb >= 0, xb < w, yb >= 0, yb < h else { continue }
                valid += 1
                cellPx[cell] += 1
                if abs(Int(cur[y * w + x]) - Int(p[yb * w + xb])) > threshold {
                    changed += 1
                    cells[cell] += 1
                }
            }
        }
        let shares = zip(cells, cellPx).map { $1 > 0 ? Double($0) / Double($1) : 0 }
        let frac = valid > 0 ? Double(changed) / Double(valid) : 0
        var cx = 0.0, cy = 0.0, wsum = 0.0
        for (i, s) in shares.enumerated() where s > 0 {
            cx += (Double(i % cols) + 0.5) * s; cy += (Double(i / cols) + 0.5) * s; wsum += s
        }
        var rec: [String: Any] = [
            "t": round3(Double(index) / fps),
            "frac": (frac * 100_000).rounded() / 100_000,
            "peak": round3(shares.max() ?? 0),
            // camera velocity in screen-widths (x) and screen-heights (y) per second;
            // positive x = scene content moved left, i.e. camera panned right.
            "cam": [round3(Double(dx) / Double(w) * fps), round3(Double(dy) / Double(h) * fps)],
            "cells": shares.enumerated().filter { $0.element >= 0.06 }.map { $0.offset },
        ]
        if wsum > 0 { rec["c"] = [round3(cx / wsum / Double(cols)), round3(cy / wsum / Double(rows))] }
        let data = try JSONSerialization.data(withJSONObject: rec, options: [.sortedKeys])
        out.write(data)
        out.write("\n".data(using: .utf8)!)
    }
}

// MARK: - color: what colour is the thing that changed?

/// Compares the same box in a before and an after frame, keeps the pixels that changed, and
/// reports their average colour in each: "new" = what appeared, "old" = what was there.
func colorCmd(_ argv: [String]) throws {
    var args = argv
    guard let beforePath = flagValue(&args, "--before"), let afterPath = flagValue(&args, "--after"),
          let boxStr = flagValue(&args, "--box") else { throw Failure("color needs --before --after --box") }
    let box = boxStr.split(separator: ",").compactMap { Double($0) }
    guard box.count == 4 else { throw Failure("box must be x,y,w,h") }
    let a = try loadImage(afterPath)
    let w = min(a.width, 640), h = Int(Double(a.height) * Double(w) / Double(a.width))
    let before = try rasterize(try loadImage(beforePath), w, h), after = try rasterize(a, w, h)
    let x0 = max(0, Int(box[0] * Double(w))), y0 = max(0, Int(box[1] * Double(h)))
    let x1 = min(w, Int((box[0] + box[2]) * Double(w)) + 1), y1 = min(h, Int((box[1] + box[3]) * Double(h)) + 1)
    var newSum = [0.0, 0.0, 0.0], oldSum = [0.0, 0.0, 0.0], changed = 0, total = 0
    for y in y0..<max(y0, y1) {
        for x in x0..<max(x0, x1) {
            let o = (y * w + x) * 4          // bitmap memory rows run top to bottom
            total += 1
            let d = (0..<3).reduce(0) { $0 + abs(Int(after.px[o + $1]) - Int(before.px[o + $1])) }
            if d > 60 {
                changed += 1
                for c in 0..<3 { newSum[c] += Double(after.px[o + c]); oldSum[c] += Double(before.px[o + c]) }
            }
        }
    }
    let n = Double(max(1, changed))
    try printJSON([
        "new": newSum.map { ($0 / n).rounded() }, "old": oldSum.map { ($0 / n).rounded() },
        "share": round3(Double(changed) / Double(max(1, total))),
    ])
}

// MARK: - Vision: ocr, tags, saliency

/// Vision boxes are normalized with a bottom-left origin; convert to top-left.
func topLeftBox(_ r: CGRect) -> [Double] {
    [round3(r.minX), round3(1 - r.maxY), round3(r.width), round3(r.height)]
}

func ocr(_ paths: [String]) throws {
    var results: [[String: Any]] = []
    for p in paths {
        let req = VNRecognizeTextRequest()
        req.recognitionLevel = .accurate
        req.usesLanguageCorrection = true
        req.automaticallyDetectsLanguage = true
        try VNImageRequestHandler(cgImage: try loadImage(p)).perform([req])
        let lines: [[String: Any]] = (req.results ?? []).compactMap { obs in
            guard let top = obs.topCandidates(1).first, top.confidence >= 0.3 else { return nil }
            return ["text": top.string, "conf": round3(Double(top.confidence)), "box": topLeftBox(obs.boundingBox)]
        }
        results.append(["file": p, "lines": lines])
    }
    try printJSON(results)
}

func tags(_ paths: [String]) throws {
    var results: [[String: Any]] = []
    for p in paths {
        let classify = VNClassifyImageRequest()
        let humans = VNDetectHumanRectanglesRequest()
        let faces = VNDetectFaceRectanglesRequest()
        try VNImageRequestHandler(cgImage: try loadImage(p)).perform([classify, humans, faces])
        let labels: [[Any]] = (classify.results ?? [])
            .filter { $0.confidence >= 0.25 }
            .sorted { $0.confidence > $1.confidence }
            .prefix(6)
            .map { [$0.identifier, round3(Double($0.confidence))] }
        let people = (humans.results ?? []).map { topLeftBox($0.boundingBox) }
        results.append([
            "file": p, "labels": labels,
            "people": people.count, "people_boxes": people,
            "faces": (faces.results ?? []).count,
        ])
    }
    try printJSON(results)
}

func saliency(_ paths: [String]) throws {
    var results: [[String: Any]] = []
    for p in paths {
        let req = VNGenerateObjectnessBasedSaliencyImageRequest()
        try VNImageRequestHandler(cgImage: try loadImage(p)).perform([req])
        let boxes = (req.results?.first?.salientObjects ?? [])
            .sorted { $0.confidence > $1.confidence }
            .map { ["box": topLeftBox($0.boundingBox), "conf": round3(Double($0.confidence))] as [String: Any] }
        results.append(["file": p, "objects": boxes])
    }
    try printJSON(results)
}

// MARK: - SoundAnalysis: what kinds of sound, when

final class SoundObserver: NSObject, SNResultsObserving {
    var windows: [[String: Any]] = []
    var failure: String?
    func request(_ request: SNRequest, didProduce result: SNResult) {
        guard let r = result as? SNClassificationResult else { return }
        let top = r.classifications
            .filter { $0.confidence >= 0.3 }
            .prefix(3)
            .map { [$0.identifier, round3($0.confidence)] as [Any] }
        guard !top.isEmpty else { return }
        windows.append([
            "start": round3(r.timeRange.start.seconds),
            "end": round3(CMTimeAdd(r.timeRange.start, r.timeRange.duration).seconds),
            "top": top,
        ])
    }
    func request(_ request: SNRequest, didFailWithError error: Error) { failure = "\(error)" }
}

func sounds(_ path: String) throws {
    let analyzer = try SNAudioFileAnalyzer(url: URL(fileURLWithPath: path))
    let req = try SNClassifySoundRequest(classifierIdentifier: .version1)
    req.windowDuration = CMTime(seconds: 1.0, preferredTimescale: 48_000)
    req.overlapFactor = 0.5
    let obs = SoundObserver()
    try analyzer.add(req, withObserver: obs)
    analyzer.analyze()
    if let f = obs.failure { throw Failure("sound analysis failed: \(f)") }
    try printJSON(obs.windows)
}

// MARK: - main

let argv = Array(CommandLine.arguments.dropFirst())
guard let cmd = argv.first else {
    FileHandle.standardError.write("usage: vsense sheet|plot|ocr|tags|saliency|sounds ...\n".data(using: .utf8)!)
    exit(2)
}
let rest = Array(argv.dropFirst())
do {
    switch cmd {
    case "sheet": try sheet(rest)
    case "plot": try plot(rest)
    case "strobe": try strobe(rest)
    case "motion": try motion(rest)
    case "color": try colorCmd(rest)
    case "ocr": try ocr(rest)
    case "tags": try tags(rest)
    case "saliency": try saliency(rest)
    case "faces": try faces(rest)
    case "pose": try pose(rest)
    case "voices": try voices(rest)
    case "stack": try stack(rest)
    case "sounds":
        guard let p = rest.first else { throw Failure("sounds needs an audio file") }
        try sounds(p)
    default: throw Failure("unknown command \(cmd)")
    }
} catch {
    FileHandle.standardError.write("vsense: \(error)\n".data(using: .utf8)!)
    exit(1)
}
