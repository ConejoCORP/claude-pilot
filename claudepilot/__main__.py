"""Lancement : python -m claudepilot (ou pythonw, sans console)."""
from __future__ import annotations

import logging
import os
import sys
import threading
from logging.handlers import RotatingFileHandler

from .config import LOG_DIR


def setup_logging(filename: str = "claudepilot.log") -> None:
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    if sys.stderr is None:  # pythonw : les barres de progression des librairies écriraient dans le vide
        sys.stderr = open(os.devnull, "w", encoding="utf-8")
    if sys.stdout is None:
        sys.stdout = open(os.devnull, "w", encoding="utf-8")
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s [%(threadName)s] %(name)s : %(message)s",
        handlers=[
            RotatingFileHandler(LOG_DIR / filename, maxBytes=2_000_000, backupCount=3, encoding="utf-8"),
            logging.StreamHandler(sys.stderr),
        ],
    )
    log = logging.getLogger("claudepilot")
    sys.excepthook = lambda t, v, tb: log.critical("Erreur non gérée", exc_info=(t, v, tb))
    threading.excepthook = lambda a: log.critical(
        "Erreur non gérée dans %s", a.thread.name if a.thread else "?", exc_info=(a.exc_type, a.exc_value, a.exc_traceback)
    )


def main() -> int:
    setup_logging()
    import getpass
    from pathlib import Path

    from PySide6.QtGui import QIcon
    from PySide6.QtNetwork import QLocalServer, QLocalSocket
    from PySide6.QtWidgets import QApplication

    from . import winapi
    from .app import Controller

    winapi.set_app_id("ClaudePilot.App")
    app = QApplication(sys.argv)
    app.setApplicationName("Claude Pilot")
    app.setWindowIcon(QIcon(str(Path(__file__).parent / "assets" / "icon.ico")))
    app.setQuitOnLastWindowClosed(False)

    # Une seule instance : relancer Claude Pilot ramène simplement la fenêtre déjà ouverte.
    name = f"ClaudePilot-{getpass.getuser()}"
    probe = QLocalSocket()
    probe.connectToServer(name)
    if probe.waitForConnected(300):
        probe.write(b"show")
        probe.waitForBytesWritten(300)
        return 0
    QLocalServer.removeServer(name)
    server = QLocalServer()
    server.listen(name)

    controller = Controller(app)

    def bring_back() -> None:
        connection = server.nextPendingConnection()
        if connection is not None:
            connection.disconnectFromServer()
        controller.window.bring_to_front()

    server.newConnection.connect(bring_back)
    controller.start()
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
