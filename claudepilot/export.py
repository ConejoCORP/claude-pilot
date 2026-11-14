"""Assemblage du retour pour Claude Code : texte horodaté, marqueurs [C1 · 0:12] et captures prêtes à lire."""
from __future__ import annotations

import logging
import os
import re
import zipfile
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

from PIL import Image

from . import triggers
from .model import Capture, Utterance, Word, fmt_duration, fmt_time
from .screen import render_capture

log = logging.getLogger(__name__)

RETOUR_NAME = "retour.md"
LATEST_NAME = "dernier_retour.md"
_EXPORTED_IMAGE = re.compile(r"^C\d+_\d+m\d{2}s\.png$")


@dataclass
class Layout:
    """Où placer chaque capture dans le texte, et quels mots retirer (commandes vocales)."""

    before: dict[int, list[int]] = field(default_factory=lambda: defaultdict(list))
    after: dict[int, list[int]] = field(default_factory=lambda: defaultdict(list))
    removed: set[int] = field(default_factory=set)
    point_starts: set[int] = field(default_factory=set)
    orphans: list[int] = field(default_factory=list)


@dataclass
class ExportResult:
    md_path: Path
    latest_path: Path
    images: list[Path]
    prompt: str


def capture_filename(n: int, t: float) -> str:
    s = max(0, int(t))
    return f"C{n}_{s // 60}m{s % 60:02d}s.png"


def exported_images(folder: Path) -> list[Path]:
    images = [p for p in folder.iterdir() if _EXPORTED_IMAGE.match(p.name)]
    return sorted(images, key=lambda p: int(p.name[1:].split("_")[0]))


def compute_layout(
    utterances: list[Utterance],
    captures: list[Capture],
    trigger_words: list[str],
    kept: list[str],
    commands: bool = True,
) -> Layout:
    """captures : déjà triées par moment et numérotées 1..n dans cet ordre."""
    words: list[Word] = []
    spans: list[tuple[int, int, float, float]] = []  # premier mot, dernier+1, début, fin
    for utt in utterances:
        if utt.words:
            spans.append((len(words), len(words) + len(utt.words), utt.start, utt.end))
            words.extend(utt.words)

    layout = Layout()
    actions = triggers.detect(words, trigger_words, kept, commands)
    for action in actions:
        if action.remove:
            layout.removed.update(action.words)
        if action.kind == "point":
            layout.point_starts.add(action.words[0])
    voice = [a for a in actions if a.kind == "capture"]
    used: set[int] = set()

    for n, cap in enumerate(captures, start=1):
        if not words:
            layout.orphans.append(n)
            continue
        if cap.source == "voix":
            best = None
            for k, action in enumerate(voice):
                if k in used or abs(action.t - cap.t) > 1.5:
                    continue
                if best is None or abs(action.t - cap.t) < abs(voice[best].t - cap.t):
                    best = k
            if best is not None:
                used.add(best)
                layout.after[voice[best].words[-1]].append(n)
                continue
        _place_by_time(layout, words, spans, n, cap.t)
    return layout


def _place_by_time(layout: Layout, words: list[Word], spans, n: int, t: float) -> None:
    for first, last, start, end in spans:
        if start - 0.3 <= t <= end + 0.3:
            previous = [i for i in range(first, last) if words[i].mid < t]
            if previous:
                layout.after[previous[-1]].append(n)
            else:
                layout.before[first].append(n)
            return
    for first, _last, start, _end in spans:
        if start > t:  # capture prise dans un silence : elle introduit la phrase suivante
            layout.before[first].append(n)
            return
    layout.after[spans[-1][1] - 1].append(n)


def render_markdown(
    utterances: list[Utterance],
    captures: list[Capture],
    names: list[str],
    layout: Layout,
    started,
    duration: float,
    engine_label: str,
    folder: Path,
    image_ref: Callable[[int], str],
    cursor_ring: bool = False,
    source: str = "",
) -> str:
    """source : nom du fichier importé ; vide pour un retour enregistré en direct."""
    count = len(captures)
    plural = "s" if count > 1 else ""
    if source:
        out = [
            f"# Transcription de « {source} » ({fmt_duration(duration)}, {count} capture{plural})",
            "",
            f"> **Pour Claude :** transcription automatique du fichier `{source}`, importé dans Claude Pilot.",
        ]
    else:
        out = [
            f"# Retour vocal du {started:%d/%m/%Y} à {started:%H:%M} ({fmt_duration(duration)}, {count} capture{plural})",
            "",
            "> **Pour Claude :** transcription automatique de ce que j'ai dit à voix haute en te montrant mon écran.",
        ]
    if count:
        moment = "image de la vidéo" if source else "capture d'écran prise"
        out += [
            f"> - `[Cn · m:ss]` = {moment} à cet instant précis de la phrase. "
            "Lis chaque capture listée en bas de ce fichier (outil Read) avant de répondre.",
            "> - Sur les captures, le cercle rouge montre où était ma souris (souvent ce dont je parle : "
            "« ce bouton », « ici »). Le bandeau du haut rappelle le numéro et le moment." if cursor_ring else
            "> - Le bandeau en haut de chaque capture rappelle son numéro et son moment.",
        ]
    out += ["> - Quelques mots peuvent être mal reconnus : fie-toi au sens général et aux captures.", ""]

    def marker(n: int) -> str:
        return f"**[C{n} · {fmt_time(captures[n - 1].t)}]**"

    # 1. Une phrase = un paragraphe ; « point suivant » ouvre une section.
    blocks: list[dict] = []
    current: dict | None = None

    def paragraph() -> dict:
        nonlocal current
        if current is None:
            current = {"kind": "para", "t": None, "tokens": [], "caps": [], "words": 0}
        return current

    def add_marker(n: int) -> None:
        p = paragraph()
        p["tokens"].append(marker(n))
        p["caps"].append(n)
        if p["t"] is None:
            p["t"] = captures[n - 1].t

    def close() -> None:
        nonlocal current
        if current is not None and current["tokens"]:
            blocks.append(current)
        current = None

    index = 0
    for utt in utterances:
        if not utt.words:
            continue
        for word in utt.words:
            if index in layout.point_starts:
                close()
                blocks.append({"kind": "heading"})
            for n in layout.before.get(index, []):
                add_marker(n)
            if index not in layout.removed:
                p = paragraph()
                if p["t"] is None:
                    p["t"] = word.start
                p["tokens"].append(word.text)
                p["words"] += 1
            for n in layout.after.get(index, []):
                add_marker(n)
            index += 1
        close()

    # 2. « Capture. » dit seul après une pause : le marqueur rejoint la phrase d'avant (sinon celle d'après).
    merged: list[dict] = []
    carry: dict | None = None
    for block in blocks:
        if block["kind"] == "heading":
            if carry is not None:
                merged.append(carry)
                carry = None
            merged.append(block)
        elif block["words"] == 0 and merged and merged[-1]["kind"] == "para":
            merged[-1]["tokens"] += block["tokens"]
            merged[-1]["caps"] += block["caps"]
        elif block["words"] == 0:
            if carry is None:
                carry = block
            else:
                carry["tokens"] += block["tokens"]
                carry["caps"] += block["caps"]
        else:
            if carry is not None:
                block["tokens"] = carry["tokens"] + block["tokens"]
                block["caps"] = carry["caps"] + block["caps"]
                block["t"] = min(carry["t"], block["t"])
                carry = None
            merged.append(block)
    if carry is not None:
        merged.append(carry)

    # 3. Rendu.
    point_no = 0
    pending_heading = any(b["kind"] == "heading" for b in merged)
    for block in merged:
        if block["kind"] == "heading":
            pending_heading = True
            continue
        if pending_heading:
            point_no += 1
            out += [f"### Point {point_no}", ""]
            pending_heading = False
        out += [f"**[{fmt_time(block['t'] or 0)}]** " + " ".join(block["tokens"]), ""]
        for n in block["caps"]:
            cap = captures[n - 1]
            out.append(f"![C{n} · {fmt_time(cap.t)} · {cap.where}]({image_ref(n)})")
        if block["caps"]:
            out.append("")

    if layout.orphans:
        out += ["Captures prises sans parole :", ""]
        for n in layout.orphans:
            cap = captures[n - 1]
            out += [marker(n), "", f"![C{n} · {fmt_time(cap.t)} · {cap.where}]({image_ref(n)})", ""]

    if count:
        out += ["## Captures (dans l'ordre)", ""]
        for n, cap in enumerate(captures, start=1):
            w, h = cap.size
            out.append(
                f"{n}. **C{n} · {fmt_time(cap.t)}** · {cap.where} ({w}×{h}) · {cap.source} — "
                f"`{folder / names[n - 1]}`"
            )
        out.append("")
    out += ["---", f"*Transcrit avec {engine_label or 'un moteur inconnu'} · Claude Pilot*", ""]
    return "\n".join(out)


def claude_code_prompt(md_path: Path, count: int) -> str:
    if not count:
        return f"Voici mon retour vocal : lis `{md_path}` et traite mes remarques point par point."
    plural = "s" if count > 1 else ""
    return (
        f"Voici mon retour vocal (transcription + {count} capture{plural} d'écran) : lis `{md_path}`, "
        f"puis chaque capture qu'il liste, dans l'ordre, et traite mes remarques point par point."
    )


def export_session(session, cfg) -> ExportResult:
    """Écrit retour.md, les captures C1…Cn et retours/dernier_retour.md."""
    folder: Path = session.folder
    captures = sorted((c for c in session.captures if not c.cancelled), key=lambda c: c.t)
    for c in captures:
        c.t = min(c.t, session.duration) if session.duration else c.t

    for old in folder.iterdir():
        if _EXPORTED_IMAGE.match(old.name):
            old.unlink()
    names, images = [], []
    for n, cap in enumerate(captures, start=1):
        name = capture_filename(n, cap.t)
        with Image.open(session.raw_dir / cap.raw_file) as raw:
            label = f"C{n} · {fmt_time(cap.t)} · {cap.where}"
            rendered = render_capture(raw, label, cap.cursor if cfg.cursor_ring else None, cfg.export_max_px)
        rendered.save(folder / name, "PNG", compress_level=6)
        names.append(name)
        images.append(folder / name)

    layout = compute_layout(session.utterances, captures, cfg.triggers, cfg.triggers_kept, cfg.voice_commands)
    common = (session.utterances, captures, names, layout, session.started, session.duration,
              session.engine_label, folder)
    options = {"cursor_ring": cfg.cursor_ring, "source": session.source}
    md_path = folder / RETOUR_NAME
    md_path.write_text(render_markdown(*common, image_ref=lambda n: names[n - 1], **options), encoding="utf-8")
    latest_path = folder.parent / LATEST_NAME
    latest_path.write_text(
        render_markdown(*common, image_ref=lambda n: f"<{(folder / names[n - 1]).as_posix()}>", **options),
        encoding="utf-8",
    )
    session.save()
    return ExportResult(md_path, latest_path, images, claude_code_prompt(md_path, len(captures)))


def make_zip(folder: Path) -> Path:
    """retour.md et ses captures dans un zip rangé dans le dossier du retour : à glisser dans un chat ou à envoyer.

    La liste finale de retour.md donne des chemins complets (pour Claude Code) : dans le zip, les noms suffisent.
    """
    md = (folder / RETOUR_NAME).read_text(encoding="utf-8").replace(str(folder) + os.sep, "")
    name = f"Retour {folder.name}"
    path = folder / f"{name}.zip"
    tmp = path.with_suffix(".tmp")
    with zipfile.ZipFile(tmp, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr(f"{name}/{RETOUR_NAME}", md)
        for image in exported_images(folder):
            archive.write(image, f"{name}/{image.name}", compress_type=zipfile.ZIP_STORED)  # PNG : déjà compressé
    tmp.replace(path)
    return path
