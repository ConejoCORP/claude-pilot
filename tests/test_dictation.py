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
