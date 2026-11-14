"""Transcription phrase par phrase pendant l'enregistrement, et retranscription complète d'une session."""
from __future__ import annotations

import logging
import queue
import threading
import time
from pathlib import Path
from typing import Callable

import numpy as np

from . import vad
from .config import ENGINE_LABELS
from .engines import Engine, EngineManager
from .model import Utterance, Word, fmt_duration, fmt_time

log = logging.getLogger(__name__)

SR = 16000


class TranscriptionWorker(threading.Thread):
    """Transcrit chaque phrase dès que le détecteur de voix entend une pause : le texte ne bouge plus ensuite."""

    def __init__(
        self,
        engines: EngineManager,
        engine_key: Callable[[], str],
        audio: Callable[[int, int], np.ndarray],
        lexicon: Callable[[], list[str]],
        on_result: Callable[[Utterance], None],
    ):
        super().__init__(name="transcription", daemon=True)
        self.engines = engines
        self.engine_key = engine_key
        self.audio = audio
        self.lexicon = lexicon
        self.on_result = on_result
        self._q: queue.Queue = queue.Queue()
        self._pending = 0
        self._lock = threading.Lock()
        self._idle = threading.Event()
        self._idle.set()

    def submit(self, start: int, end: int) -> None:
        with self._lock:
            self._pending += 1
            self._idle.clear()
        self._q.put((start, end))

    def wait_idle(self, timeout: float | None = None) -> bool:
        return self._idle.wait(timeout)

    def stop(self) -> None:
        self._q.put(None)

    def run(self) -> None:
        while True:
            item = self._q.get()
            if item is None:
                return
            try:
                self.on_result(self._transcribe(*item))
            except Exception:
                log.exception("Phrase perdue")
            finally:
                with self._lock:
                    self._pending -= 1
                    if self._pending == 0:
                        self._idle.set()

    def _transcribe(self, start: int, end: int) -> Utterance:
        audio = self.audio(start, end)
        try:
            engine = self.engines.get(self.engine_key())
            t0 = time.perf_counter()
            words = engine.transcribe(audio, offset=start / SR, lexicon=self.lexicon())
            return Utterance(start / SR, end / SR, words, engine.label, time.perf_counter() - t0)
        except Exception as exc:
            log.exception("Transcription impossible de %s à %s", fmt_time(start / SR), fmt_time(end / SR))
            marker = Word("[passage non transcrit : relance « Retranscrire »]", start / SR, end / SR)
            return Utterance(start / SR, end / SR, [marker], f"erreur : {exc}")


def load_audio(path: Path) -> np.ndarray:
    """Audio d'une session en int16 16 kHz mono."""
    import soundfile as sf

    pcm, rate = sf.read(str(path), dtype="int16", always_2d=True)
    pcm = pcm[:, 0]
    if rate != SR:
        import soxr

        pcm = np.clip(soxr.resample(pcm.astype(np.float32), rate, SR), -32768, 32767).astype(np.int16)
    return pcm


def session_segments(session, pcm: np.ndarray) -> list[tuple[int, int]]:
    """Mêmes découpages que pendant l'enregistrement, ou nouveau découpage si la session a planté."""
    if session.utterances:
        return [(int(round(u.start * SR)), int(round(u.end * SR))) for u in session.utterances]
    return vad.segment(pcm)


def transcribe_segments(
    engine: Engine, pcm: np.ndarray, segments: list[tuple[int, int]], lexicon: list[str],
    progress: Callable[[int, int], None] | None = None,
) -> tuple[list[Utterance], float]:
    audio = pcm.astype(np.float32) / 32768.0
    utterances, total = [], 0.0
    for i, (start, end) in enumerate(segments):
        if progress is not None:
            progress(i, len(segments))
        t0 = time.perf_counter()
        words = engine.transcribe(audio[start:end], offset=start / SR, lexicon=lexicon)
        elapsed = time.perf_counter() - t0
        total += elapsed
        utterances.append(Utterance(start / SR, end / SR, words, engine.label, elapsed))
    return utterances, total


def retranscribe(session, engine: Engine, lexicon: list[str]) -> None:
    pcm = load_audio(session.audio_path)
    utterances, _ = transcribe_segments(engine, pcm, session_segments(session, pcm), lexicon)
    session.replace_utterances(utterances, engine.label)
    if not session.duration:
        session.duration = len(pcm) / SR


def compare_engines(
    session, engines: EngineManager, keys: list[str], lexicon: list[str], progress: Callable[[str], None]
) -> Path:
    """Fait retranscrire les mêmes phrases par chaque moteur et écrit comparaison.md dans la session."""
    pcm = load_audio(session.audio_path)
    segments = session_segments(session, pcm)
    duration = len(pcm) / SR
    results = []
    for key in keys:
        progress(f"Comparaison : {ENGINE_LABELS[key]}…")
        try:
            engine = engines.get(key)
            utterances, total = transcribe_segments(engine, pcm, segments, lexicon)
            results.append((engine.label, utterances, total, None))
        except Exception as exc:
            log.exception("Comparaison impossible avec %s", key)
            results.append((ENGINE_LABELS[key], None, 0.0, str(exc)))

    lines = [
        f"# Comparaison des moteurs — retour du {session.started:%d/%m/%Y à %H:%M}",
        "",
        f"{len(segments)} phrases, {fmt_duration(duration)} d'audio : chaque moteur retranscrit exactement les mêmes morceaux.",
        "",
        "| Moteur | Calcul total | Vitesse |",
        "|---|---|---|",
    ]
    for label, _utts, total, error in results:
        if error:
            lines.append(f"| {label} | échec : {error} | |")
        else:
            speed = f"{duration / total:.0f}× le temps réel" if total > 0 else "—"
            lines.append(f"| {label} | {total:.1f} s | {speed} |")
    lines.append("")
    for i, (start, end) in enumerate(segments):
        lines += [f"## Phrase {i + 1} · {fmt_time(start / SR)} → {fmt_time(end / SR)}", ""]
        for label, utterances, _total, error in results:
            if not error:
                lines.append(f"- **{label}** : {utterances[i].text or '*(rien)*'}")
        lines.append("")
    path = session.folder / "comparaison.md"
    path.write_text("\n".join(lines), encoding="utf-8")
    return path
