// People and fine detail: faces (head pose, mouth movement), body pose, voice features,
// and multi-frame stacking for small things in low-resolution video.
//
//   vsense faces  img...                        -> JSON (box, yaw/pitch/roll, mouth opening)
//   vsense pose   img...                        -> JSON (body joints per person)
//   vsense voices --wav a.wav --spans a-b,c-d   -> JSON (pitch and timbre per span)
//   vsense stack  --out o.png [--scale 4] crop... (align and average: shift-and-add zoom)

import Accelerate
import AVFoundation
import CoreGraphics
import Foundation
import Vision

// MARK: - faces

func pointsSpan(_ pts: [CGPoint]) -> (Double, Double) {
    guard let minY = pts.map(\.y).min(), let maxY = pts.map(\.y).max(),
          let minX = pts.map(\.x).min(), let maxX = pts.map(\.x).max() else { return (0, 0) }
    return (Double(maxX - minX), Double(maxY - minY))
}

func faces(_ paths: [String]) throws {
    var results: [[String: Any]] = []
    for p in paths {
        let req = VNDetectFaceLandmarksRequest()
        do {
            try VNImageRequestHandler(cgImage: try loadImage(p)).perform([req])
        } catch {
            results.append(["file": p, "faces": [], "error": "\(error)"])
            continue
        }
        let list: [[String: Any]] = (req.results ?? []).map { f in
            var d: [String: Any] = ["box": topLeftBox(f.boundingBox), "conf": round3(Double(f.confidence))]
            if let v = f.yaw { d["yaw"] = round3(v.doubleValue) }
            if let v = f.pitch { d["pitch"] = round3(v.doubleValue) }
            if let v = f.roll { d["roll"] = round3(v.doubleValue) }
            if let lm = f.landmarks {
                // inner-lip opening relative to the face height: moves while someone talks
                if let inner = lm.innerLips?.normalizedPoints, let outer = lm.outerLips?.normalizedPoints {
                    let (_, ih) = pointsSpan(inner)
                    let (ow, _) = pointsSpan(outer)
                    d["mouth"] = round3(ih)
                    d["mouth_w"] = round3(ow)
                }
                if let l = lm.leftEye?.normalizedPoints, let r = lm.rightEye?.normalizedPoints {
                    d["eyes"] = round3((pointsSpan(l).1 + pointsSpan(r).1) / 2)
                    // eyebrow height above the eyes (rises in surprise, drops in a frown)
                    if let lb = lm.leftEyebrow?.normalizedPoints, let rb = lm.rightEyebrow?.normalizedPoints {
                        let brows = (lb + rb).map(\.y), eyes = (l + r).map(\.y)
                        d["brow"] = round3(Double(brows.reduce(0, +) / CGFloat(brows.count)
                                                  - eyes.reduce(0, +) / CGFloat(eyes.count)))
                    }
                }
                // smile: mouth corners lifted above the middle of the lips (y grows upward here)
                if let outer = lm.outerLips?.normalizedPoints, outer.count >= 4,
                   let left = outer.min(by: { $0.x < $1.x }), let right = outer.max(by: { $0.x < $1.x }) {
                    let mid = outer.map(\.y).reduce(0, +) / CGFloat(outer.count)
                    d["smile"] = round3(Double((left.y + right.y) / 2 - mid))
                }
            }
            return d
        }
        results.append(["file": p, "faces": list])
    }
    try printJSON(results)
}

// MARK: - body pose

let jointNames: [(VNHumanBodyPoseObservation.JointName, String)] = [
    (.nose, "nose"), (.neck, "neck"), (.leftShoulder, "lsh"), (.rightShoulder, "rsh"),
    (.leftElbow, "lel"), (.rightElbow, "rel"), (.leftWrist, "lwr"), (.rightWrist, "rwr"),
    (.leftHip, "lhip"), (.rightHip, "rhip"), (.root, "root"), (.leftKnee, "lkn"), (.rightKnee, "rkn"),
]

func pose(_ paths: [String]) throws {
    var results: [[String: Any]] = []
    for p in paths {
        let req = VNDetectHumanBodyPoseRequest()
        let handReq = VNDetectHumanHandPoseRequest()      // hands are found even without a body in shot
        handReq.maximumHandCount = 4
        do {
            try VNImageRequestHandler(cgImage: try loadImage(p)).perform([req, handReq])
        } catch {
            results.append(["file": p, "people": [], "hands": [], "error": "\(error)"])
            continue
        }
        let hands: [[String: Any]] = (handReq.results ?? []).compactMap { obs in
            guard let pts = try? obs.recognizedPoints(.all) else { return nil }
            let good = pts.values.filter { $0.confidence >= 0.3 }
            guard good.count >= 6 else { return nil }
            let xs = good.map { Double($0.location.x) }, ys = good.map { 1 - Double($0.location.y) }
            var d: [String: Any] = [
                "box": [round3(xs.min()!), round3(ys.min()!), round3(xs.max()! - xs.min()!), round3(ys.max()! - ys.min()!)],
                "c": [round3(xs.reduce(0, +) / Double(xs.count)), round3(ys.reduce(0, +) / Double(ys.count))],
            ]
            if let w = pts[.wrist], w.confidence >= 0.3 { d["wrist"] = [round3(Double(w.location.x)), round3(1 - Double(w.location.y))] }
            return d
        }
        let people: [[String: [Double]]] = (req.results ?? []).compactMap { obs in
            guard let pts = try? obs.recognizedPoints(.all) else { return nil }
            var joints: [String: [Double]] = [:]
            for (jn, short) in jointNames {
                if let pt = pts[jn], pt.confidence >= 0.3 {
                    joints[short] = [round3(Double(pt.location.x)), round3(1 - Double(pt.location.y)), round3(Double(pt.confidence))]
                }
            }
            return joints.count >= 4 ? joints : nil
        }
        results.append(["file": p, "people": people, "hands": hands])
    }
    try printJSON(results)
}

// MARK: - voices: pitch and timbre per speech span (for telling speakers apart)

func readMono16k(_ path: String) throws -> [Float] {
    let file = try AVAudioFile(forReading: URL(fileURLWithPath: path))
    let fmt = file.processingFormat
    guard let buf = AVAudioPCMBuffer(pcmFormat: fmt, frameCapacity: AVAudioFrameCount(file.length)) else {
        throw Failure("audio buffer failed")
    }
    try file.read(into: buf)
    guard let ch = buf.floatChannelData else { throw Failure("audio is not float PCM") }
    let samples = Array(UnsafeBufferPointer(start: ch[0], count: Int(buf.frameLength)))
    if Int(fmt.sampleRate) != 16_000 { throw Failure("voices expects 16 kHz audio, got \(fmt.sampleRate)") }
    return samples
}

final class MelBank {
    let n = 512, bands = 26, rate: Float = 16_000
    let filters: [[Float]]
    let dft: vDSP.DiscreteFourierTransform<Float>
    let window: [Float]

    init() throws {
        let n = 512, bands = 26, rate: Float = 16_000
        func mel(_ f: Float) -> Float { 2595 * log10(1 + f / 700) }
        func hz(_ m: Float) -> Float { 700 * (pow(10, m / 2595) - 1) }
        let lo = mel(80), hi = mel(7600)
        let edges = (0...(bands + 1)).map { hz(lo + (hi - lo) * Float($0) / Float(bands + 1)) }
        let bins = edges.map { Int(($0 / rate) * Float(n)) }
        var fs: [[Float]] = []
        for b in 0..<bands {
            var f = [Float](repeating: 0, count: n / 2)
            for k in bins[b]..<bins[b + 1] where k < n / 2 { f[k] = Float(k - bins[b]) / Float(max(1, bins[b + 1] - bins[b])) }
            for k in bins[b + 1]..<bins[b + 2] where k < n / 2 { f[k] = Float(bins[b + 2] - k) / Float(max(1, bins[b + 2] - bins[b + 1])) }
            fs.append(f)
        }
        filters = fs
        dft = try vDSP.DiscreteFourierTransform(count: n, direction: .forward, transformType: .complexComplex, ofType: Float.self)
        window = vDSP.window(ofType: Float.self, usingSequence: .hanningDenormalized, count: 400, isHalfWindow: false)
    }

    /// 12 cepstral coefficients (c1..c12) of one 25 ms frame.
    func mfcc(_ frame: ArraySlice<Float>) -> [Float] {
        var re = [Float](repeating: 0, count: n), im = [Float](repeating: 0, count: n)
        for (i, v) in frame.enumerated() where i < 400 { re[i] = v * window[i] }
        let (fr, fi) = dft.transform(real: re, imaginary: im)
        var power = [Float](repeating: 0, count: n / 2)
        for k in 0..<(n / 2) { power[k] = fr[k] * fr[k] + fi[k] * fi[k] }
        let logE = filters.map { log(max(1e-10, vDSP.dot($0, power))) }
        return (1...12).map { c in
            var s: Float = 0
            for (m, e) in logE.enumerated() { s += e * cos(Float.pi * Float(c) * (Float(m) + 0.5) / Float(bands)) }
            return s
        }
    }
}

/// Autocorrelation pitch of a 40 ms frame; nil when unvoiced.
func pitch(_ x: ArraySlice<Float>) -> Float? {
    let a = Array(x)
    let energy = vDSP.dot(a, a)
    guard energy > 1e-4 else { return nil }
    var best: Float = 0, lag = 0
    for l in 40...min(266, a.count - 1) {          // 400 Hz .. 60 Hz
        let r = vDSP.dot(Array(a[0..<(a.count - l)]), Array(a[l...])) / energy
        if r > best { best = r; lag = l }
    }
    return best >= 0.45 ? 16_000 / Float(lag) : nil
}

func voices(_ argv: [String]) throws {
    var args = argv
    guard let wav = flagValue(&args, "--wav"), let spanArg = flagValue(&args, "--spans") else {
        throw Failure("voices needs --wav and --spans a-b,c-d")
    }
    let x = try readMono16k(wav)
    let bank = try MelBank()
    var out: [[String: Any]] = []
    for span in spanArg.split(separator: ",") {
        let ab = span.split(separator: "-").compactMap { Double($0) }
        guard ab.count == 2 else { continue }
        let s = max(0, Int(ab[0] * 16_000)), e = min(x.count, Int(ab[1] * 16_000))
        var feats: [[Float]] = [], pitches: [Float] = []
        var i = s
        let peak = e > s ? (vDSP.maximumMagnitude(Array(x[s..<e]))) : 0
        while i + 640 <= e {
            let fr = x[i..<(i + 400)]
            let rms = sqrt(vDSP.meanSquare(Array(fr)))
            if rms > peak * 0.05 {                     // skip pauses inside the span
                feats.append(bank.mfcc(fr))
                if let f0 = pitch(x[i..<(i + 640)]) { pitches.append(f0) }
            }
            i += 160
        }
        var d: [String: Any] = ["start": ab[0], "end": ab[1], "frames": feats.count]
        if !feats.isEmpty {
            let dims = feats[0].count
            let mean = (0..<dims).map { k in feats.map { $0[k] }.reduce(0, +) / Float(feats.count) }
            let sd = (0..<dims).map { k in sqrt(feats.map { pow($0[k] - mean[k], 2) }.reduce(0, +) / Float(feats.count)) }
            d["mfcc"] = mean.map { round3(Double($0)) }
            d["mfcc_sd"] = sd.map { round3(Double($0)) }
        }
        if pitches.count >= 5 {
            let sorted = pitches.sorted()
            d["f0"] = round3(Double(sorted[sorted.count / 2]))
            d["f0_lo"] = round3(Double(sorted[sorted.count / 10]))
            d["f0_hi"] = round3(Double(sorted[sorted.count * 9 / 10]))
            d["voiced"] = round3(Double(pitches.count) / Double(max(1, feats.count)))
        }
        out.append(d)
    }
    try printJSON(out)
}

// MARK: - stack: shift-and-add super-resolution over neighbouring frames

func gray(_ r: RGBA) -> [Float] {
    (0..<(r.w * r.h)).map { i in
        0.299 * Float(r.px[i * 4]) + 0.587 * Float(r.px[i * 4 + 1]) + 0.114 * Float(r.px[i * 4 + 2])
    }
}

func stack(_ argv: [String]) throws {
    var args = argv
    guard let out = flagValue(&args, "--out") else { throw Failure("stack needs --out") }
    let scale = Int(flagValue(&args, "--scale") ?? "4") ?? 4
    guard args.count >= 2 else { throw Failure("stack needs at least 2 crops") }
    let first = try loadImage(args[0])
    let w = first.width * scale, h = first.height * scale
    let frames = try args.map { try rasterize(try loadImage($0), w, h) }   // bicubic upscale
    let ref = gray(frames[frames.count / 2])
    let r = scale * 3                                                        // ±3 source pixels
    var acc = [Float](repeating: 0, count: w * h * 3)
    var cnt = [Float](repeating: 0, count: w * h)
    for f in frames {
        let g = gray(f)
        var best = (0, 0), bestCost = Float.greatestFiniteMagnitude
        // coarse-to-fine would be faster; crops are small so a full search is fine
        for dy in stride(from: -r, through: r, by: 1) {
            for dx in stride(from: -r, through: r, by: 1) {
                var cost: Float = 0, n: Float = 0
                for y in stride(from: r, to: h - r, by: 3) {
                    for x in stride(from: r, to: w - r, by: 3) {
                        cost += abs(g[(y + dy) * w + (x + dx)] - ref[y * w + x]); n += 1
                    }
                }
                if n > 0, cost / n < bestCost { bestCost = cost / n; best = (dx, dy) }
            }
        }
        if bestCost > 40 { continue }   // this frame shows something else (motion blur, occlusion)
        for y in 0..<h {
            let sy = y + best.1
            guard sy >= 0, sy < h else { continue }
            for x in 0..<w {
                let sx = x + best.0
                guard sx >= 0, sx < w else { continue }
                let o = (sy * w + sx) * 4, t = y * w + x
                acc[t * 3] += Float(f.px[o]); acc[t * 3 + 1] += Float(f.px[o + 1]); acc[t * 3 + 2] += Float(f.px[o + 2])
                cnt[t] += 1
            }
        }
    }
    // average, then a light unsharp mask to restore edges the averaging softened
    var avg = [Float](repeating: 0, count: w * h * 3)
    for t in 0..<(w * h) where cnt[t] > 0 { for c in 0..<3 { avg[t * 3 + c] = acc[t * 3 + c] / cnt[t] } }
    let ctx = try makeContext(w, h)
    guard let data = ctx.data?.assumingMemoryBound(to: UInt8.self) else { throw Failure("context failed") }
    let bpr = ctx.bytesPerRow
    for y in 0..<h {
        for x in 0..<w {
            for c in 0..<3 {
                let v = avg[(y * w + x) * 3 + c]
                var blur: Float = 0, n: Float = 0
                for (dx, dy) in [(-2, 0), (2, 0), (0, -2), (0, 2)] {
                    let xx = min(w - 1, max(0, x + dx)), yy = min(h - 1, max(0, y + dy))
                    blur += avg[(yy * w + xx) * 3 + c]; n += 1
                }
                let sharp = v + 0.6 * (v - blur / n)
                let o = y * bpr + x * 4          // bitmap memory rows run top-down
                data[o + c] = UInt8(max(0, min(255, sharp)))
            }
            data[y * bpr + x * 4 + 3] = 255
        }
    }
    guard let img = ctx.makeImage() else { throw Failure("render failed") }
    try save(img, to: out)
    try printJSON(["out": out, "frames": frames.count, "scale": scale])
}

