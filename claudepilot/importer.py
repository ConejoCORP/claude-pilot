"""Retour à partir d'un fichier audio ou vidéo : transcription, images aux mots déclencheurs, comme en direct.

Le décodage passe par PyAV (FFmpeg embarqué), déjà installé avec faster-whisper : mp3, m4a, wav, mp4, mkv, mov…
"""
from __future__ import annotations

import logging
from pathlib import Path
from typing import Callable

import av
import numpy as np
import soundfile as sf

from . import triggers, vad
from .captures import VoiceCaptures
from .config import Config
from .engines import Engine
from .model import Capture
from .screen import Frame, Monitor, signature
from .session import Session
from .transcriber import SR, transcribe_segments

log = logging.getLogger(__name__)

MEDIA_EXTENSIONS = (
    ".mp3", ".m4a", ".wav", ".flac", ".ogg", ".opus", ".aac", ".wma",
    ".mp4", ".mkv", ".mov", ".webm", ".avi", ".m4v", ".wmv",
)
AUTO_EVERY = 30.0  # vidéo sans mot déclencheur : une image toutes les 30 s de parole environ…
AUTO_MAX = 40  # …et pas plus de 40 au total


def is_media(path: Path) -> bool:
    return path.suffix.lower() in MEDIA_EXTENSIONS


def decode_audio(path: Path) -> tuple[np.ndarray, float]:
    """Piste son en int16 16 kHz mono, et le moment où elle commence dans le fichier (en secondes)."""
    with av.open(str(path)) as container:
        if not container.streams.audio:
            raise ValueError("ce fichier n'a pas de piste son")
        stream = container.streams.audio[0]
        start = float(stream.start_time * stream.time_base) if stream.start_time is not None else 0.0
        resampler = av.AudioResampler(format="s16", layout="mono", rate=SR)
        chunks = []
        for frame in container.decode(stream):
            chunks.extend(out.to_ndarray()[0] for out in resampler.resample(frame))
        chunks.extend(out.to_ndarray()[0] for out in resampler.resample(None))
    pcm = np.concatenate(chunks).astype(np.int16) if chunks else np.zeros(0, np.int16)
    return pcm, start


def has_video(path: Path) -> bool:
    """Vraie piste vidéo (la pochette d'un mp3 n'en est pas une)."""
    with av.open(str(path)) as container:
        return any(not s.disposition & av.stream.Disposition.attached_pic for s in container.streams.video)


class VideoFrames:
    """Images de la vidéo à un instant donné : le pendant de la boîte noire pour un fichier importé."""

    def __init__(self, path: Path, offset: float):
        self.container = av.open(str(path))
        self.stream = next(s for s in self.container.streams.video
                           if not s.disposition & av.stream.Disposition.attached_pic)
        self.stream.thread_type = "AUTO"
        self.offset = offset  # début de la piste son : le temps 0 de la transcription
        self.monitor = Monitor(0, 0, 0, 0, self.stream.codec_context.width, self.stream.codec_context.height)

    def frame_at(self, t: float) -> Frame | None:
        """L'image affichée au temps t de la transcription."""
        stream = self.stream
        self.container.seek(max(0, int((t + self.offset) / stream.time_base)), stream=stream, backward=True)
        chosen = None
        for frame in self.container.decode(stream):
            if frame.time is None:
                continue
            if chosen is not None and frame.time - self.offset > t:
                break
            chosen = frame
        if chosen is None:
            return None
        image = chosen.to_image()
        return Frame(max(0.0, chosen.time - self.offset), self.monitor, None, img=image, sig=signature(image))

    def close(self) -> None:
        self.container.close()


def import_media(path: Path, root: Path, engine: Engine, cfg: Config, progress: Callable[[str], None]) -> Session:
    """Crée un retour à partir du fichier ; il ne reste qu'à l'exporter."""
    progress(f"Lecture de {path.name}…")
    pcm, offset = decode_audio(path)
    if len(pcm) < SR // 2:
        raise ValueError("ce fichier ne contient pas de son")
    session = Session.create(root)
    session.set_source(path.name)
    sf.write(str(session.audio_path), pcm, SR, subtype="PCM_16", format="FLAC")
    progress("Recherche des phrases…")
    segments = vad.segment(pcm)
    utterances, _ = transcribe_segments(engine, pcm, segments, list(cfg.lexicon),
                                        lambda i, n: progress(f"Transcription… {i * 100 // max(n, 1)} %"))
    for utt in utterances:
        session.add_utterance(utt)
    if has_video(path):
        progress("Images de la vidéo…")
        add_video_captures(session, VideoFrames(path, offset), cfg)
    session.finish(len(pcm) / SR, engine.label)
    return session


def add_video_captures(session: Session, frames: VideoFrames, cfg: Config) -> None:
    """Images aux mots déclencheurs, avec les règles du direct ; sinon une image toutes les 30 s environ."""

    def store(frame: Frame, source: str, trigger: str) -> str:
        name = f"{len(session.captures) + 1:03d}_{'voix' if source == 'voix' else 'auto'}_{frame.t:07.2f}s.png"
        image = frame.image()
        image.save(session.raw_dir / name, "PNG", compress_level=1)
        session.add_capture(Capture(round(frame.t, 3), source, name, 0, image.size, None, trigger))
        return name

    try:
        voice = VoiceCaptures(session, frames.frame_at, store)
        for utt in session.utterances:
            for action in triggers.detect(utt.words, cfg.triggers, cfg.triggers_kept, cfg.voice_commands):
                if action.kind == "capture":
                    voice.capture(action)
                elif action.kind == "cancel":
                    session.cancel_last_capture(action.t)
        if any(not c.cancelled for c in session.captures):
            return
        every = max(AUTO_EVERY, (session.utterances[-1].end if session.utterances else 0) / AUTO_MAX)
        due, previous = 0.0, None
        for utt in session.utterances:
            if not utt.words or utt.start < due:
                continue
            frame = frames.frame_at(utt.words[0].start)
            if frame is not None and (previous is None or not previous.same_view(frame)):
                store(frame, "vidéo", "")
                previous = frame
            due = utt.start + every
    finally:
        frames.close()
