"""Quand un mot déclencheur prend-il vraiment une capture ? Mêmes règles en direct et pour un fichier importé."""
from __future__ import annotations

import logging
from typing import Callable

from .model import fmt_time
from .screen import Frame
from .session import Session
from .triggers import VoiceAction

log = logging.getLogger(__name__)

# « ici », « tu vois » restent dans le texte : ce sont des mots du quotidien. Ils ne capturent que si c'est
# utile : pas dans les 3 s autour d'une autre capture, ni la même vue (écran et souris) dans les 20 s.
EVERYDAY_GAP = 3.0
SAME_VIEW_WINDOW = 20.0


class VoiceCaptures:
    def __init__(
        self,
        session: Session,
        frame_at: Callable[[float], Frame | None],
        store: Callable[[Frame, str, str], str],
    ):
        self.session = session
        self.frame_at = frame_at  # image de ce moment : boîte noire en direct, image de la vidéo importée
        self.store = store  # (image, source, mot) -> nom de la capture enregistrée
        self.everyday: set[str] = set()  # captures prises sur « ici », « tu vois »… : un « regarde » juste après les remplace
        self.views: dict[str, Frame] = {}  # vignette de chaque capture, pour ne pas reprendre la même vue

    def remember(self, name: str, frame: Frame) -> None:
        self.views[name] = Frame(frame.t, frame.monitor, frame.cursor, sig=frame.sig)

    def capture(self, action: VoiceAction) -> bool:
        recent = [c for c in self.session.captures if not c.cancelled and abs(c.t - action.t) < EVERYDAY_GAP]
        if action.everyday and recent:
            return False  # une capture vient d'être prise
        replaced = [c for c in recent if c.raw_file in self.everyday and c.t <= action.t]
        if any(c not in replaced and abs(c.t - action.t) < 1.5 for c in recent):
            return False  # déjà capturé au même moment (raccourci + mot)
        frame = self.frame_at(action.t)
        if frame is None:
            log.warning("Pas d'image pour la capture vocale de %s", fmt_time(action.t))
            return False
        if action.everyday and self.same_view(frame):
            return False
        for cap in replaced:  # « ici… regarde » : on montre le moment du « regarde »
            self.session.cancel(cap)
        name = self.store(frame, "voix", action.phrase)
        self.remember(name, frame)
        if action.everyday:
            self.everyday.add(name)
        return True

    def same_view(self, frame: Frame) -> bool:
        """La dernière capture montre déjà la même chose, depuis peu : une de plus n'apprendrait rien à Claude."""
        previous = [c for c in self.session.captures if not c.cancelled and c.t <= frame.t + 0.5]
        if not previous:
            return False
        last = max(previous, key=lambda c: c.t)
        view = self.views.get(last.raw_file)
        return view is not None and frame.t - last.t < SAME_VIEW_WINDOW and view.same_view(frame)
