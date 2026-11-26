"""Dictée partout : Maj+Win+H écrit ce que tu dis là où est le curseur, dans n'importe quelle appli.

Un petit processus en arrière-plan, lancé avec Windows. Entre deux dictées, il attend le raccourci, micro coupé.
Le moteur de transcription tourne à part (dictation_worker) : il démarre à la première dictée et se ferme après
le délai choisi, en rendant mémoire et carte graphique. Pendant qu'il se charge, on parle déjà : le son attend.

Lancement : pythonw -m claudepilot.dictation [reload | quit | toggle]
"""
from __future__ import annotations

import getpass
import json
import logging
import os
import queue
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path
from typing import Callable

import numpy as np
from PySide6.QtCore import QObject, QPoint, QRect, QRectF, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QCursor, QFontMetrics, QGuiApplication, QIcon, QPainter, QPen
from PySide6.QtNetwork import QLocalServer, QLocalSocket
from PySide6.QtWidgets import (
    QApplication, QHBoxLayout, QLabel, QMenu, QSystemTrayIcon, QToolButton, QVBoxLayout, QWidget,
)

from . import installinfo, sounds, winapi
from .audio import Recorder, refresh_devices
from .config import APP_DIR, DATA_DIR, INSTALL_DIR, Config
from .ui import APP_ICON, LevelMeter, glyph_icon
from .vad import StreamingVAD

log = logging.getLogger(__name__)

SERVER = f"ClaudePilotDictee-{getpass.getuser()}"
PID_FILE = DATA_DIR / installinfo.DICTATION_PIDS[0]
# Où l'utilisateur a posé la barre, en proportion de l'écran : elle revient au même endroit sur l'écran où il
# travaille. À part de config.json, que Claude Pilot réécrit en entier avec ses propres réglages.
SPOT_FILE = DATA_DIR / "dictee_position.json"
SILENCE_STOP_S = 20.0  # plus personne ne parle : la dictée s'arrête toute seule
NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)


# --- commandes depuis Claude Pilot
def send_command(command: str) -> bool:
    """« reload », « quit » ou « toggle » à la dictée déjà lancée. False : elle ne tourne pas."""
    socket = QLocalSocket()
    socket.connectToServer(SERVER)
    if not socket.waitForConnected(300):
        return False
    socket.write(command.encode())
    socket.waitForBytesWritten(300)
    socket.disconnectFromServer()
    return True


def _pythonw() -> Path:
    return Path(sys.executable).with_name("pythonw.exe")


def launch() -> None:
    subprocess.Popen([str(_pythonw()), "-m", "claudepilot.dictation"], cwd=str(APP_DIR), close_fds=True)


def ensure_running() -> None:
    """Lance la dictée si elle ne tourne pas ; sinon elle relit ses réglages."""
    if not send_command("reload"):
        launch()


def enable() -> bool:
    """Lancée avec Windows, et tout de suite. False : raccourci de démarrage impossible à créer."""
    icon = installinfo.icon_path(INSTALL_DIR) if INSTALL_DIR else APP_ICON
    ok = installinfo.create_dictation_shortcut(_pythonw(), APP_DIR, icon)
    ensure_running()
    return ok


def disable() -> None:
    installinfo.remove_dictation_shortcut()
    send_command("quit")


# --- moteur
class EngineProcess:
    """Le moteur dans son propre processus. Le fermer rend toute sa mémoire (y compris sur la carte graphique)."""

    def __init__(self, on_message: Callable[[dict, object], None]):
        self.on_message = on_message  # (message, processus), appelé depuis un fil de lecture
        self.proc: subprocess.Popen | None = None
        self.ready = False
        self._outbox: queue.Queue | None = None

    def start(self) -> None:
        if self.proc is not None and self.proc.poll() is None:
            return
        self.ready = False
        self.proc = subprocess.Popen(
            # -P : rien n'est chargé depuis le dossier de départ (le dossier temporaire)
            [str(Path(sys.executable).with_name("python.exe")), "-P", "-m", "claudepilot.dictation_worker"],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
            cwd=tempfile.gettempdir(), env=dict(os.environ, PYTHONPATH=str(APP_DIR)), creationflags=NO_WINDOW,
        )
        self._outbox = queue.Queue()
        threading.Thread(target=self._write, args=(self.proc, self._outbox), name="dictee-envoi", daemon=True).start()
        threading.Thread(target=self._read, args=(self.proc,), name="dictee-reponses", daemon=True).start()

    def submit(self, audio: np.ndarray) -> None:
        if self._outbox is not None:
            self._outbox.put(audio.astype(np.float32).tobytes())

    def release(self) -> None:
        """Le moteur finit les phrases déjà reçues, puis se ferme de lui-même."""
        if self._outbox is not None:
            self._outbox.put(b"")
        self.proc, self._outbox, self.ready = None, None, False

    @staticmethod
    def _write(proc: subprocess.Popen, outbox: queue.Queue) -> None:
        while True:
            data = outbox.get()
            try:
                proc.stdin.write(len(data).to_bytes(4, "little") + data)
                proc.stdin.flush()
                if not data:
                    proc.stdin.close()
                    return
            except OSError:
                return

    def _read(self, proc: subprocess.Popen) -> None:
        for line in proc.stdout:
            try:
                message = json.loads(line)
            except ValueError:
                continue
            if "ready" in message and proc is self.proc:
                self.ready = True
            self.on_message(message, proc)
        proc.wait()
        self.on_message({"exit": proc.returncode}, proc)


# --- barre affichée pendant la dictée
class DictationPill(QWidget):
    """En bas de l'écran pendant la dictée, ou là où on l'a glissée. Elle ne prend jamais le clavier : le texte va
    là où était le curseur, même pendant qu'on la déplace."""

    stop_clicked = Signal()

    def __init__(self, spot_file: Path = SPOT_FILE):
        super().__init__(None, Qt.WindowType.FramelessWindowHint | Qt.WindowType.Tool
                         | Qt.WindowType.WindowStaysOnTopHint | Qt.WindowType.WindowDoesNotAcceptFocus)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating)
        self.setFixedSize(460, 64)
        self.setCursor(Qt.CursorShape.OpenHandCursor)
        self.setToolTip("Glisse la barre pour la déplacer · double-clic pour la remettre en bas")
        self.spot_file = spot_file
        self.spot = self._read_spot()  # (x, y) entre 0 et 1 ; None : en bas, au milieu
        self._drag: QPoint | None = None
        self._drag_from = QPoint()
        row = QHBoxLayout(self)
        row.setContentsMargins(18, 10, 10, 10)
        row.setSpacing(12)
        mic = QLabel()
        mic.setPixmap(glyph_icon("mic", color="#f87171", size=22).pixmap(22, 22))
        row.addWidget(mic)
        self.level = LevelMeter(horizontal=False, length=30, thickness=5)
        row.addWidget(self.level)
        texts = QVBoxLayout()
        texts.setSpacing(1)
        self.title = QLabel()
        self.title.setStyleSheet('color: #f4f4f5; font-family: "Segoe UI"; font-size: 10.5pt; font-weight: 600;')
        self.detail = QLabel()
        self.detail.setStyleSheet('color: #a1a1aa; font-family: "Segoe UI"; font-size: 9pt;')
        texts.addWidget(self.title)
        texts.addWidget(self.detail)
        row.addLayout(texts, 1)
        stop = QToolButton()
        stop.setIcon(glyph_icon("stop", color="#e8e8ea", size=18))
        stop.setToolTip("Arrêter la dictée")
        stop.setCursor(Qt.CursorShape.ArrowCursor)
        stop.setStyleSheet("QToolButton { background: #27272a; border: none; border-radius: 16px; padding: 7px; }"
                           "QToolButton:hover { background: #3f3f46; }")
        stop.clicked.connect(self.stop_clicked.emit)
        row.addWidget(stop)

    def set_text(self, title: str, detail: str = "") -> None:
        self.title.setText(title)
        self.set_detail(detail)

    def set_detail(self, detail: str) -> None:
        width = self.width() - 150
        self.detail.setText(QFontMetrics(self.detail.font()).elidedText(detail, Qt.TextElideMode.ElideLeft, width))

    def show_bar(self) -> None:
        if not self.isVisible():  # déjà affichée : elle reste où elle est
            screen = QGuiApplication.screenAt(QCursor.pos()) or QGuiApplication.primaryScreen()
            self.move(self.home(screen.availableGeometry()))
        self.show()
        winapi.no_activate(int(self.winId()))

    def home(self, area: QRect) -> QPoint:
        """Sa place sur cet écran : celle où on l'a posée, sinon en bas au milieu."""
        if self.spot is None:
            return QPoint(area.x() + (area.width() - self.width()) // 2, area.bottom() - self.height() - 28)
        fx, fy = self.spot
        return QPoint(area.x() + round((area.width() - self.width()) * fx),
                      area.y() + round((area.height() - self.height()) * fy))

    # --- déplacement
    def mousePressEvent(self, event) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            self._drag = event.globalPosition().toPoint() - self.pos()
            self._drag_from = self.pos()
            self.setCursor(Qt.CursorShape.ClosedHandCursor)

    def mouseMoveEvent(self, event) -> None:
        if self._drag is not None:
            self.move(event.globalPosition().toPoint() - self._drag)

    def mouseReleaseEvent(self, event) -> None:
        if self._drag is None:
            return
        self.move(event.globalPosition().toPoint() - self._drag)  # les derniers mouvements ont pu être regroupés
        self._drag = None
        self.setCursor(Qt.CursorShape.OpenHandCursor)
        if self.pos() != self._drag_from:
            self._settle()

    def mouseDoubleClickEvent(self, event) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            self.spot = None
            self.spot_file.unlink(missing_ok=True)
            screen = QGuiApplication.screenAt(self.geometry().center()) or QGuiApplication.primaryScreen()
            self.move(self.home(screen.availableGeometry()))

    def _settle(self) -> None:
        """Posée : entièrement sur l'écran où elle est, et sa place est retenue pour les prochaines dictées."""
        screen = QGuiApplication.screenAt(self.geometry().center()) or QGuiApplication.primaryScreen()
        area = screen.availableGeometry()
        free_x, free_y = max(1, area.width() - self.width()), max(1, area.height() - self.height())
        fx = min(1.0, max(0.0, (self.x() - area.x()) / free_x))
        fy = min(1.0, max(0.0, (self.y() - area.y()) / free_y))
        self.spot = (round(fx, 4), round(fy, 4))
        self.move(self.home(area))
        try:
            self.spot_file.parent.mkdir(parents=True, exist_ok=True)
            self.spot_file.write_text(json.dumps({"x": self.spot[0], "y": self.spot[1]}), encoding="utf-8")
        except OSError:
            log.warning("Place de la barre de dictée non enregistrée", exc_info=True)

    def _read_spot(self) -> tuple[float, float] | None:
        try:
            data = json.loads(self.spot_file.read_text(encoding="utf-8"))
            return min(1.0, max(0.0, float(data["x"]))), min(1.0, max(0.0, float(data["y"])))
        except (OSError, ValueError, KeyError, TypeError):
            return None

    def paintEvent(self, _event) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setPen(QPen(QColor("#3f3f46"), 1))
        p.setBrush(QColor("#18181b"))
        p.drawRoundedRect(QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5), 22, 22)


# --- la dictée
class Dictation(QObject):
    hotkey = Signal(str)
    engine_message = Signal(object, object)
    flushed = Signal()  # l'enregistreur a envoyé ses dernières phrases

    def __init__(self, app: QApplication):
        super().__init__()
        self.app = app
        self.cfg = Config.load()
        self.engine = EngineProcess(self.engine_message.emit)
        self.engine_message.connect(self._on_engine)
        self.pill = DictationPill()
        self.pill.stop_clicked.connect(self.stop)
        self.recorder: Recorder | None = None
        self.flushing = False
        self.pending = 0  # phrases envoyées au moteur, pas encore écrites
        self.typed = 0
        self.unwritten: list[str] = []
        self.started = self.last_voice = 0.0
        self.loudest = -120.0
        self._lock = threading.Lock()
        self.release_timer = QTimer(self)
        self.release_timer.setSingleShot(True)
        self.release_timer.timeout.connect(self.engine.release)
        self.ticker = QTimer(self)
        self.ticker.setInterval(100)
        self.ticker.timeout.connect(self._tick)
        self.flushed.connect(self._after_flush)
        self.hotkeys = winapi.HotkeyManager(self.hotkey.emit)
        self.hotkey.connect(lambda _name: self.toggle())
        self.tray = QSystemTrayIcon(QIcon(str(APP_ICON)), self)
        menu = QMenu()
        self.dictate_action = menu.addAction("Dicter", self.toggle)
        menu.addAction("Ouvrir Claude Pilot", self._open_app)
        menu.addSeparator()
        menu.addAction("Quitter la dictée", self.quit)
        self.tray.setContextMenu(menu)
        self.tray.activated.connect(
            lambda reason: self.toggle() if reason == QSystemTrayIcon.ActivationReason.Trigger else None)

    def start(self) -> None:
        self.hotkeys.start()
        self.tray.show()
        self.reload()
        log.info("Dictée prête : %s", self.cfg.hotkey_dictation)

    def reload(self) -> None:
        self.cfg = Config.load()
        sounds.prepare(self.cfg.sound_volume)
        hotkey = winapi.display_hotkey(self.cfg.hotkey_dictation)
        if not self.hotkeys.register("dictate", self.cfg.hotkey_dictation):
            self.tray.showMessage("Claude Pilot", f"{hotkey} est déjà pris par une autre appli : change-le dans "
                                                  "Claude Pilot → Réglages → Dictée.")
        self.dictate_action.setText(f"Dicter ({hotkey})")
        self.tray.setToolTip(f"Dictée Claude Pilot · {hotkey}")
        if self.cfg.dictation_keep_min == 0:
            self.release_timer.stop()
            self.engine.start()  # toujours prêt

    def command(self, text: str) -> None:
        {"reload": self.reload, "quit": self.quit, "toggle": self.toggle}.get(text, lambda: None)()

    # --- dicter
    def toggle(self) -> None:
        if self.recorder is not None:
            self.stop()
        else:
            self.begin()

    def begin(self) -> None:
        if self.flushing:
            return
        self.cfg = Config.load()  # micro ou moteur changés dans Claude Pilot entre-temps
        self.release_timer.stop()
        try:
            refresh_devices()  # Windows a pu changer de micro depuis la dernière dictée
            vad = StreamingVAD()
            recorder = Recorder(self.cfg.microphone)
            recorder.on_audio = lambda pcm, start: self._on_audio(recorder, vad, pcm, start)
            recorder.on_boundary = lambda position: [self._submit(recorder, s, e) for s, e in vad.flush(position)]
            self.engine.start()
            recorder.start(None)
        except Exception as exc:
            log.exception("Dictée impossible")
            self._problem("Dictée impossible", f"micro inaccessible : {exc}")
            return
        self.recorder, self.typed, self.unwritten = recorder, 0, []
        self.started = self.last_voice = time.monotonic()
        self.loudest = -120.0
        self.pill.set_text("Je t'écoute…" if self.engine.ready else "Je t'écoute… (le moteur se prépare)",
                           "Parle : chaque phrase s'écrit là où est ton curseur.")
        self.pill.show_bar()
        self.ticker.start()
        self._sound("start")

    def stop(self) -> None:
        recorder, self.recorder = self.recorder, None
        if recorder is None:
            return
        self.ticker.stop()
        self.flushing = True
        self.pill.set_text("Fin de la dictée…", self.pill.detail.text())
        self._sound("stop")

        def work():
            try:
                recorder.stop()  # la dernière phrase part au moteur
            finally:
                self.flushed.emit()

        threading.Thread(target=work, name="dictee-arret", daemon=True).start()

    def _on_audio(self, recorder: Recorder, vad: StreamingVAD, pcm: np.ndarray, start: int) -> None:
        for s, e in vad.feed(pcm, start):
            self._submit(recorder, s, e)
        if vad.last_prob > 0.5:
            self.last_voice = time.monotonic()

    def _submit(self, recorder: Recorder, start: int, end: int) -> None:
        with self._lock:
            self.pending += 1
        self.engine.submit(recorder.get(start, end))

    def _tick(self) -> None:
        recorder = self.recorder
        if recorder is None:
            return
        self.pill.level.set_db(recorder.level_db)
        self.loudest = max(self.loudest, recorder.level_db)
        now = time.monotonic()
        if now - self.started > 4.0 and self.loudest < -80.0:
            self.pill.set_detail("Le micro ne capte rien : choisis-en un autre dans Claude Pilot.")
        if now - self.last_voice > SILENCE_STOP_S:
            self.stop()

    def _on_engine(self, message: dict, proc) -> None:
        current = proc is self.engine.proc
        if "ready" in message:
            if current and self.recorder is not None:
                self.pill.set_text("Je t'écoute…", self.pill.detail.text())
        elif "text" in message:
            with self._lock:
                self.pending = max(0, self.pending - 1)
            if message["text"]:
                self._write(message["text"])
            self._maybe_finish()
        elif "error" in message:
            log.warning("Moteur de la dictée : %s", message["error"])
            self._problem("Moteur indisponible", message["error"])
        elif "exit" in message and current:
            self.engine.proc = None
            if self.pending or self.recorder is not None:
                with self._lock:
                    self.pending = 0
                self._problem("Le moteur s'est arrêté", "Relance la dictée avec le raccourci.")

    def _write(self, text: str) -> None:
        chunk = text if self.typed == 0 and not self.unwritten else " " + text
        if winapi.type_text(chunk):
            self.typed += 1
            self.pill.set_detail(text)
            return
        self.unwritten.append(text)  # fenêtre qui refuse le clavier (appli en administrateur…) : presse-papiers
        QGuiApplication.clipboard().setText(" ".join(self.unwritten))
        self.pill.set_text("Impossible d'écrire dans cette fenêtre", "Texte copié : colle-le avec Ctrl+V.")

    def _after_flush(self) -> None:
        self.flushing = False
        self._maybe_finish()

    def _maybe_finish(self) -> None:
        if self.recorder is not None or self.flushing or self.pending:
            return
        QTimer.singleShot(900, lambda: self.pill.hide() if self.recorder is None else None)
        if self.cfg.dictation_keep_min > 0:
            self.release_timer.start(self.cfg.dictation_keep_min * 60_000)

    def _problem(self, title: str, detail: str) -> None:
        recorder, self.recorder = self.recorder, None
        if recorder is not None:
            self.ticker.stop()
            threading.Thread(target=recorder.stop, daemon=True).start()
        self.pill.set_text(title, detail)
        self.pill.show_bar()
        self._sound("error")
        QTimer.singleShot(6000, lambda: self.pill.hide() if self.recorder is None else None)

    def _sound(self, name: str) -> None:
        if self.cfg.sounds:
            sounds.play(name, self.cfg.sound_volume)

    def _open_app(self) -> None:
        subprocess.Popen([str(_pythonw()), "-m", "claudepilot"], cwd=str(APP_DIR), close_fds=True)

    def quit(self) -> None:
        if self.recorder is not None:
            self.recorder.stop()
            self.recorder = None
        self.engine.release()
        self.hotkeys.stop()
        self.tray.hide()
        self.app.quit()


def main() -> int:
    from .__main__ import setup_logging

    setup_logging("dictee.log")
    if str(APP_DIR) not in sys.path:
        sys.path.insert(0, str(APP_DIR))
    os.chdir(tempfile.gettempdir())  # le dossier app\ reste libre : une mise à jour peut le remplacer
    winapi.set_app_id("ClaudePilot.Dictee")
    app = QApplication(sys.argv)
    app.setApplicationName("Claude Pilot Dictée")
    app.setQuitOnLastWindowClosed(False)

    command = sys.argv[1] if len(sys.argv) > 1 else "reload"
    if send_command(command) or command == "quit":  # déjà lancée : c'est elle qui obéit
        return 0
    QLocalServer.removeServer(SERVER)
    server = QLocalServer()
    server.listen(SERVER)
    dictation = Dictation(app)

    def on_connection() -> None:
        connection = server.nextPendingConnection()
        if connection is None:
            return
        connection.waitForReadyRead(300)
        dictation.command(bytes(connection.readAll()).decode(errors="ignore").strip())
        connection.disconnectFromServer()

    server.newConnection.connect(on_connection)
    PID_FILE.parent.mkdir(parents=True, exist_ok=True)
    PID_FILE.write_text(str(os.getpid()), encoding="utf-8")
    dictation.start()
    if command == "toggle":
        dictation.toggle()
    try:
        return app.exec()
    finally:
        PID_FILE.unlink(missing_ok=True)


if __name__ == "__main__":
    sys.exit(main())
