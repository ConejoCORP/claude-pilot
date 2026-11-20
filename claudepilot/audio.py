"""Enregistrement du micro en 16 kHz mono : écriture continue sur disque et horloge unique de la session."""
from __future__ import annotations

import logging
import queue
import threading
import time
from pathlib import Path
from typing import Callable

import numpy as np
import sounddevice as sd
import soundfile as sf

log = logging.getLogger(__name__)

SR = 16000
_BOUNDARY = object()  # pause ou arrêt : la phrase en cours doit être close


def input_devices() -> list[str]:
    """Micros disponibles, sur l'API audio par défaut de Windows."""
    hostapi = sd.default.hostapi
    names: list[str] = []
    for device in sd.query_devices():
        if device["max_input_channels"] > 0 and device["hostapi"] == hostapi and device["name"] not in names:
            names.append(device["name"])
    return names


def default_input() -> str:
    """Nom du micro par défaut de Windows ("" s'il n'y en a pas)."""
    try:
        return sd.query_devices(kind="input")["name"]
    except (sd.PortAudioError, ValueError):
        return ""


def refresh_devices() -> None:
    """Relit les micros : PortAudio garde la liste du lancement, même si Windows change de micro par défaut entre-temps.
    Aucun flux audio ne doit être ouvert."""
    sd._terminate()
    sd._initialize()


def microphone_problem(chosen: str, last: str, default: str, names: list[str]) -> str | None:
    """« missing » : le micro choisi est débranché. « changed » : réglé sur le micro par défaut, mais Windows a changé
    de micro depuis le dernier retour où l'on a entendu quelqu'un (une carte d'acquisition rebranchée, par exemple)."""
    if chosen:
        return "missing" if chosen not in names else None
    if last and default and default != last:
        return "changed"
    return None


def _find_device(name: str) -> int | None:
    if not name:
        return None
    devices = list(enumerate(sd.query_devices()))
    for i, device in devices:
        if device["max_input_channels"] > 0 and device["hostapi"] == sd.default.hostapi and device["name"] == name:
            return i
    for i, device in devices:
        if device["max_input_channels"] > 0 and name.lower() in device["name"].lower():
            return i
    log.warning("Micro « %s » introuvable : micro par défaut utilisé", name)
    return None


class Recorder:
    """Le nombre d'échantillons enregistrés sert d'horloge à toute la session : aucune dérive possible."""

    def __init__(
        self,
        device: str = "",
        on_audio: Callable[[np.ndarray, int], None] | None = None,
        on_boundary: Callable[[int], None] | None = None,
    ):
        self.device = device
        self.on_audio = on_audio
        self.on_boundary = on_boundary
        self.level_db = -90.0
        self.overflows = 0
        self._q: queue.Queue = queue.Queue()
        self._buf = np.zeros(SR * 120, dtype=np.int16)
        self._n = 0
        self._buf_lock = threading.Lock()
        self._clock_lock = threading.Lock()
        self._anchor_perf: float | None = None
        self._anchor_frames = 0
        self._cb_frames = 0
        self._rate = SR
        self._latency = 0.0
        self._paused = False
        self._resampler = None
        self._stream: sd.InputStream | None = None
        self._file: sf.SoundFile | None = None
        self._thread: threading.Thread | None = None

    def start(self, path: Path) -> None:
        self._stream = self._open(_find_device(self.device))
        self._latency = float(self._stream.latency)
        self._file = sf.SoundFile(str(path), "w", samplerate=SR, channels=1, subtype="PCM_16", format="FLAC")
        self._thread = threading.Thread(target=self._run, name="audio", daemon=True)
        self._thread.start()
        self._stream.start()

    def _open(self, device: int | None) -> sd.InputStream:
        try:
            stream = sd.InputStream(
                samplerate=SR, channels=1, dtype="float32", device=device, blocksize=512, callback=self._callback
            )
            self._rate, self._resampler = SR, None
            return stream
        except sd.PortAudioError as exc:
            import soxr

            rate = int(sd.query_devices(device, "input")["default_samplerate"])
            log.info("16 kHz refusé par le micro (%s) : capture à %d Hz puis conversion", exc, rate)
            self._rate = rate
            self._resampler = soxr.ResampleStream(rate, SR, 1, dtype="float32")
            return sd.InputStream(
                samplerate=rate, channels=1, dtype="float32", device=device,
                blocksize=int(rate * 0.032), callback=self._callback,
            )

    def _callback(self, indata, frames, _time_info, status) -> None:
        if status:
            self.overflows += 1
        if self._paused:
            return
        now = time.perf_counter()
        block = indata[:, 0].copy()
        with self._clock_lock:
            self._cb_frames += frames
            self._anchor_perf, self._anchor_frames = now, self._cb_frames
        if frames:
            rms = float(np.sqrt(np.mean(block * block)))
            self.level_db = 20.0 * np.log10(max(rms, 1e-5))
        self._q.put(block)

    def now(self) -> float:
        """Moment présent sur l'horloge de la session, en secondes d'audio."""
        with self._clock_lock:
            if self._anchor_perf is None:
                return 0.0
            base = self._anchor_frames / self._rate
            if self._paused:
                return base
            return base + min(time.perf_counter() - self._anchor_perf, 1.0) + self._latency

    @property
    def paused(self) -> bool:
        return self._paused

    def pause(self) -> None:
        self._paused = True
        self._q.put(_BOUNDARY)

    def resume(self) -> None:
        self._paused = False

    def stop(self) -> float:
        if self._stream is not None:
            self._stream.stop()
            self._stream.close()
        self._q.put(_BOUNDARY)
        self._q.put(None)
        if self._thread is not None:
            self._thread.join(15)
        return self.duration

    @property
    def duration(self) -> float:
        return self._n / SR

    @property
    def samples(self) -> int:
        return self._n

    def get(self, start: int, end: int) -> np.ndarray:
        """Audio de la session entre deux positions, en float32."""
        with self._buf_lock:
            end = min(end, self._n)
            return self._buf[max(0, start):end].astype(np.float32) / 32768.0

    def _append(self, pcm: np.ndarray) -> int:
        with self._buf_lock:
            start, end = self._n, self._n + len(pcm)
            if end > len(self._buf):
                grown = np.zeros(max(end, len(self._buf) * 2), dtype=np.int16)
                grown[:start] = self._buf[:start]
                self._buf = grown
            self._buf[start:end] = pcm
            self._n = end
            return start

    def _run(self) -> None:
        try:
            while True:
                item = self._q.get()
                if item is None:
                    break
                if item is _BOUNDARY:
                    self._notify(self.on_boundary, self._n)
                    continue
                block = item if self._resampler is None else self._resampler.resample_chunk(item)
                if not len(block):
                    continue
                pcm = np.clip(np.round(block * 32767.0), -32768, 32767).astype(np.int16)
                start = self._append(pcm)
                try:
                    self._file.write(pcm)
                except Exception:
                    log.exception("Écriture audio impossible")
                self._notify(self.on_audio, pcm, start)
        finally:
            if self._file is not None:
                self._file.close()

    @staticmethod
    def _notify(callback, *args) -> None:
        if callback is None:
            return
        try:
            callback(*args)
        except Exception:
            log.exception("Erreur dans le traitement audio")


class MicMonitor:
    """Écoute le micro sans rien enregistrer, juste pour afficher son niveau (bouton « Tester »)."""

    def __init__(self, device: str = ""):
        self.level_db = -90.0
        index = _find_device(device)
        try:
            self._stream = sd.InputStream(
                samplerate=SR, channels=1, dtype="float32", device=index, blocksize=1024, callback=self._callback
            )
        except sd.PortAudioError:
            rate = int(sd.query_devices(index, "input")["default_samplerate"])
            self._stream = sd.InputStream(
                samplerate=rate, channels=1, dtype="float32", device=index, blocksize=2048, callback=self._callback
            )
        self._stream.start()

    def _callback(self, indata, frames, _time_info, _status) -> None:
        if frames:
            rms = float(np.sqrt(np.mean(indata[:, 0] * indata[:, 0])))
            self.level_db = 20.0 * np.log10(max(rms, 1e-5))

    def close(self) -> None:
        self._stream.stop()
        self._stream.close()
