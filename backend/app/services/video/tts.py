"""Local text-to-speech. Engines: Windows SAPI / espeak via pyttsx3 (run in a subprocess to isolate COM),
or Piper (if configured). Returns None when no engine works; the renderer then uses a silent track."""
from __future__ import annotations

import logging
import subprocess
import sys
import wave
from pathlib import Path

log = logging.getLogger(__name__)

# Windows: SAPI through comtypes with an explicit 48 kHz file stream (pyttsx3.save_to_file can write empty WAVs).
_SAPI_CLI = r"""
import sys, comtypes.client
text, out, voice_name, wpm = sys.argv[1], sys.argv[2], sys.argv[3], int(sys.argv[4])
voice = comtypes.client.CreateObject('SAPI.SpVoice')
if voice_name:
    toks = voice.GetVoices()
    for i in range(toks.Count):
        if voice_name.lower() in toks.Item(i).GetDescription().lower():
            voice.Voice = toks.Item(i); break
voice.Rate = max(-10, min(10, round((wpm - 180) / 18)))
fmt = comtypes.client.CreateObject('SAPI.SpAudioFormat'); fmt.Type = 39
stream = comtypes.client.CreateObject('SAPI.SpFileStream'); stream.Format = fmt
stream.Open(out, 3)
voice.AudioOutputStream = stream
voice.Speak(text)
stream.Close()
"""

# Other platforms: pyttsx3 (espeak / NSSpeechSynthesizer)
_CLI = r"""
import sys, pyttsx3
text, out, voice, rate = sys.argv[1], sys.argv[2], sys.argv[3], int(sys.argv[4])
e = pyttsx3.init()
if voice:
    for v in e.getProperty('voices'):
        if voice.lower() in (v.name or '').lower() or voice == v.id:
            e.setProperty('voice', v.id); break
e.setProperty('rate', rate)
e.save_to_file(text, out)
e.runAndWait()
"""


def wav_duration(path: Path) -> float:
    try:
        with wave.open(str(path), "rb") as w:
            return w.getnframes() / float(w.getframerate() or 1)
    except Exception:
        from app.services.video.ffmpeg import probe

        return probe(path).duration


def synthesize(text: str, out: Path, cfg: dict) -> Path | None:
    text = " ".join((text or "").split())
    if not text or not cfg.get("enabled", True):
        return None
    out = Path(out)
    engine = cfg.get("engine", "sapi")
    try:
        if engine == "piper" and cfg.get("piper_path") and cfg.get("piper_model"):
            proc = subprocess.run([cfg["piper_path"], "--model", cfg["piper_model"], "--output_file", str(out)],
                                  input=text.encode("utf-8"), capture_output=True, timeout=120)
            if proc.returncode != 0:
                raise RuntimeError(proc.stderr.decode("utf-8", "replace")[-300:])
        else:
            code = _SAPI_CLI if sys.platform == "win32" else _CLI
            proc = subprocess.run([sys.executable, "-c", code, text, str(out), cfg.get("voice_name", ""), str(int(cfg.get("rate", 178)))],
                                  capture_output=True, timeout=120)
            if proc.returncode != 0:
                raise RuntimeError(proc.stderr.decode("utf-8", "replace")[-300:])
        if out.exists() and out.stat().st_size > 1000 and wav_duration(out) > 0.2:
            return out
        raise RuntimeError("TTS produced no audio")
    except Exception as exc:
        log.warning("TTS unavailable (%s): %s", engine, str(exc)[:200])
        return None


def list_voices() -> list[dict]:
    if sys.platform == "win32":
        code = ("import comtypes.client,json;v=comtypes.client.CreateObject('SAPI.SpVoice');t=v.GetVoices();"
                "print(json.dumps([{'id':t.Item(i).Id,'name':t.Item(i).GetDescription()} for i in range(t.Count)]))")
    else:
        code = "import pyttsx3,json;e=pyttsx3.init();print(json.dumps([{'id':v.id,'name':v.name} for v in e.getProperty('voices')]))"
    try:
        proc = subprocess.run([sys.executable, "-c", code], capture_output=True, timeout=30)
        import json

        return json.loads(proc.stdout.decode("utf-8", "replace") or "[]")
    except Exception:
        return []
