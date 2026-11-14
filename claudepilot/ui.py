"""Éléments d'interface partagés : icônes, vumètre, barre flottante (invisible dans les captures)."""
from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from PySide6.QtCore import QPoint, QRectF, QSize, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QFont, QFontDatabase, QGuiApplication, QIcon, QPainter, QPainterPath, QPen, QPixmap
from PySide6.QtWidgets import QHBoxLayout, QLabel, QSizePolicy, QToolButton, QWidget

from . import winapi

APP_ICON = Path(__file__).parent / "assets" / "icon.ico"
GLYPHS = {  # Segoe Fluent Icons
    "camera": "", "pause": "", "play": "", "stop": "", "mic": "",
    "close": "", "settings": "", "folder": "", "copy": "", "refresh": "",
    "delete": "", "prev": "", "next": "", "import": "", "zip": "",
}


@lru_cache(maxsize=1)
def icon_family() -> str:
    available = set(QFontDatabase.families())
    for name in ("Segoe Fluent Icons", "Segoe MDL2 Assets"):
        if name in available:
            return name
    return "Segoe UI Symbol"


def glyph_icon(name: str, color: str = "#e8e8ea", size: int = 20) -> QIcon:
    """Icône dessinée en image : aucune feuille de style ne peut la remplacer par un carré vide."""
    scale = 2
    pix = QPixmap(size * scale, size * scale)
    pix.fill(Qt.GlobalColor.transparent)
    p = QPainter(pix)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    p.setRenderHint(QPainter.RenderHint.TextAntialiasing)
    font = QFont(icon_family())
    font.setPixelSize(int(size * scale * 0.8))
    p.setFont(font)
    p.setPen(QColor(color))
    p.drawText(pix.rect(), Qt.AlignmentFlag.AlignCenter, GLYPHS[name])
    p.end()
    pix.setDevicePixelRatio(scale)
    return QIcon(pix)


class LevelMeter(QWidget):
    """Vumètre : on voit d'un coup d'œil que le micro capte bien."""

    def __init__(self, parent=None, horizontal: bool = False, length: int = 20, thickness: int = 5):
        super().__init__(parent)
        self.horizontal = horizontal
        if horizontal:
            self.setFixedSize(length, thickness)
        else:
            self.setFixedSize(thickness, length)
        self.level = 0.0

    def set_db(self, db: float) -> None:
        level = min(1.0, max(0.0, (db + 60.0) / 60.0))
        if abs(level - self.level) > 0.02:
            self.level = level
            self.update()

    def paintEvent(self, _event) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setPen(Qt.PenStyle.NoPen)
        radius = min(self.width(), self.height()) / 2
        p.setBrush(QColor(255, 255, 255, 40))
        p.drawRoundedRect(QRectF(self.rect()), radius, radius)
        color = QColor("#4ade80" if self.level < 0.85 else "#f87171")
        p.setBrush(color)
        if self.horizontal:
            w = self.width() * self.level
            if w >= 1:
                p.drawRoundedRect(QRectF(0, 0, w, self.height()), radius, radius)
        else:
            h = self.height() * self.level
            if h >= 1:
                p.drawRoundedRect(QRectF(0, self.height() - h, self.width(), h), radius, radius)


class Overlay(QWidget):
    """Petite barre toujours au premier plan pendant l'enregistrement, exclue des captures d'écran."""

    capture_clicked = Signal()
    pause_clicked = Signal()
    stop_clicked = Signal()
    copy_clicked = Signal()
    open_clicked = Signal()
    moved = Signal(int, int)

    def __init__(self):
        super().__init__(
            None,
            Qt.WindowType.FramelessWindowHint | Qt.WindowType.WindowStaysOnTopHint
            | Qt.WindowType.Tool | Qt.WindowType.WindowDoesNotAcceptFocus,
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating)
        self.setFixedWidth(680)
        self.setStyleSheet(
            'QLabel { color: #e8e8ea; font-family: "Segoe UI"; font-size: 10pt; }'
            "QLabel#muted { color: #9a9aa2; font-size: 9pt; }"
            'QToolButton { color: #e8e8ea; background: transparent; border: none; border-radius: 6px;'
            ' padding: 4px 9px; font-family: "Segoe UI"; font-size: 10pt; }'
            "QToolButton:hover { background: rgba(255, 255, 255, 0.14); }"
            "QToolButton#primary { background: #2563eb; color: white; }"
            "QToolButton#primary:hover { background: #1d4ed8; }"
        )
        self._hwnd = 0
        self._visible = False
        self._flash = False
        self._drag: QPoint | None = None
        self._dot_on = True
        self._dot_color = "#ef4444"
        self._caption = ""
        self._blink = QTimer(self)
        self._blink.setInterval(600)
        self._blink.timeout.connect(self._toggle_dot)
        self._autohide = QTimer(self)
        self._autohide.setSingleShot(True)
        self._autohide.timeout.connect(self.hide)

        row = QHBoxLayout(self)
        row.setContentsMargins(14, 7, 8, 7)
        row.setSpacing(10)
        self.dot = QLabel("●")
        self.level = LevelMeter()
        self.clock = QLabel("0:00")
        self.count = QLabel()
        self.caption = QLabel()
        self.caption.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        self.caption.setMinimumWidth(60)
        self.engine = QLabel()
        self.engine.setObjectName("muted")
        self.btn_capture = self._icon_button("camera", "Capturer l'écran sous la souris", self.capture_clicked)
        self.btn_pause = self._icon_button("pause", "Pause", self.pause_clicked)
        self.btn_stop = self._icon_button("stop", "Terminer et préparer le retour", self.stop_clicked)
        self.btn_copy = self._text_button("Copier pour Claude Code", self.copy_clicked, primary=True)
        self.btn_open = self._text_button("Ouvrir", self.open_clicked)
        self.btn_close = self._icon_button("close", "Masquer", None)
        for w in (self.dot, self.level, self.clock, self.count, self.caption, self.engine, self.btn_capture,
                  self.btn_pause, self.btn_stop, self.btn_copy, self.btn_open, self.btn_close):
            row.addWidget(w)
        self._mode("recording")

    def _icon_button(self, icon: str, tip: str, signal) -> QToolButton:
        button = QToolButton(self)
        button.setIcon(glyph_icon(icon, size=16))
        button.setIconSize(QSize(16, 16))
        button.setToolTip(tip)
        button.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        button.clicked.connect(signal.emit if signal is not None else self.hide)
        return button

    def _text_button(self, text: str, signal, primary: bool = False) -> QToolButton:
        button = QToolButton(self)
        button.setText(text)
        button.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        if primary:
            button.setObjectName("primary")
        button.clicked.connect(signal.emit)
        return button

    # --- états
    def _mode(self, mode: str) -> None:
        live = mode in ("recording", "paused")
        for w in (self.level, self.clock, self.count, self.engine, self.btn_capture, self.btn_pause, self.btn_stop):
            w.setVisible(live)
        for w in (self.btn_copy, self.btn_open):
            w.setVisible(mode == "ready")
        self.btn_close.setVisible(mode in ("ready", "message"))

    def show_recording(self, engine_label: str, capture_hotkey: str) -> None:
        self._autohide.stop()
        self._mode("recording")
        self.btn_capture.setToolTip(f"Capturer l'écran sous la souris ({winapi.display_hotkey(capture_hotkey)})")
        self.btn_pause.setIcon(glyph_icon("pause", size=16))
        self.btn_pause.setToolTip("Pause")
        self.engine.setText(engine_label)
        self._set_dot("#ef4444", blink=True)
        self.set_caption("Je t'écoute…")
        self._show()

    def show_paused(self) -> None:
        self._mode("paused")
        self.btn_pause.setIcon(glyph_icon("play", size=16))
        self.btn_pause.setToolTip("Reprendre")
        self._set_dot("#9a9aa2", blink=False)
        self.set_caption("En pause : rien n'est enregistré")

    def show_busy(self, text: str) -> None:
        self._autohide.stop()
        self._mode("busy")
        self._set_dot("#f59e0b", blink=True)
        self.set_caption(text)
        self._show()

    def show_ready(self, text: str) -> None:
        self._mode("ready")
        self._set_dot("#22c55e", blink=False)
        self.set_caption(text)
        self._show()
        self._autohide.start(25000)

    def show_message(self, text: str, error: bool = False, timeout_ms: int = 8000) -> None:
        self._mode("message")
        self._set_dot("#ef4444" if error else "#60a5fa", blink=False)
        self.set_caption(text)
        self._show()
        self._autohide.start(timeout_ms)

    def update_live(self, seconds: float, count: int, level_db: float) -> None:
        total = int(seconds)
        self.clock.setText(f"{total // 60}:{total % 60:02d}")
        self.count.setText(f"{count} capture{'s' if count > 1 else ''}")
        self.level.set_db(level_db)

    def set_caption(self, text: str) -> None:
        self._caption = text
        self._elide()

    def flash(self) -> None:
        self._flash = True
        self.update()
        QTimer.singleShot(350, self._unflash)

    def physical_rect(self) -> tuple[int, int, int, int] | None:
        """Rectangle de la barre en vrais pixels ; appelé depuis le fil de capture (aucun appel Qt ici)."""
        hwnd = self._hwnd
        return winapi.window_rect(hwnd) if self._visible and hwnd else None

    def place(self, pos: list[int] | None) -> None:
        self.adjustSize()
        if pos and QGuiApplication.screenAt(QPoint(pos[0] + 20, pos[1] + 10)) is not None:
            self.move(pos[0], pos[1])
            return
        area = QGuiApplication.primaryScreen().availableGeometry()
        self.move(area.center().x() - self.width() // 2, area.top() + 12)

    # --- détails
    def _show(self) -> None:
        if not self.isVisible():
            self.show()
        self.raise_()
        QTimer.singleShot(0, self._elide)

    def _set_dot(self, color: str, blink: bool) -> None:
        self._dot_color = color
        self._dot_on = True
        self.dot.setStyleSheet(f"color: {color};")
        if blink:
            self._blink.start()
        else:
            self._blink.stop()

    def _toggle_dot(self) -> None:
        self._dot_on = not self._dot_on
        self.dot.setStyleSheet(f"color: {self._dot_color if self._dot_on else 'rgba(0,0,0,0)'};")

    def _unflash(self) -> None:
        self._flash = False
        self.update()

    def _elide(self) -> None:
        metrics = self.caption.fontMetrics()
        self.caption.setText(metrics.elidedText(self._caption, Qt.TextElideMode.ElideLeft, max(40, self.caption.width())))
        self.caption.setToolTip(self._caption)

    def showEvent(self, event) -> None:
        super().showEvent(event)
        self._hwnd = int(self.winId())
        winapi.exclude_from_capture(self._hwnd)
        self._visible = True

    def hideEvent(self, event) -> None:
        self._visible = False
        super().hideEvent(event)

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        self._elide()

    def paintEvent(self, _event) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        path = QPainterPath()
        path.addRoundedRect(QRectF(self.rect()).adjusted(1, 1, -1, -1), 12, 12)
        p.fillPath(path, QColor(24, 24, 27, 240))
        p.setPen(QPen(QColor("#ef4444") if self._flash else QColor(255, 255, 255, 45), 2 if self._flash else 1))
        p.drawPath(path)

    def mousePressEvent(self, event) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            self._drag = event.globalPosition().toPoint() - self.frameGeometry().topLeft()

    def mouseMoveEvent(self, event) -> None:
        if self._drag is not None:
            self.move(event.globalPosition().toPoint() - self._drag)

    def mouseReleaseEvent(self, _event) -> None:
        if self._drag is not None:
            self._drag = None
            self.moved.emit(self.x(), self.y())

