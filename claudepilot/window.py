"""Fenêtre principale de Claude Pilot : enregistrer, suivre en direct, retrouver et envoyer ses retours."""
from __future__ import annotations

import html
import re
from functools import partial
from pathlib import Path
from typing import Callable

from PySide6.QtCore import QSize, Qt, QTimer, QUrl, Signal
from PySide6.QtGui import QColor, QIcon, QImage, QTextDocument
from PySide6.QtWidgets import (
    QComboBox, QFrame, QHBoxLayout, QLabel, QListWidget, QListWidgetItem, QMainWindow, QPushButton,
    QSplitter, QTextBrowser, QToolButton, QVBoxLayout, QWidget,
)

from . import winapi
from .config import ENGINE_LABELS, Config
from .ui import APP_ICON, LevelMeter, glyph_icon
from .viewer import CaptureViewer

STYLE = """
QMainWindow, QWidget#root { background: #111113; }
QWidget { color: #e8e8ea; font-family: "Segoe UI"; font-size: 10pt; }
QLabel#title { font-size: 15pt; font-weight: 600; }
QLabel#section { color: #a1a1aa; font-size: 9pt; font-weight: 600; }
QLabel#muted { color: #9a9aa2; }
QLabel#clock { font-size: 17pt; font-weight: 600; }
QFrame#card { background: #18181b; border: 1px solid #27272a; border-radius: 12px; }
QListWidget#history { background: #18181b; border: 1px solid #27272a; border-radius: 12px; padding: 6px; outline: none; }
QListWidget#history::item { padding: 8px 10px; border-radius: 8px; margin: 1px 0; }
QListWidget#history::item:selected { background: #1e3a8a; color: #ffffff; }
QListWidget#history::item:hover:!selected { background: #222226; }
QTextBrowser { background: #18181b; border: 1px solid #27272a; border-radius: 12px; padding: 10px 18px;
               selection-background-color: #1d4ed8; }
QPushButton { background: #232327; border: 1px solid #2f2f35; border-radius: 8px; padding: 7px 14px; }
QPushButton:hover { background: #2c2c32; }
QPushButton:checked { background: #14532d; border-color: #16a34a; }
QPushButton:disabled { color: #5c5c63; background: #1b1b1f; border-color: #26262b; }
QPushButton#primary { background: #2563eb; border-color: #2563eb; color: #ffffff; }
QPushButton#primary:hover { background: #1d4ed8; }
QPushButton#primary:disabled { background: #1e293b; border-color: #1e293b; color: #64748b; }
QPushButton#record { background: #dc2626; border-color: #dc2626; color: #ffffff; font-size: 11pt; font-weight: 600;
                     padding: 10px 22px; border-radius: 10px; }
QPushButton#record:hover { background: #b91c1c; }
QPushButton#record[recording="true"] { background: #2a1215; border: 1px solid #ef4444; color: #fecaca; }
QPushButton#record:disabled { background: #3f3f46; border-color: #3f3f46; color: #a1a1aa; }
QComboBox { background: #232327; border: 1px solid #2f2f35; border-radius: 8px; padding: 5px 10px; min-width: 170px; }
QComboBox:hover { border-color: #3f3f46; }
QComboBox:disabled { color: #5c5c63; }
QComboBox QAbstractItemView { background: #232327; border: 1px solid #2f2f35; selection-background-color: #1e3a8a;
                              outline: none; }
QToolButton { background: transparent; border: none; border-radius: 8px; padding: 6px; }
QToolButton:hover { background: #232327; }
QMenu { background: #232327; border: 1px solid #2f2f35; padding: 4px; }
QMenu::item { padding: 6px 18px; border-radius: 6px; }
QMenu::item:selected { background: #1e3a8a; }
QStatusBar { color: #9a9aa2; }
QStatusBar::item { border: none; }
QSplitter::handle { background: transparent; }
QScrollBar:vertical { background: transparent; width: 10px; margin: 4px 2px; }
QScrollBar::handle:vertical { background: #34343b; border-radius: 4px; min-height: 30px; }
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical { height: 0; }
QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical { background: transparent; }
QToolTip { background: #232327; color: #e8e8ea; border: 1px solid #3f3f46; }
QDialog { background: #18181b; }
QLineEdit, QPlainTextEdit, QSpinBox, QDoubleSpinBox { background: #232327; border: 1px solid #2f2f35; border-radius: 6px;
                                                      padding: 5px 8px; selection-background-color: #1d4ed8; }
QLineEdit:focus, QPlainTextEdit:focus, QSpinBox:focus, QDoubleSpinBox:focus { border-color: #2563eb; }
QSlider::groove:horizontal { height: 4px; background: #3f3f46; border-radius: 2px; }
QSlider::sub-page:horizontal { background: #2563eb; border-radius: 2px; }
QSlider::handle:horizontal { width: 14px; margin: -5px 0; border-radius: 7px; background: #e8e8ea; }
QTabWidget::pane { border: 1px solid #27272a; border-radius: 10px; background: #18181b; top: -1px; }
QTabBar::tab { background: transparent; color: #9a9aa2; padding: 8px 16px; border: none;
               border-bottom: 2px solid transparent; }
QTabBar::tab:selected { color: #ffffff; border-bottom: 2px solid #2563eb; }
QTabBar::tab:hover:!selected { color: #e8e8ea; }
"""

WELCOME = """
<h2 style="color:#f4f4f5;">Prêt pour un retour vocal</h2>
<p style="color:#a1a1aa;">Montre ton écran en parlant, comme en partage d'écran : Claude Code reçoit ta voix transcrite
et chaque capture à l'instant exact où tu l'as prise.</p>
<p><b>1.</b> Clique sur <b>Démarrer un retour</b>, en haut à gauche, ou appuie sur <b>{record}</b>, même depuis une
autre appli.</p>
<p><b>2.</b> Parle normalement. Pour capturer l'écran où est ta souris : <b>{capture}</b>, le bouton <b>Capturer</b>,
ou dis « capture », « regarde », « tu vois » ou « ici ».</p>
<p><b>3.</b> Termine avec <b>{record}</b> : le message pour Claude Code est copié. Colle-le avec <b>Ctrl+V</b>.</p>
<p style="color:#71717a;">Commandes vocales : « retire la capture », « point suivant ». Clique sur une capture pour la voir
en grand.</p>
"""
UNFINISHED = """
<h2 style="color:#fbbf24;">Enregistrement interrompu</h2>
<p>L'audio et les captures sont bien sur le disque. Clique sur <b>Récupérer cet enregistrement</b> pour le retranscrire
et préparer le retour.</p>
"""

_MARKER = re.compile(r"\*\*\[(C\d+ · \d+:\d{2}(?::\d{2})?)\]\*\*")
_STAMP = re.compile(r"^\*\*\[(\d+:\d{2}(?::\d{2})?)\]\*\*\s?(.*)$")
_IMAGE = re.compile(r"^!\[(.*?)\]\(<?(.*?)>?\)$")
_CHIP = '<span style="background-color:#1e3a8a; color:#dbeafe;">&nbsp;{}&nbsp;</span>'


def to_html(
    md: str, image_url: Callable[[str], str], image_width: int = 460, images: list[tuple[str, str]] | None = None
) -> str:
    """Aperçu lisible d'un retour.md. Son format vient de export.py : pas besoin d'un vrai moteur Markdown.

    images : reçoit (légende, adresse) de chaque capture ; chaque image devient alors un lien « capture:n ».
    """
    out: list[str] = []
    in_capture_list = False
    for line in md.splitlines():
        if line.startswith("## Captures"):
            in_capture_list = True  # liste destinée à Claude, déjà visible sous forme d'images
            continue
        if in_capture_list:
            if line.startswith("---"):
                in_capture_list = False
            continue
        if not line.strip() or line.startswith(">") or line.strip() == "---":
            continue
        if line.startswith("# "):
            out.append(f'<h2 style="color:#f4f4f5;">{html.escape(line[2:])}</h2>')
        elif line.startswith("### "):
            out.append(f'<h3 style="color:#93c5fd;">{html.escape(line[4:])}</h3>')
        elif (image := _IMAGE.match(line)) is not None:
            src = image_url(image.group(2))
            tag = f'<img src="{html.escape(src, quote=True)}" width="{image_width}">'
            if images is not None:
                tag = f'<a href="capture:{len(images)}" title="Clique pour la voir en grand">{tag}</a>'
                images.append((image.group(1), src))
            out.append(f'<p>{tag}<br><span style="color:#a1a1aa;">{html.escape(image.group(1))}</span></p>')
        elif line.startswith("*") and line.endswith("*") and not line.startswith("**"):
            out.append(f'<p style="color:#71717a; margin-top:18px;"><i>{html.escape(line.strip("*"))}</i></p>')
        else:
            stamp = _STAMP.match(line)
            when, text = (stamp.group(1), stamp.group(2)) if stamp else ("", line)
            body = _MARKER.sub(lambda m: _CHIP.format(m.group(1)), html.escape(text))
            prefix = f'<span style="color:#71717a;">{when}</span>&nbsp;&nbsp;' if when else ""
            out.append(f"<p>{prefix}{body}</p>")
    return "".join(out)


class Preview(QTextBrowser):
    """Aperçu du retour ; les images sont réduites une fois puis gardées en mémoire."""

    def __init__(self):
        super().__init__()
        self.images: dict[str, QImage] = {}
        self.setOpenLinks(False)
        self.document().setDefaultStyleSheet("a { text-decoration: none; }")

    def loadResource(self, kind, url: QUrl):
        image_kind = QTextDocument.ResourceType.ImageResource
        if kind == image_kind or kind == getattr(image_kind, "value", image_kind):
            key = url.toString()
            if key in self.images:
                return self.images[key]
            if url.isLocalFile():
                image = QImage(url.toLocalFile())
                if not image.isNull():
                    if image.width() > 1000:
                        image = image.scaledToWidth(1000, Qt.TransformationMode.SmoothTransformation)
                    self.images[key] = image
                    return image
        return super().loadResource(kind, url)


class MainWindow(QMainWindow):
    record_clicked = Signal()
    capture_clicked = Signal()
    pause_clicked = Signal()
    engine_changed = Signal(str)
    mic_changed = Signal(str)
    mic_test_toggled = Signal(bool)
    settings_clicked = Signal()
    session_selected = Signal(object)  # Path ou None
    copy_clicked = Signal()
    open_clicked = Signal()
    delete_clicked = Signal()
    recover_clicked = Signal()
    close_requested = Signal()

    def __init__(self):
        super().__init__()
        self.setWindowTitle("Claude Pilot")
        self.setWindowIcon(QIcon(str(APP_ICON)))
        self.resize(1180, 780)
        self.setMinimumSize(920, 600)
        self.setStyleSheet(STYLE)
        self._hwnd = 0
        self._visible = False
        self._images: list[tuple[str, str]] = []  # captures affichées dans l'aperçu : (légende, adresse)
        # Capture de l'enregistrement en cours, en grand (fourni par le contrôleur) ; None : la réduction suffira
        self.live_image: Callable[[str], QImage | None] = lambda _name: None

        root = QWidget()
        root.setObjectName("root")
        self.setCentralWidget(root)
        outer = QVBoxLayout(root)
        outer.setContentsMargins(18, 14, 18, 6)
        outer.setSpacing(12)
        outer.addLayout(self._header())
        splitter = QSplitter(Qt.Orientation.Horizontal)
        splitter.setHandleWidth(12)
        splitter.setChildrenCollapsible(False)
        splitter.addWidget(self._history_panel())
        splitter.addWidget(self._main_panel())
        splitter.setSizes([270, 910])
        splitter.setStretchFactor(1, 1)
        outer.addWidget(splitter, 1)
        self.statusBar().setSizeGripEnabled(False)

    # --- construction
    def _header(self) -> QHBoxLayout:
        row = QHBoxLayout()
        row.setSpacing(8)
        logo = QLabel()
        logo.setPixmap(QIcon(str(APP_ICON)).pixmap(30, 30))
        title = QLabel("Claude Pilot")
        title.setObjectName("title")
        row.addWidget(logo)
        row.addWidget(title)
        row.addStretch(1)
        row.addWidget(self._muted("Moteur"))
        self.engine = QComboBox()
        for key, label in ENGINE_LABELS.items():
            self.engine.addItem(label, key)
        self.engine.activated.connect(lambda i: self.engine_changed.emit(self.engine.itemData(i)))
        row.addWidget(self.engine)
        self.engine_status = QLabel()
        row.addWidget(self.engine_status)
        row.addSpacing(18)
        row.addWidget(self._muted("Micro"))
        self.mic = QComboBox()
        self.mic.setMinimumWidth(240)
        self.mic.activated.connect(lambda i: self.mic_changed.emit(self.mic.itemData(i)))
        row.addWidget(self.mic)
        self.mic_level = LevelMeter(horizontal=True, length=70, thickness=6)
        row.addWidget(self.mic_level)
        self.mic_test = QPushButton("Tester")
        self.mic_test.setCheckable(True)
        self.mic_test.setToolTip("Affiche le niveau du micro, sans rien enregistrer")
        self.mic_test.toggled.connect(self.mic_test_toggled.emit)
        row.addWidget(self.mic_test)
        self.settings_btn = QToolButton()
        self.settings_btn.setIcon(glyph_icon("settings"))
        self.settings_btn.setIconSize(QSize(20, 20))
        self.settings_btn.setToolTip("Réglages")
        self.settings_btn.clicked.connect(self.settings_clicked.emit)
        row.addWidget(self.settings_btn)
        return row

    def _history_panel(self) -> QWidget:
        panel = QWidget()
        col = QVBoxLayout(panel)
        col.setContentsMargins(0, 0, 0, 0)
        col.setSpacing(8)
        self.record_btn = QPushButton()  # en tête de la liste : le nouveau retour viendra s'y ajouter
        self.record_btn.setObjectName("record")
        self.record_btn.clicked.connect(self.record_clicked.emit)
        col.addWidget(self.record_btn)
        self.record_hint = self._muted("")
        self.record_hint.setAlignment(Qt.AlignmentFlag.AlignHCenter)
        self.record_hint.setWordWrap(True)
        col.addWidget(self.record_hint)
        col.addSpacing(8)
        label = QLabel("MES RETOURS")
        label.setObjectName("section")
        col.addWidget(label)
        self.history = QListWidget()
        self.history.setObjectName("history")
        self.history.currentItemChanged.connect(self._history_changed)
        self.history.itemClicked.connect(lambda item: self._history_changed(item, None))
        col.addWidget(self.history, 1)
        return panel

    def _main_panel(self) -> QWidget:
        panel = QWidget()
        col = QVBoxLayout(panel)
        col.setContentsMargins(0, 0, 0, 0)
        col.setSpacing(10)

        card = self.card = QFrame()  # visible seulement pendant un enregistrement
        card.setObjectName("card")
        card_col = QVBoxLayout(card)
        card_col.setContentsMargins(16, 14, 16, 12)
        card_col.setSpacing(8)
        row = QHBoxLayout()
        row.setSpacing(10)
        self.capture_btn = QPushButton(" Capturer")
        self.capture_btn.setIcon(glyph_icon("camera"))
        self.capture_btn.clicked.connect(self.capture_clicked.emit)
        self.pause_btn = QPushButton(" Pause")
        self.pause_btn.setIcon(glyph_icon("pause"))
        self.pause_btn.clicked.connect(self.pause_clicked.emit)
        row.addWidget(self.capture_btn)
        row.addWidget(self.pause_btn)
        row.addStretch(1)
        self.count = self._muted("")
        self.level = LevelMeter(horizontal=True, length=90, thickness=6)
        self.clock = QLabel("0:00")
        self.clock.setObjectName("clock")
        row.addWidget(self.count)
        row.addWidget(self.level)
        row.addWidget(self.clock)
        card_col.addLayout(row)
        self.hint = self._muted("")
        self.hint.setWordWrap(True)
        card_col.addWidget(self.hint)
        col.addWidget(card)

        self.preview = Preview()
        self.preview.anchorClicked.connect(self._open_capture)
        col.addWidget(self.preview, 1)

        actions = QHBoxLayout()
        actions.setSpacing(8)
        self.copy_btn = self._action("copy", "Copier pour Claude Code", self.copy_clicked, primary=True)
        self.recover_btn = self._action("refresh", "Récupérer cet enregistrement", self.recover_clicked, primary=True)
        self.open_btn = self._action("folder", "Ouvrir le dossier", self.open_clicked)
        self.delete_btn = self._action("delete", "Supprimer", self.delete_clicked)
        for button in (self.copy_btn, self.recover_btn, self.open_btn):
            actions.addWidget(button)
        actions.addStretch(1)
        actions.addWidget(self.delete_btn)
        col.addLayout(actions)
        return panel

    @staticmethod
    def _muted(text: str) -> QLabel:
        label = QLabel(text)
        label.setObjectName("muted")
        return label

    @staticmethod
    def _action(icon: str | None, text: str, signal, primary: bool = False) -> QPushButton:
        button = QPushButton(f" {text}" if icon else text)
        if icon:
            button.setIcon(glyph_icon(icon, color="#ffffff" if primary else "#e8e8ea"))
        if primary:
            button.setObjectName("primary")
        button.clicked.connect(signal.emit)
        return button

    # --- état
    def init_controls(self, cfg: Config, microphones: list[str]) -> None:
        self.engine.blockSignals(True)
        self.engine.setCurrentIndex(max(0, self.engine.findData(cfg.engine)))
        self.engine.blockSignals(False)
        self.mic.blockSignals(True)
        self.mic.clear()
        self.mic.addItem("Micro par défaut de Windows", "")
        for name in microphones:
            self.mic.addItem(name, name)
        self.mic.setCurrentIndex(max(0, self.mic.findData(cfg.microphone)))
        self.mic.blockSignals(False)

    def set_mode(self, mode: str, cfg: Config) -> None:
        """idle, recording, paused, finishing ou busy (comparaison, retranscription…)."""
        record = winapi.display_hotkey(cfg.hotkey_record)
        capture = winapi.display_hotkey(cfg.hotkey_capture)
        live = mode in ("recording", "paused")
        self.record_btn.setProperty("recording", live)
        self.record_btn.style().unpolish(self.record_btn)
        self.record_btn.style().polish(self.record_btn)
        self.record_btn.setEnabled(mode in ("idle", "recording", "paused"))
        if live:
            self.record_btn.setText("■   Terminer le retour")
            self.record_hint.setText(f"ou {record}")
            self.hint.setText(
                f"{record} pour terminer · {capture}, le bouton Capturer, « capture », « regarde », « ici »… : écran "
                "sous ta souris · « retire la capture », « point suivant »"
            )
        elif mode == "finishing":
            self.record_btn.setText("Préparation du retour…")
            self.record_hint.setText("encore une seconde")
            self.hint.setText("Transcription des dernières phrases : le retour se prépare.")
        else:
            self.record_btn.setText("●   Démarrer un retour")
            self.record_hint.setText(f"ou {record} depuis n'importe où")
        self.card.setVisible(live or mode == "finishing")
        self.capture_btn.setVisible(live)
        self.capture_btn.setToolTip(f"Capturer l'écran sous la souris ({capture})")
        self.pause_btn.setVisible(live)
        self.pause_btn.setText(" Reprendre" if mode == "paused" else " Pause")
        self.pause_btn.setIcon(glyph_icon("play" if mode == "paused" else "pause"))
        for widget in (self.clock, self.count):
            widget.setVisible(live or mode == "finishing")
        self.level.setVisible(live)
        idle = mode == "idle"
        for widget in (self.engine, self.mic, self.mic_test, self.settings_btn, self.history):
            widget.setEnabled(idle or (widget is self.history and mode == "busy"))

    def set_actions(self, kind: str | None) -> None:
        """None (rien de sélectionné), ready, unfinished ou busy."""
        for button in (self.copy_btn, self.open_btn):
            button.setVisible(kind != "unfinished")
            button.setEnabled(kind == "ready")
        self.recover_btn.setVisible(kind == "unfinished")
        self.delete_btn.setEnabled(kind in ("ready", "unfinished"))

    def set_engine_status(self, text: str, kind: str, tooltip: str = "") -> None:
        color = {"ok": "#4ade80", "busy": "#fbbf24", "error": "#f87171"}[kind]
        self.engine_status.setText(text)
        self.engine_status.setStyleSheet(f"color: {color};")
        self.engine_status.setToolTip(tooltip)

    def set_mic_test(self, on: bool) -> None:
        self.mic_test.blockSignals(True)
        self.mic_test.setChecked(on)
        self.mic_test.blockSignals(False)
        if not on:
            self.mic_level.set_db(-90.0)

    def update_live(self, seconds: float, count: int, level_db: float) -> None:
        total = int(seconds)
        self.clock.setText(f"{total // 60}:{total % 60:02d}")
        self.count.setText(f"{count} capture{'s' if count > 1 else ''}")
        self.level.set_db(level_db)

    def notify(self, text: str, error: bool = False) -> None:
        self.statusBar().setStyleSheet("color: #f87171;" if error else "")
        self.statusBar().showMessage(text, 15000 if error else 8000)

    # --- historique
    def set_history(self, entries: list[dict], select: Path | None) -> None:
        self.history.blockSignals(True)
        self.history.clear()
        chosen = None
        for entry in entries:
            item = QListWidgetItem(f"{entry['title']}\n{entry['subtitle']}")
            item.setData(Qt.ItemDataRole.UserRole, str(entry["folder"]))
            if entry["unfinished"]:
                item.setForeground(QColor("#fbbf24"))
            self.history.addItem(item)
            if select is not None and Path(entry["folder"]) == select:
                chosen = item
        if not entries:
            empty = QListWidgetItem("Aucun retour pour l'instant.\nLe premier apparaîtra ici.")
            empty.setFlags(Qt.ItemFlag.NoItemFlags)
            empty.setForeground(QColor("#71717a"))
            self.history.addItem(empty)
        if chosen is not None:
            self.history.setCurrentItem(chosen)
        self.history.blockSignals(False)

    def _history_changed(self, current, _previous) -> None:
        folder = current.data(Qt.ItemDataRole.UserRole) if current is not None else None
        self.session_selected.emit(Path(folder) if folder else None)

    # --- aperçu
    def show_welcome(self, cfg: Config) -> None:
        self._images = []
        self.preview.setHtml(WELCOME.format(record=winapi.display_hotkey(cfg.hotkey_record),
                                            capture=winapi.display_hotkey(cfg.hotkey_capture)))

    def show_unfinished(self) -> None:
        self._images = []
        self.preview.setHtml(UNFINISHED)

    def show_retour(self, folder: Path, md: str) -> None:
        self.preview.images = {k: v for k, v in self.preview.images.items() if k.startswith("cap:")}
        self._images = []
        self.preview.setHtml(to_html(md, lambda ref: QUrl.fromLocalFile(str(folder / ref)).toString(),
                                     images=self._images))
        self.preview.verticalScrollBar().setValue(0)

    def show_markdown(self, md: str) -> None:
        self._images = []
        self.preview.setMarkdown(md)
        self.preview.verticalScrollBar().setValue(0)

    def show_live(self, md: str) -> None:
        bar = self.preview.verticalScrollBar()
        follow = bar.value() >= bar.maximum() - 40
        self._images = []
        content = to_html(md, lambda ref: ref, image_width=380, images=self._images)
        self.preview.setHtml(content or '<p style="color:#9a9aa2;">Je t\'écoute…</p>')
        if follow:
            QTimer.singleShot(0, lambda: bar.setValue(bar.maximum()))

    def _open_capture(self, url: QUrl) -> None:
        """Clic sur une capture de l'aperçu : elle s'ouvre en grand, avec les autres à portée de flèche."""
        if url.scheme() != "capture" or not url.path().isdigit() or int(url.path()) >= len(self._images):
            return
        items = [(label, partial(self._full_image, src)) for label, src in self._images]
        CaptureViewer(items, int(url.path()), self).show()

    def _full_image(self, src: str) -> QImage | None:
        if src.startswith("cap:"):  # enregistrement en cours : l'image brute, sinon la réduction de l'aperçu
            return self.live_image(src[4:]) or self.preview.images.get(src)
        image = QImage(QUrl(src).toLocalFile())
        return None if image.isNull() else image

    def add_live_image(self, key: str, image: QImage) -> None:
        self.preview.images[key] = image

    def clear_live_images(self) -> None:
        self.preview.images = {k: v for k, v in self.preview.images.items() if not k.startswith("cap:")}

    # --- fenêtre
    def physical_rect(self) -> tuple[int, int, int, int] | None:
        """Appelé depuis le fil de capture : seulement des valeurs déjà connues, aucun appel Qt."""
        hwnd = self._hwnd
        return winapi.window_rect(hwnd) if self._visible and hwnd else None

    def bring_to_front(self) -> None:
        self.showNormal()
        self.raise_()
        self.activateWindow()

    def showEvent(self, event) -> None:
        super().showEvent(event)
        self._hwnd = int(self.winId())
        self._visible = True
        winapi.dark_title_bar(self._hwnd)

    def hideEvent(self, event) -> None:
        self._visible = False
        super().hideEvent(event)

    def closeEvent(self, event) -> None:
        event.ignore()  # le contrôleur décide : terminer l'enregistrement en cours, puis quitter
        self.close_requested.emit()
