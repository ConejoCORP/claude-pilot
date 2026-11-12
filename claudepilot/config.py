"""Réglages de l'application et emplacements des fichiers.

Deux dispositions possibles :
- copie de développement (le dépôt) : tout reste dans le dossier du projet ;
- installation (install.json à côté du dossier app\\) : le code est remplacé à chaque mise à jour, donc
  réglages, journaux et moteurs vivent à côté, et les retours dans Documents\\Claude Pilot\\retours.
"""
from __future__ import annotations

import ctypes
import json
import logging
from ctypes import wintypes
from dataclasses import asdict, dataclass, field, fields
from pathlib import Path

APP_DIR = Path(__file__).resolve().parent.parent
INSTALL_DIR = APP_DIR.parent if (APP_DIR.parent / "install.json").exists() else None
BASE_DIR = INSTALL_DIR or APP_DIR
DATA_DIR = BASE_DIR / "data"
LOG_DIR = DATA_DIR / "logs"
MODELS_DIR = BASE_DIR / "models"
CONFIG_PATH = DATA_DIR / "config.json"


def documents_dir() -> Path:
    """Dossier Documents de l'utilisateur, même s'il a été déplacé (OneDrive, autre disque…)."""

    class GUID(ctypes.Structure):
        _fields_ = [("Data1", wintypes.DWORD), ("Data2", wintypes.WORD), ("Data3", wintypes.WORD),
                    ("Data4", ctypes.c_ubyte * 8)]

    folder_documents = GUID(0xFDD39AD0, 0x238F, 0x46AF, (ctypes.c_ubyte * 8)(0xAD, 0xB4, 0x6C, 0x85, 0x48, 0x03, 0x69, 0xC7))
    path = ctypes.c_wchar_p()
    try:
        if ctypes.windll.shell32.SHGetKnownFolderPath(ctypes.byref(folder_documents), 0, None, ctypes.byref(path)) == 0:
            result = Path(path.value)
            ctypes.windll.ole32.CoTaskMemFree(path)
            return result
    except (AttributeError, OSError):
        pass
    return Path.home() / "Documents"


DEFAULT_RETOURS_DIR = documents_dir() / "Claude Pilot" / "retours" if INSTALL_DIR else APP_DIR / "retours"

ENGINE_LABELS = {
    "parakeet-v3": "Parakeet TDT v3",
    "whisper-large-v3": "Whisper large-v3",
    "whisper-large-v3-turbo": "Whisper large-v3 turbo",
}
# Banc d'essai du 29/10/2026 (RTX 5080) : Parakeet 0 % d'erreur et 69× le temps réel, sans mot inventé.
DEFAULT_ENGINE = "parakeet-v3"

DEFAULT_TRIGGERS = ["capture d'écran", "capture", "screenshot", "screen", "regarde", "tu vois", "ici"]
DEFAULT_KEPT = ["regarde", "tu vois", "ici"]  # mots du quotidien : ils restent dans le texte
# Listes par défaut des versions précédentes : remplacées par les nouvelles tant que l'utilisateur n'y a pas touché.
# (« là », ajouté en 1.0.2, est retiré en 1.0.6 : trop courant à l'oral, il capturait à tout bout de champ.)
_FORMER_DEFAULTS = {
    "triggers": [
        ["capture d'écran", "capture", "screenshot", "screen", "regarde"],
        ["capture d'écran", "capture", "screenshot", "screen", "regarde", "tu vois", "là", "ici"],
    ],
    "triggers_kept": [["regarde"], ["regarde", "tu vois", "là", "ici"]],
}

log = logging.getLogger(__name__)


@dataclass
class Config:
    engine: str = DEFAULT_ENGINE
    device: str = "auto"  # auto, cuda ou cpu
    microphone: str = ""  # vide = micro par défaut de Windows
    retours_dir: str = str(DEFAULT_RETOURS_DIR)
    hotkey_record: str = "Ctrl+Alt+Space"
    hotkey_capture: str = "F8"
    triggers: list[str] = field(default_factory=lambda: list(DEFAULT_TRIGGERS))
    triggers_kept: list[str] = field(default_factory=lambda: list(DEFAULT_KEPT))  # restent dans le texte
    voice_commands: bool = True  # « retire la capture », « point suivant »
    lexicon: list[str] = field(
        default_factory=lambda: ["Claude", "Claude Code", "Home Assistant", "OBS", "API", "JSON", "Python"]
    )
    export_max_px: int = 2000
    cursor_ring: bool = False  # cercle rouge autour de la souris sur les captures (l'IA peut le prendre pour l'appli)
    ring_fps: float = 2.0
    ring_seconds: int = 60
    copy_prompt_on_stop: bool = True
    sounds: bool = True
    sound_volume: int = 70  # en %
    sound_each_phrase: bool = True  # petit « plip » à chaque phrase transcrite
    check_updates: bool = True  # chercher une nouvelle version à chaque lancement
    skipped_version: str = ""  # version que l'utilisateur a choisi d'ignorer
    overlay_pos: list[int] | None = None

    @classmethod
    def load(cls) -> Config:
        cfg = cls()
        if CONFIG_PATH.exists():
            try:
                data = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
                known = {f.name for f in fields(cls)}
                for key, value in data.items():
                    if key in known and value not in _FORMER_DEFAULTS.get(key, []):
                        setattr(cfg, key, value)
            except (OSError, ValueError):
                log.exception("config.json illisible, réglages par défaut utilisés")
        if cfg.engine not in ENGINE_LABELS:
            cfg.engine = DEFAULT_ENGINE
        return cfg

    def save(self) -> None:
        DATA_DIR.mkdir(parents=True, exist_ok=True)
        tmp = CONFIG_PATH.with_suffix(".tmp")
        tmp.write_text(json.dumps(asdict(self), ensure_ascii=False, indent=2), encoding="utf-8")
        tmp.replace(CONFIG_PATH)

    @property
    def retours_path(self) -> Path:
        return Path(self.retours_dir)
