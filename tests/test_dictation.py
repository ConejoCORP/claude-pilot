"""Dictée partout : frappes envoyées, échanges avec le moteur, arrêt avant une mise à jour."""
import io
import json
import subprocess
import sys
import time
from pathlib import Path

import numpy as np

from claudepilot import installinfo, winapi
from claudepilot.dictation_worker import serve
from claudepilot.model import Word


def test_frappes_unicode_et_emoji():
    down, up = winapi.KEYEVENTF_UNICODE, winapi.KEYEVENTF_UNICODE | winapi.KEYEVENTF_KEYUP
    assert winapi.key_events("é") == [(0, 0xE9, down), (0, 0xE9, up)]
    assert [scan for _vk, scan, flags in winapi.key_events("😀") if flags == down] == [0xD83D, 0xDE00]
    assert winapi.key_events("\n") == [(winapi.VK_RETURN, 0, 0), (winapi.VK_RETURN, 0, winapi.KEYEVENTF_KEYUP)]


class FakeEngine:
    def transcribe(self, audio, offset=0.0, lexicon=None):
        n = len(audio) // 16000
        return [Word("Bonjour", 0, 0.4)] + [Word("encore.", 0.5, 0.9)] * (n - 1)


def test_echanges_avec_le_moteur():
    frames = b""
    for seconds in (1, 3):
        data = np.zeros(16000 * seconds, dtype=np.float32).tobytes()
        frames += len(data).to_bytes(4, "little") + data
    frames += (0).to_bytes(4, "little")
    replies = []
    serve(io.BytesIO(frames), lambda **m: replies.append(m), FakeEngine(), [])
    assert replies == [{"text": "Bonjour"}, {"text": "Bonjour encore. encore."}]


def test_arret_de_la_dictee_seulement_si_c_est_la_notre(tmp_path):
    pid_file = tmp_path / "pid"
    child = subprocess.Popen([sys.executable, "-c", f"import os, time; open(r'{pid_file}', 'w').write(str(os.getpid())); "
                                                    "time.sleep(60)"])
    try:
        for _ in range(100):
            if pid_file.exists() and pid_file.read_text():
                break
            time.sleep(0.05)
        pid = pid_file.read_text()
        data = tmp_path / "data"
        data.mkdir()
        (data / installinfo.DICTATION_PIDS[0]).write_text(pid)
        assert not installinfo.stop_dictation(data, tmp_path)  # pas lancé depuis ce dossier : on n'y touche pas
        (data / installinfo.DICTATION_PIDS[0]).write_text(pid)
        assert installinfo.stop_dictation(data, Path(sys.base_prefix))
        assert not (data / installinfo.DICTATION_PIDS[0]).exists()
        child.wait(10)
    finally:
        child.kill()


def test_reponses_du_moteur_en_json():
    line = json.dumps({"text": "Ça marche à 100 % 😀"}, ensure_ascii=False)
    assert json.loads(line)["text"] == "Ça marche à 100 % 😀"


def test_barre_de_dictee_deplacable(tmp_path, monkeypatch):
    import os
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtCore import QEvent, QPoint, QPointF, Qt
    from PySide6.QtGui import QGuiApplication, QMouseEvent
    from PySide6.QtWidgets import QApplication

    from claudepilot import dictation

    QApplication.instance() or QApplication([])
    monkeypatch.setattr(dictation.winapi, "no_activate", lambda hwnd: None)
    left = Qt.MouseButton.LeftButton

    def mouse(pill, kind, at: QPoint, buttons=left):
        return QMouseEvent(kind, QPointF(pill.mapFromGlobal(at)), QPointF(at), left, buttons,
                           Qt.KeyboardModifier.NoModifier)

    def drag(pill, by: QPoint):
        start = pill.geometry().center()
        pill.mousePressEvent(mouse(pill, QEvent.Type.MouseButtonPress, start))
        for step in (1, 2, 3):
            pill.mouseMoveEvent(mouse(pill, QEvent.Type.MouseMove, start + by * step / 3))
        pill.mouseReleaseEvent(mouse(pill, QEvent.Type.MouseButtonRelease, start + by, Qt.MouseButton.NoButton))

    spot = tmp_path / "dictee_position.json"
    area = QGuiApplication.primaryScreen().availableGeometry()
    pill = dictation.DictationPill(spot)
    pill.show_bar()
    assert pill.pos() == QPoint(area.x() + (area.width() - pill.width()) // 2, area.bottom() - pill.height() - 28)

    drag(pill, QPoint(0, 0))  # un simple clic ne la déplace pas
    assert pill.spot is None and not spot.exists()

    start = pill.pos()
    drag(pill, QPoint(-100, -300))
    moved = pill.pos()
    assert (moved - (start + QPoint(-100, -300))).manhattanLength() <= 2

    at = pill.geometry().center()  # lâchée plus loin que le dernier mouvement reçu : posée sous la souris
    pill.mousePressEvent(mouse(pill, QEvent.Type.MouseButtonPress, at))
    pill.mouseReleaseEvent(mouse(pill, QEvent.Type.MouseButtonRelease, at + QPoint(30, 20), Qt.MouseButton.NoButton))
    assert (pill.pos() - (moved + QPoint(30, 20))).manhattanLength() <= 2
    drag(pill, QPoint(-30, -20))
    moved = pill.pos()
    assert spot.exists() and pill.spot is not None
    pill.hide()
    pill.show_bar()
    assert (pill.pos() - moved).manhattanLength() <= 2  # elle revient là où on l'a posée

    again = dictation.DictationPill(spot)  # prochaine session : la place est relue
    assert (again.home(area) - moved).manhattanLength() <= 2

    drag(pill, QPoint(-5000, -5000))  # lâchée hors de l'écran : ramenée dans le coin
    assert pill.pos() == area.topLeft() and pill.spot == (0.0, 0.0)

    pill.mouseDoubleClickEvent(mouse(pill, QEvent.Type.MouseButtonDblClick, pill.geometry().center()))
    assert pill.spot is None and not spot.exists()
    assert pill.pos() == QPoint(area.x() + (area.width() - pill.width()) // 2, area.bottom() - pill.height() - 28)
    pill.hide()
