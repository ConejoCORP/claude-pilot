"""Structures de données partagées entre l'enregistrement, la transcription et l'export."""
from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class Word:
    text: str
    start: float
    end: float

    @property
    def mid(self) -> float:
        return (self.start + self.end) / 2


@dataclass
class Utterance:
    """Un morceau de parole entre deux pauses, transcrit une fois pour toutes."""

    start: float
    end: float
    words: list[Word] = field(default_factory=list)
    engine: str = ""
    compute_s: float = 0.0

    @property
    def text(self) -> str:
        return " ".join(w.text for w in self.words)


@dataclass
class Capture:
    t: float  # moment dans la session (secondes d'audio)
    source: str  # "raccourci", "bouton" ou "voix"
    raw_file: str  # image brute dans raw/
    monitor: int  # numéro d'écran Windows (\\.\DISPLAYn)
    size: tuple[int, int]  # taille de l'écran capturé, en pixels
    cursor: tuple[int, int] | None = None  # souris, en pixels dans l'image brute
    trigger: str = ""  # mot déclencheur pour une capture vocale
    cancelled: bool = False

    @property
    def where(self) -> str:
        """« écran 2 », ou « vidéo » pour une image tirée d'un fichier importé (écran 0)."""
        return f"écran {self.monitor}" if self.monitor else "vidéo"


def fmt_time(seconds: float) -> str:
    """0:12, 4:05 ou 1:02:03."""
    total = max(0, int(seconds))
    h, rem = divmod(total, 3600)
    m, s = divmod(rem, 60)
    return f"{h}:{m:02d}:{s:02d}" if h else f"{m}:{s:02d}"


def fmt_duration(seconds: float) -> str:
    """« 45 s », « 4 min 12 s » ou « 1 h 02 min »."""
    total = max(0, int(round(seconds)))
    h, rem = divmod(total, 3600)
    m, s = divmod(rem, 60)
    if h:
        return f"{h} h {m:02d} min"
    if m:
        return f"{m} min {s:02d} s"
    return f"{s} s"
