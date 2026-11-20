"""Simulation de bout en bout sans micro ni écran réel.

L'audio de test est envoyé dans le vrai contrôleur de l'appli (détection de voix, transcription,
déclencheurs vocaux, capture au raccourci, export). Les « écrans » sont des images de synthèse :
rien n'est capturé sur l'écran réel. Usage : .venv\\Scripts\\python.exe tools\\simulate_session.py [moteur]
"""
from __future__ import annotations

import os
import shutil
import sys
import threading
import time
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import numpy as np  # noqa: E402
import soundfile as sf  # noqa: E402
from PIL import Image, ImageDraw, ImageFont  # noqa: E402

from claudepilot import app as app_module  # noqa: E402
from claudepilot.config import DATA_DIR  # noqa: E402
from claudepilot.screen import Frame, Monitor, signature  # noqa: E402

SR = 16000
SPEED = 4.0  # audio envoyé 4 fois plus vite que le temps réel
HOTKEY_AT = 33.0  # « appui sur le raccourci » pendant « Je regarde aussi le fichier… »
MONITOR = Monitor(1, 2, 0, 0, 2560, 1440)


class FakeRecorder:
    """Même interface que audio.Recorder, alimentée par un fichier WAV."""

    AUDIO = ROOT / "tests" / "data" / "tts_fr.wav"

    def __init__(self, device: str = "", on_audio=None, on_boundary=None):
        self.on_audio, self.on_boundary = on_audio, on_boundary
        self.level_db = -20.0
        pcm, _ = sf.read(self.AUDIO, dtype="int16")
        self._pcm = np.concatenate([pcm, np.zeros(SR, dtype=np.int16)])
        self._n = 0
        self._paused = False
        self._done = threading.Event()
        self._thread = threading.Thread(target=self._stream, daemon=True)

    def start(self, path: Path) -> None:
        self._file = sf.SoundFile(str(path), "w", samplerate=SR, channels=1, subtype="PCM_16", format="FLAC")
        self._thread.start()

    def _stream(self) -> None:
        for start in range(0, len(self._pcm), 512):
            block = self._pcm[start:start + 512]
            self._file.write(block)
            self._n = start + len(block)
            self.on_audio(block, start)
            time.sleep(len(block) / SR / SPEED)
        self._done.set()

    def wait_end(self) -> None:
        self._done.wait()

    def now(self) -> float:
        return self._n / SR

    def get(self, start: int, end: int) -> np.ndarray:
        return self._pcm[start:min(end, self._n)].astype(np.float32) / 32768.0

    def pause(self) -> None: ...
    def resume(self) -> None: ...

    @property
    def duration(self) -> float:
        return self._n / SR

    def stop(self) -> float:
        self._done.wait()
        self.on_boundary(self._n)
        self._file.close()
        return self.duration


def _font(size: int, bold: bool = False):
    try:
        return ImageFont.truetype("segoeuib.ttf" if bold else "segoeui.ttf", size)
    except OSError:
        return ImageFont.load_default(size=size)


def synthetic_screen(t: float) -> Image.Image:
    """Fausse appli à corriger : le bouton « Valider » est collé au bord, puis une erreur rouge apparaît."""
    img = Image.new("RGB", (MONITOR.width, MONITOR.height), (241, 243, 246))
    draw = ImageDraw.Draw(img)
    draw.rectangle((0, 0, MONITOR.width, 64), fill=(32, 33, 36))
    draw.text((28, 14), "Mon appli — Inscription", font=_font(28), fill=(235, 235, 235))
    draw.rounded_rectangle((760, 200, 1800, 1060), radius=24, fill=(255, 255, 255), outline=(222, 226, 232), width=2)
    draw.text((840, 260), "Créer un compte", font=_font(56, bold=True), fill=(17, 24, 39))
    for i, label in enumerate(("Nom", "Adresse e-mail", "Mot de passe")):
        y = 410 + i * 170
        draw.text((840, y), label, font=_font(30), fill=(75, 85, 99))
        draw.rounded_rectangle((840, y + 48, 1720, y + 118), radius=12, fill=(249, 250, 251),
                               outline=(209, 213, 219), width=3)
    draw.rounded_rectangle((2250, 900, 2540, 990), radius=14, fill=(37, 99, 235))
    draw.text((2318, 916), "Valider", font=_font(40, bold=True), fill=(255, 255, 255))
    if t >= 15:  # « la console affiche une erreur rouge »
        draw.rounded_rectangle((60, 1230, 1560, 1390), radius=14, fill=(254, 226, 226), outline=(239, 68, 68), width=3)
        draw.text((100, 1256), "Erreur : impossible d'enregistrer le formulaire", font=_font(38, bold=True),
                  fill=(185, 28, 28))
        draw.text((100, 1316), "TypeError: cannot read properties of undefined (reading 'submit')", font=_font(28),
                  fill=(153, 27, 27))
    return img


class FakeScreen:
    """Même interface que screen.ScreenService ; images de synthèse au lieu de l'écran réel."""

    def __init__(self, clock, fps=2.0, seconds=60.0, avoid_rects=None):
        self.clock = clock

    def start(self) -> None: ...
    def stop(self) -> None: ...

    def _frame(self, t: float) -> Frame:
        img = synthetic_screen(t)
        return Frame(t, MONITOR, (2395, 945), img=img, sig=signature(img))  # la souris pointe le bouton « Valider »

    def capture(self, timeout: float = 3.0) -> Frame:
        return self._frame(self.clock())

    def frame_at(self, t: float) -> Frame:
        return self._frame(round(t * 2) / 2)  # comme une boîte noire à 2 images/s


class NoHotkeys:
    def register(self, *_args) -> bool:
        return True

    def unregister(self, *_args) -> None: ...
    def stop(self) -> None: ...


def main() -> None:
    from PySide6.QtWidgets import QApplication

    engine = sys.argv[1] if len(sys.argv) > 1 and sys.argv[1] != "-" else None
    hotkey = len(sys.argv) <= 2
    if not hotkey:
        FakeRecorder.AUDIO = Path(sys.argv[2])
    out_root = DATA_DIR / "simulation"
    shutil.rmtree(out_root, ignore_errors=True)
    app_module.Recorder = FakeRecorder
    app_module.ScreenService = FakeScreen

    qt = QApplication(sys.argv)
    ctrl = app_module.Controller(qt)
    ctrl.hotkeys = NoHotkeys()
    ctrl.cfg.retours_dir = str(out_root)
    ctrl.cfg.sounds = False
    ctrl.cfg.copy_prompt_on_stop = False
    ctrl.cfg.save = lambda: None  # ne touche pas aux vrais réglages
    if engine:
        ctrl.cfg.engine = engine
    ctrl.engines.get(ctrl.cfg.engine)  # moteur chargé avant de « parler »

    t0 = time.perf_counter()
    ctrl.start_recording()
    recorder = ctrl.recorder

    def script():
        if hotkey:
            while recorder.now() < HOTKEY_AT:
                time.sleep(0.01)
            ctrl.run(lambda: ctrl.capture("raccourci"))
        recorder.wait_end()
        ctrl.run(ctrl.stop_recording)

    threading.Thread(target=script, daemon=True).start()
    while ctrl.state != "idle" or ctrl.last_result is None:
        qt.processEvents()
        time.sleep(0.01)
        if time.perf_counter() - t0 > 300:
            raise SystemExit("Simulation bloquée")

    result = ctrl.last_result
    md = result.md_path.read_text(encoding="utf-8")
    print(md)
    print(f"--- {len(result.images)} capture(s), simulation en {time.perf_counter() - t0:.1f} s")
    print(f"--- message pour Claude Code : {result.prompt}")
    ctrl._png.shutdown(wait=True)


if __name__ == "__main__":
    main()
