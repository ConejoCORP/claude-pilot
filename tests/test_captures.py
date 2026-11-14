"""Quand « ici », « tu vois », « regarde »… prennent-ils vraiment une capture ?"""
from PIL import Image, ImageDraw

from claudepilot.captures import VoiceCaptures
from claudepilot.model import Capture
from claudepilot.screen import Frame, Monitor, signature
from claudepilot.session import Session
from claudepilot.triggers import VoiceAction

MONITOR = Monitor(1, 1, 0, 0, 2560, 1440)
PAGE = Image.new("RGB", (2560, 1440), (240, 241, 244))
DIALOG = PAGE.copy()
ImageDraw.Draw(DIALOG).rectangle((700, 300, 1900, 1100), fill=(32, 33, 36))  # une fenêtre s'ouvre


class Screen:
    """Boîte noire de test : l'écran affiché et la souris se règlent à la main."""

    def __init__(self):
        self.image, self.cursor = PAGE, (500, 500)

    def frame_at(self, t: float) -> Frame:
        return Frame(t, MONITOR, self.cursor, sig=signature(self.image))


def voice_captures(tmp_path) -> tuple[VoiceCaptures, Screen]:
    session = Session.create(tmp_path)
    screen = Screen()

    def store(frame: Frame, source: str, trigger: str) -> str:
        name = f"{len(session.captures) + 1:03d}_{source}.png"
        session.add_capture(Capture(round(frame.t, 3), source, name, 1, (2560, 1440), frame.cursor, trigger))
        return name

    return VoiceCaptures(session, screen.frame_at, store), screen


def say(voice: VoiceCaptures, phrase: str, t: float, everyday: bool, remove: bool = False) -> bool:
    return voice.capture(VoiceAction("capture", t, (0,), remove, phrase, everyday))


def kept(voice: VoiceCaptures) -> list[tuple[str, float]]:
    return [(c.trigger, c.t) for c in voice.session.captures if not c.cancelled]


def test_mots_du_quotidien_sans_doublons(tmp_path):
    voice, screen = voice_captures(tmp_path)
    assert say(voice, "ici", 10.0, everyday=True)
    assert not say(voice, "tu vois", 11.5, everyday=True)  # juste après une capture
    assert not say(voice, "ici", 18.0, everyday=True)  # même écran, souris au même endroit
    screen.cursor = (1800, 900)
    assert say(voice, "ici", 19.0, everyday=True)  # la souris montre autre chose
    screen.image = DIALOG
    assert say(voice, "tu vois", 23.0, everyday=True)  # l'écran a changé
    assert not say(voice, "ici", 30.0, everyday=True)
    assert say(voice, "ici", 44.0, everyday=True)  # même vue, mais 20 s plus tard : on situe la suite du texte
    assert kept(voice) == [("ici", 10.0), ("ici", 19.0), ("tu vois", 23.0), ("ici", 44.0)]


def test_regarde_remplace_le_mot_du_quotidien_qui_le_precede(tmp_path):
    voice, _screen = voice_captures(tmp_path)
    assert say(voice, "ici", 36.3, everyday=True)
    assert say(voice, "regarde", 38.3, everyday=False)
    assert kept(voice) == [("regarde", 38.3)]
    assert not say(voice, "tu vois", 38.6, everyday=True)


def test_le_mot_capture_ne_double_pas_un_raccourci(tmp_path):
    voice, screen = voice_captures(tmp_path)
    frame = screen.frame_at(5.0)
    voice.remember(voice.store(frame, "raccourci", ""), frame)
    assert not say(voice, "capture", 5.8, everyday=False, remove=True)
    assert say(voice, "capture", 7.0, everyday=False, remove=True)  # « capture » insiste : toujours pris
    assert [c.source for c in voice.session.captures if not c.cancelled] == ["raccourci", "voix"]
