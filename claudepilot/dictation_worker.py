"""Moteur de la dictée, dans son propre processus : il se ferme après la dictée et rend toute la mémoire.

Échanges par les tuyaux standard, sans rien d'autre qui transite par la sortie :
  entrée : 4 octets (taille n, petit-boutiste) puis n octets d'audio float32 16 kHz ; n = 0 : fin.
  sortie : une ligne JSON par message, {"ready": moteur}, {"text": texte} ou {"error": message}.
"""
from __future__ import annotations

import json
import logging
import os
import sys
from typing import BinaryIO, Callable, TextIO

import numpy as np

log = logging.getLogger(__name__)


def _read_exact(stream: BinaryIO, size: int) -> bytes | None:
    data = b""
    while len(data) < size:
        chunk = stream.read(size - len(data))
        if not chunk:
            return None
        data += chunk
    return data


def serve(stream: BinaryIO, send: Callable[..., None], engine, lexicon: list[str]) -> None:
    """Transcrit chaque phrase reçue, dans l'ordre, jusqu'à la fin des envois."""
    while True:
        header = _read_exact(stream, 4)
        size = int.from_bytes(header, "little") if header else 0
        data = _read_exact(stream, size) if size else None
        if data is None:
            return
        try:
            words = engine.transcribe(np.frombuffer(data, dtype=np.float32), offset=0.0, lexicon=lexicon)
            send(text=" ".join(w.text for w in words).strip())
        except Exception as exc:
            log.exception("Phrase non transcrite")
            send(text="", error=str(exc))


def main() -> int:
    replies: TextIO = os.fdopen(os.dup(1), "w", encoding="utf-8", buffering=1)
    os.dup2(os.open(os.devnull, os.O_WRONLY), 1)  # ce que les librairies écrivent ne brouille pas les réponses
    sys.stdout = open(os.devnull, "w", encoding="utf-8")

    from .__main__ import setup_logging
    from .config import DATA_DIR, Config
    from .engines import EngineManager

    setup_logging("dictee_moteur.log")
    pid_file = DATA_DIR / "dictee_moteur.pid"
    pid_file.write_text(str(os.getpid()), encoding="utf-8")

    def send(**message) -> None:
        replies.write(json.dumps(message, ensure_ascii=False) + "\n")

    try:
        cfg = Config.load()
        try:
            engine = EngineManager(cfg.device).get(cfg.engine)
        except Exception as exc:
            log.exception("Moteur de la dictée indisponible")
            send(error=f"moteur indisponible : {exc}")
            return 1
        send(ready=engine.label)
        serve(sys.stdin.buffer, send, engine, list(cfg.lexicon))
        return 0
    finally:
        pid_file.unlink(missing_ok=True)


if __name__ == "__main__":
    sys.exit(main())
