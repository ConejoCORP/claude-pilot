"""Une session d'enregistrement : son dossier, son journal (reprise après plantage) et son état complet."""
from __future__ import annotations

import json
import logging
import threading
from dataclasses import asdict, fields
from datetime import datetime
from pathlib import Path

from .model import Capture, Utterance, Word

log = logging.getLogger(__name__)

JOURNAL = "session.jsonl"
STATE = "session.json"
AUDIO = "audio.flac"


class Session:
    def __init__(self, folder: Path, started: datetime):
        self.folder = folder
        self.started = started
        self.captures: list[Capture] = []
        self.utterances: list[Utterance] = []
        self.duration = 0.0
        self.engine_label = ""
        self.source = ""  # nom du fichier audio ou vidéo importé ; vide pour un enregistrement
        self._lock = threading.Lock()

    @classmethod
    def create(cls, root: Path) -> Session:
        started = datetime.now()
        base = started.strftime("%Y-%m-%d_%H-%M-%S")
        folder, n = root / base, 2
        while folder.exists():
            folder, n = root / f"{base}_{n}", n + 1
        (folder / "raw").mkdir(parents=True)
        session = cls(folder, started)
        session.log("start", started=started.isoformat(timespec="seconds"))
        return session

    @property
    def raw_dir(self) -> Path:
        return self.folder / "raw"

    @property
    def audio_path(self) -> Path:
        return self.folder / AUDIO

    def log(self, event: str, **data) -> None:
        line = json.dumps({"event": event, **data}, ensure_ascii=False)
        with self._lock, open(self.folder / JOURNAL, "a", encoding="utf-8") as f:
            f.write(line + "\n")

    def set_source(self, name: str) -> None:
        self.source = name
        self.log("source", name=name)

    def add_capture(self, cap: Capture) -> None:
        self.captures.append(cap)
        self.log("capture", **asdict(cap))

    def cancel_last_capture(self, before: float) -> Capture | None:
        """Annule la capture la plus récente prise avant ce moment."""
        candidates = [c for c in self.captures if not c.cancelled and c.t <= before + 0.5]
        if not candidates:
            return None
        cap = max(candidates, key=lambda c: c.t)
        self.cancel(cap)
        return cap

    def cancel(self, cap: Capture) -> None:
        cap.cancelled = True
        self.log("cancel", raw_file=cap.raw_file)

    def add_utterance(self, utt: Utterance) -> None:
        self.utterances.append(utt)
        self.utterances.sort(key=lambda u: u.start)
        self.log("utterance", **asdict(utt))

    def replace_utterances(self, utterances: list[Utterance], engine_label: str) -> None:
        self.utterances = sorted(utterances, key=lambda u: u.start)
        self.engine_label = engine_label
        self.log("retranscription", engine=engine_label)

    def finish(self, duration: float, engine_label: str) -> None:
        self.duration = duration
        self.engine_label = engine_label
        self.log("stop", duration=duration, engine=engine_label)

    def save(self) -> None:
        data = {
            "started": self.started.isoformat(timespec="seconds"),
            "duration": self.duration,
            "engine": self.engine_label,
            "source": self.source,
            "captures": [asdict(c) for c in self.captures],
            "utterances": [asdict(u) for u in self.utterances],
        }
        tmp = self.folder / (STATE + ".tmp")
        tmp.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
        tmp.replace(self.folder / STATE)

    @classmethod
    def load(cls, folder: Path) -> Session:
        """Recharge une session : état complet si présent, sinon rejoue le journal (après un plantage)."""
        state = folder / STATE
        if state.exists():
            data = json.loads(state.read_text(encoding="utf-8"))
            session = cls(folder, datetime.fromisoformat(data["started"]))
            session.duration = data.get("duration", 0.0)
            session.engine_label = data.get("engine", "")
            session.source = data.get("source", "")
            session.captures = [_capture(d) for d in data.get("captures", [])]
            session.utterances = [_utterance(d) for d in data.get("utterances", [])]
        else:
            session = cls(folder, datetime.fromtimestamp(folder.stat().st_mtime))
            for line in (folder / JOURNAL).read_text(encoding="utf-8").splitlines():
                try:
                    event = json.loads(line)
                except ValueError:
                    continue  # dernière ligne coupée par un plantage
                kind = event.pop("event", "")
                if kind == "start":
                    session.started = datetime.fromisoformat(event["started"])
                elif kind == "source":
                    session.source = event.get("name", "")
                elif kind == "capture":
                    session.captures.append(_capture(event))
                elif kind == "cancel":
                    for cap in session.captures:
                        if cap.raw_file == event.get("raw_file"):
                            cap.cancelled = True
                elif kind == "utterance":
                    session.utterances.append(_utterance(event))
                elif kind == "stop":
                    session.duration = event.get("duration", 0.0)
                    session.engine_label = event.get("engine", "")
            session.utterances.sort(key=lambda u: u.start)
        if not session.duration and session.audio_path.exists():
            try:
                import soundfile as sf

                session.duration = sf.info(str(session.audio_path)).duration
            except Exception:
                log.warning("Durée illisible pour %s", session.audio_path)
        return session


def _capture(data: dict) -> Capture:
    known = {f.name for f in fields(Capture)}
    values = {k: v for k, v in data.items() if k in known}
    values["size"] = tuple(values.get("size") or (0, 0))
    values["cursor"] = tuple(values["cursor"]) if values.get("cursor") else None
    return Capture(**values)


def _utterance(data: dict) -> Utterance:
    return Utterance(
        start=data["start"],
        end=data["end"],
        words=[Word(w["text"], w["start"], w["end"]) for w in data.get("words", [])],
        engine=data.get("engine", ""),
        compute_s=data.get("compute_s", 0.0),
    )


def unfinished_sessions(root: Path, exclude: Path | None = None) -> list[Path]:
    """Sessions interrompues (journal présent, retour jamais généré)."""
    if not root.exists():
        return []
    result = []
    for folder in sorted(root.iterdir()):
        if folder == exclude or not folder.is_dir():
            continue
        if (folder / JOURNAL).exists() and not (folder / "retour.md").exists():
            result.append(folder)
    return result
