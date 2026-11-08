"""Read narration.txt aloud with Kokoro (voice bm_george), one WAV per [section], into OUTDIR.
Usage: voice.py KOKORO_DIR OUTDIR [SCRIPT]   (KOKORO_DIR holds kokoro-v1.0.onnx and voices-v1.0.bin;
SCRIPT defaults to narration.txt here). In the text, [pause] is a short silence."""
import os
import re
import sys
import numpy as np
import soundfile as sf
from kokoro_onnx import Kokoro

VOICE, LANG, SPEED, PAUSE = "bm_george", "en-gb", 0.95, 0.7
# Words the British voice says in a way the audience won't expect: word -> phonemes to use instead
SAY = {"narrator": "nˈɛɹeɪtə"}

model, out = sys.argv[1:3]
os.makedirs(out, exist_ok=True)
k = Kokoro(f"{model}/kokoro-v1.0.onnx", f"{model}/voices-v1.0.bin")
text = open(sys.argv[3] if len(sys.argv) > 3 else os.path.join(os.path.dirname(os.path.abspath(__file__)), "narration.txt")).read()
fixes = {k.tokenizer.phonemize(w, LANG): p for w, p in SAY.items()}


def speak(t):
    ph = k.tokenizer.phonemize(t, LANG)
    for was, now in fixes.items():
        ph = ph.replace(was, now)
    return k.create(ph, voice=VOICE, speed=SPEED, lang=LANG, is_phonemes=True)


sections = re.findall(r"\[(\w+)\]\n(.*?)(?=\n\[(?!pause\])|\Z)", text, re.S)
open(f"{out}/order.txt", "w").write(" ".join(n for n, _ in sections) + "\n")
for name, body in sections:
    parts = []
    for i, chunk in enumerate(" ".join(body.split()).split("[pause]")):
        audio, rate = speak(chunk.strip())
        parts += ([np.zeros(int(PAUSE * rate), dtype=audio.dtype)] if i else []) + [audio]
    audio = np.concatenate(parts)
    sf.write(f"{out}/{name}.wav", audio, rate)
    print(f"{name} {len(audio) / rate:.1f}")
