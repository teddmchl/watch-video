"""Speaker embeddings for spans of a 16 kHz wav (run with the skill's own .venv python).

  embed.py MODEL WAV < spans.json   ->  JSON list: unit-length vector per span (null if too short)

A trained speaker model (WeSpeaker ResNet34 via sherpa-onnx) hears *who* is talking far more
reliably than pitch and timbre statistics, which shift with what is being said.
"""

import json
import sys

import numpy as np
import sherpa_onnx
import soundfile as sf


def main() -> int:
    model, wav = sys.argv[1], sys.argv[2]
    spans = json.load(sys.stdin)
    ex = sherpa_onnx.SpeakerEmbeddingExtractor(
        sherpa_onnx.SpeakerEmbeddingExtractorConfig(model=model, num_threads=4))
    audio, sr = sf.read(wav, dtype="float32", always_2d=False)
    if audio.ndim > 1:
        audio = audio.mean(axis=1)
    out = []
    for a, b in spans:
        seg = audio[int(a * sr):int(b * sr)]
        if len(seg) < int(0.4 * sr):
            out.append(None)
            continue
        s = ex.create_stream()
        s.accept_waveform(sr, seg)
        s.input_finished()
        if not ex.is_ready(s):
            out.append(None)
            continue
        e = np.array(ex.compute(s), dtype=np.float32)
        n = float(np.linalg.norm(e))
        out.append([round(float(v), 5) for v in (e / n)] if n else None)
    json.dump(out, sys.stdout)
    return 0


if __name__ == "__main__":
    sys.exit(main())
