import json
import wave

import numpy as np
import pytest
from PIL import Image, ImageDraw

from claudepilot import config, sounds
from claudepilot.screen import Frame, Monitor, signature
from claudepilot.window import to_html
from claudepilot.winapi import MOD_ALT, MOD_CONTROL, MOD_SHIFT, parse_hotkey


def test_nouveaux_mots_pour_les_reglages_d_avant(tmp_path, monkeypatch):
    path = tmp_path / "config.json"
    monkeypatch.setattr(config, "CONFIG_PATH", path)
    old = {"triggers": ["capture d'écran", "capture", "screenshot", "screen", "regarde"],
           "triggers_kept": ["regarde"], "microphone": "Chat Mic"}
    path.write_text(json.dumps(old), encoding="utf-8")
    cfg = config.Config.load()
    assert "ici" in cfg.triggers and "tu vois" in cfg.triggers_kept and cfg.microphone == "Chat Mic"
    v102 = {"triggers": ["capture d'écran", "capture", "screenshot", "screen", "regarde", "tu vois", "là", "ici"],
            "triggers_kept": ["regarde", "tu vois", "là", "ici"]}
    path.write_text(json.dumps(v102), encoding="utf-8")
    cfg = config.Config.load()
    assert "là" not in cfg.triggers and "là" not in cfg.triggers_kept  # « là » retiré en 1.0.6
    path.write_text(json.dumps({"triggers": ["capture", "go"]}), encoding="utf-8")
    assert config.Config.load().triggers == ["capture", "go"]  # liste choisie par l'utilisateur : gardée


def test_meme_vue():
    monitor = Monitor(1, 1, 0, 0, 2560, 1440)
    page = Image.new("RGB", (2560, 1440), (240, 241, 244))
    dialog = page.copy()
    ImageDraw.Draw(dialog).rectangle((700, 300, 1900, 1100), fill=(32, 33, 36))
    typed = page.copy()
    ImageDraw.Draw(typed).text((800, 600), "Bonjour", fill=(0, 0, 0))
    first = Frame(1.0, monitor, (500, 500), sig=signature(page))
    assert first.same_view(Frame(4.0, monitor, (560, 520), sig=signature(page)))  # souris à peine bougée
    assert first.same_view(Frame(4.0, monitor, (500, 500), sig=signature(typed)))  # un mot tapé
    assert not first.same_view(Frame(4.0, monitor, (1500, 900), sig=signature(page)))  # souris ailleurs
    assert not first.same_view(Frame(4.0, monitor, (500, 500), sig=signature(dialog)))  # fenêtre ouverte
    other = Monitor(2, 2, 2560, 0, 2560, 1440)
    assert not first.same_view(Frame(4.0, other, (500, 500), sig=signature(page)))  # autre écran
    assert not first.same_view(Frame(4.0, monitor, (500, 500)))  # vignette inconnue : on capture


def test_captures_cliquables_dans_l_apercu():
    md = "**[0:01]** Regarde, **[C1 · 0:01]** le bouton\n\n![C1 · 0:01 · écran 2](C1_0m01s.png)\n"
    images: list[tuple[str, str]] = []
    page = to_html(md, lambda ref: f"file:///D:/retours/{ref}", images=images)
    assert images == [("C1 · 0:01 · écran 2", "file:///D:/retours/C1_0m01s.png")]
    assert '<a href="capture:0"' in page and 'src="file:///D:/retours/C1_0m01s.png"' in page


def test_raccourcis():
    assert parse_hotkey("Ctrl+Alt+Space") == (MOD_CONTROL | MOD_ALT, 0x20)
    assert parse_hotkey("F8") == (0, 0x77)
    assert parse_hotkey("ctrl+shift+r") == (MOD_CONTROL | MOD_SHIFT, ord("R"))
    for bad in ("", "Ctrl+", "Hyper+X", "Ctrl+Alt+Machin"):
        with pytest.raises(ValueError):
            parse_hotkey(bad)


def test_tous_les_bruitages_sont_propres(tmp_path, monkeypatch):
    monkeypatch.setattr(sounds, "SOUND_DIR", tmp_path)
    for name, (peak_db, _priority) in sounds.SOUNDS.items():
        with wave.open(str(sounds._file(name, 100))) as w:
            assert w.getframerate() == sounds.SR
            x = np.frombuffer(w.readframes(w.getnframes()), dtype="<i2") / 32767
        assert 0.05 < len(x) / sounds.SR < 2.0, name
        assert abs(20 * np.log10(np.max(np.abs(x))) - peak_db) < 0.5, name  # niveau prévu, sans saturation
        assert abs(np.mean(x)) < 0.01, name
