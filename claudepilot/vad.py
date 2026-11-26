"""Détecteur de voix en continu (Silero) : découpe le flux micro en phrases à chaque pause."""
from __future__ import annotations

from pathlib import Path

import numpy as np

SR = 16000
CHUNK = 512  # 32 ms
CONTEXT = 64


def _model_path() -> Path:
    from importlib.util import find_spec  # sans importer faster-whisper, lourd : la dictée reste légère

    return Path(find_spec("faster_whisper").submodule_search_locations[0]) / "assets" / "silero_vad_v6.onnx"


class StreamingVAD:
    """Reçoit l'audio au fil de l'eau et renvoie (début, fin) en échantillons de chaque phrase terminée."""

    def __init__(
        self,
        threshold: float = 0.5,
        neg_threshold: float = 0.35,
        min_silence_ms: int = 600,
        speech_pad_ms: int = 200,
        min_speech_ms: int = 250,
        max_utterance_s: float = 25.0,
    ):
        import onnxruntime as ort

        opts = ort.SessionOptions()
        opts.inter_op_num_threads = 1
        opts.intra_op_num_threads = 1
        opts.log_severity_level = 3
        self._session = ort.InferenceSession(str(_model_path()), sess_options=opts, providers=["CPUExecutionProvider"])
        self.threshold = threshold
        self.neg_threshold = neg_threshold
        self.min_silence = SR * min_silence_ms // 1000
        self.pad = SR * speech_pad_ms // 1000
        self.min_speech = SR * min_speech_ms // 1000
        self.max_len = int(SR * max_utterance_s)
        self._reset_model()
        self._pending = np.zeros(0, dtype=np.float32)
        self._pos = 0  # position absolue du début de _pending
        self._triggered = False
        self._start = 0
        self._temp_end: int | None = None
        self._last_end = 0
        self._cut = (1.0, 0)  # (probabilité la plus basse, position) pour couper une phrase trop longue
        self.last_prob = 0.0

    def _reset_model(self) -> None:
        self._h = np.zeros((1, 1, 128), dtype=np.float32)
        self._c = np.zeros((1, 1, 128), dtype=np.float32)
        self._context = np.zeros(CONTEXT, dtype=np.float32)

    def _prob(self, chunk: np.ndarray) -> float:
        x = np.concatenate([self._context, chunk])[None, :]
        out, self._h, self._c = self._session.run(None, {"input": x, "h": self._h, "c": self._c})
        self._context = chunk[-CONTEXT:]
        return float(np.asarray(out).reshape(-1)[0])

    def feed(self, pcm: np.ndarray, start: int) -> list[tuple[int, int]]:
        """pcm : int16 ou float32 ; start : position absolue de son premier échantillon."""
        samples = pcm.astype(np.float32) / 32768.0 if pcm.dtype == np.int16 else pcm.astype(np.float32)
        expected = self._pos + len(self._pending)
        if start > expected:  # trou (pause) : on repart proprement
            self._pending = np.zeros(0, dtype=np.float32)
            self._pos = start
        self._pending = np.concatenate([self._pending, samples])
        segments: list[tuple[int, int]] = []
        while len(self._pending) >= CHUNK:
            chunk, self._pending = self._pending[:CHUNK], self._pending[CHUNK:]
            prob = self._prob(chunk)
            self.last_prob = prob
            begin, end = self._pos, self._pos + CHUNK
            self._pos = end
            segments.extend(self._step(prob, begin, end))
        return segments

    def flush(self, end: int) -> list[tuple[int, int]]:
        """Clôt la phrase en cours (pause ou arrêt de l'enregistrement)."""
        segments = []
        if self._triggered and end - self._start >= self.min_speech:
            segments.append((self._start, end))
        self._triggered = False
        self._temp_end = None
        self._last_end = end
        self._pending = np.zeros(0, dtype=np.float32)
        self._pos = end
        self._reset_model()
        return segments

    def _step(self, prob: float, begin: int, end: int) -> list[tuple[int, int]]:
        if not self._triggered:
            if prob >= self.threshold:
                self._triggered = True
                self._start = max(self._last_end, begin - self.pad)
                self._temp_end = None
                self._cut = (1.0, end)
            return []

        out: list[tuple[int, int]] = []
        if prob < self.neg_threshold:
            if self._temp_end is None:
                self._temp_end = begin
            if end - self._temp_end >= self.min_silence:
                stop = min(self._temp_end + self.pad, end)
                if self._temp_end - self._start >= self.min_speech:
                    out.append((self._start, stop))
                self._last_end = stop
                self._triggered = False
                return out
        elif prob >= self.threshold:
            self._temp_end = None

        if end - self._start >= self.max_len // 2 and prob < self._cut[0]:
            self._cut = (prob, end)
        if end - self._start >= self.max_len:
            cut = self._cut[1] if self._cut[1] > self._start + self.max_len // 2 else end
            out.append((self._start, cut))
            self._start = self._last_end = cut
            self._temp_end = None
            self._cut = (1.0, end)
        return out


def segment(pcm: np.ndarray) -> list[tuple[int, int]]:
    """Découpe un enregistrement complet (retranscription, récupération après plantage)."""
    vad = StreamingVAD()
    segments = vad.feed(pcm, 0)
    segments.extend(vad.flush(len(pcm)))
    return segments
