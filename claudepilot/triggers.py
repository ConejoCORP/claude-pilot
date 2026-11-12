"""Mots déclencheurs (« capture », « regarde », « ici »…) et commandes vocales repérés dans la transcription."""
from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass

from .model import Word

COMMANDS = {
    "point": (
        "point suivant", "nouveau point", "sujet suivant",
        "passons au point suivant", "on passe au point suivant", "passe au point suivant",
    ),
}
# « Retire la capture », « annule la dernière capture d'écran », « supprime le screen » : un verbe à l'impératif,
# quelques petits mots, puis la capture. Sans le mot « capture », « supprime ça » reste une remarque pour Claude.
CANCEL_VERBS = {
    "annule", "supprime", "efface", "retire", "enlève", "vire", "oublie", "jette",
    "annulez", "supprimez", "effacez", "retirez", "enlevez",
}
CANCEL_NOUNS = {"capture", "screen", "screenshot"}
_CANCEL_FILLERS = {"la", "le", "l", "cette", "ce", "cet", "ma", "mon", "moi", "toute", "dernière", "dernier",
                   "précédente", "précédent"}
_SCREEN = {"d'écran", "d'ecran"}
# Verbes qui ne déclenchent une capture qu'à l'impératif : « Regarde, ce bouton » oui, « je regarde » non.
IMPERATIVE_ONLY = {"regarde", "regardez", "vois", "voyez", "check"}
_SUBJECTS = {"je", "j", "tu", "il", "elle", "on", "nous", "vous", "ils", "elles", "ça", "ca", "cela", "qui"}
_CLITICS = {"ne", "n", "me", "m", "te", "t", "se", "s", "le", "la", "les", "l", "lui", "leur", "y", "en"}
# « Sur la capture d'avant », « cette capture » : on parle d'une capture, on n'en demande pas une nouvelle.
_DETERMINERS = {"la", "le", "l", "les", "cette", "ce", "cet", "ces", "ma", "mon", "mes", "ta", "ton", "tes", "sa",
                "son", "ses", "du", "des", "au", "aux"}
_ORDINALS = {"première", "premier", "deuxième", "troisième", "dernière", "dernier", "autre", "même", "précédente",
             "précédent"}
_TAKE = {"fais", "fait", "faire", "prends", "prend", "prendre", "refais", "reprends"}
_EDGE_PUNCT = re.compile(r"^[^\w']+|[^\w']+$")


@dataclass(frozen=True)
class VoiceAction:
    kind: str  # "capture", "cancel" ou "point"
    t: float  # fin du dernier mot prononcé
    words: tuple[int, ...]  # index des mots concernés
    remove: bool  # retirer ces mots du texte final
    phrase: str
    everyday: bool = False  # « ici », « tu vois » : mot du quotidien, capture seulement si elle apporte du neuf


def norm(token: str) -> str:
    text = unicodedata.normalize("NFC", token).lower().replace("’", "'").replace("`", "'")
    return _EDGE_PUNCT.sub("", text)


def _split(phrase: str) -> tuple[str, ...]:
    return tuple(t for t in (norm(part) for part in phrase.split()) if t)


def _is_imperative(tokens: list[str], i: int) -> bool:
    j, skipped = i - 1, 0
    while j >= 0 and skipped < 3:
        tok = tokens[j]
        if "'" in tok:  # « j' », « qu'on » -> « j », « on »
            tail = tok.split("'")[-1]
            tok = tail or tok.split("'")[0]
        if tok in _SUBJECTS:
            return False
        if tok in _CLITICS:
            j, skipped = j - 1, skipped + 1
            continue
        return True
    return True


def _cancel_length(tokens: list[str], i: int) -> int:
    """Nombre de mots de la commande « retire la capture » qui commence au mot i (0 s'il n'y en a pas)."""
    if tokens[i].split("-")[0] not in CANCEL_VERBS or not _is_imperative(tokens, i):
        return 0
    j = i + 1
    while j < len(tokens) and j - i <= 3 and tokens[j] in _CANCEL_FILLERS:
        j += 1
    if j < len(tokens) and tokens[j] in CANCEL_NOUNS:
        j += 1
        if j < len(tokens) and tokens[j] in _SCREEN:
            j += 1
        return j - i
    if j > i + 1:  # « supprime la carte pure d'écran » : « capture » mal reconnu, mais « d'écran » ne trompe pas
        for k in (j + 1, j + 2):
            if k < len(tokens) and tokens[k] in _SCREEN:
                return k + 1 - i
    return 0


def _names_a_capture(tokens: list[str], i: int) -> bool:
    """« sur la capture d'avant », « l'autre screen » : un déterminant devant, sans « fais » ni « prends »."""
    before = [part for tok in tokens[max(0, i - 4):i] for part in tok.split("'") if part]
    k = len(before) - 1
    while k >= 0 and before[k] in _ORDINALS:
        k -= 1
    if k < 0 or before[k] not in _DETERMINERS:
        return False
    return k == 0 or before[k - 1] not in _TAKE


def detect(words: list[Word], triggers: list[str], kept: list[str], commands: bool = True) -> list[VoiceAction]:
    """Repère les captures vocales et les commandes, du plus long motif au plus court."""
    tokens = [norm(w.text) for w in words]
    kept_set = {" ".join(_split(k)) for k in kept}
    patterns: list[tuple[str, tuple[str, ...], bool, str]] = []
    if commands:
        for kind, phrases in COMMANDS.items():
            patterns.extend((kind, _split(p), True, p) for p in phrases)
    for trigger in triggers:
        toks = _split(trigger)
        if toks:
            patterns.append(("capture", toks, " ".join(toks) not in kept_set, trigger))
    patterns.sort(key=lambda p: len(p[1]), reverse=True)

    actions: list[VoiceAction] = []
    i = 0
    while i < len(tokens):
        n = _cancel_length(tokens, i) if commands else 0
        if n:
            phrase = " ".join(w.text for w in words[i:i + n])
            actions.append(VoiceAction("cancel", words[i + n - 1].end, tuple(range(i, i + n)), True, phrase))
            i += n
            continue
        parts = tokens[i].split("-")  # « là-bas », « ce bouton-là », « regarde-moi »
        for kind, pattern, remove, phrase in patterns:
            n = len(pattern)
            matched = pattern[0] in parts if n == 1 else tuple(tokens[i:i + n]) == pattern
            if not matched:
                continue
            if kind == "capture" and n == 1 and pattern[0] in IMPERATIVE_ONLY and not _is_imperative(tokens, i):
                continue
            if kind == "capture" and remove and _names_a_capture(tokens, i):
                continue
            everyday = kind == "capture" and not remove and pattern[0] not in IMPERATIVE_ONLY
            actions.append(VoiceAction(kind, words[i + n - 1].end, tuple(range(i, i + n)), remove, phrase, everyday))
            i += n
            break
        else:
            i += 1
    return actions
