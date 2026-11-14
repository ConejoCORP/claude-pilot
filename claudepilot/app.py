"""Chef d'orchestre : la fenêtre, l'enregistrement, les captures, la transcription et l'export."""
from __future__ import annotations

import json
import logging
import os
import subprocess
import sys
import threading
from collections import Counter
from concurrent.futures import Future, ThreadPoolExecutor
from datetime import datetime
from pathlib import Path

from PIL import Image
from PySide6.QtCore import QFile, QMimeData, QObject, QTimer, QUrl, Signal
from PySide6.QtGui import QGuiApplication, QImage
from PySide6.QtWidgets import QApplication, QDialog, QMessageBox

from . import APP_VERSION, claude_code, importer, models, sounds, triggers, winapi
from .captures import VoiceCaptures
from .audio import MicMonitor, Recorder, input_devices
from .config import ENGINE_LABELS, Config
from .engines import EngineManager
from .export import (
    LATEST_NAME, RETOUR_NAME, ExportResult, claude_code_prompt, compute_layout, export_session, exported_images,
    make_zip, render_markdown,
)
from .model import Capture, Utterance, fmt_duration, fmt_time
from .screen import Frame, ScreenService, render_capture
from .session import JOURNAL, STATE, Session
from .transcriber import TranscriptionWorker, compare_engines, retranscribe
from .settings_dialog import SettingsDialog
from .ui import Overlay
from .vad import StreamingVAD
from .updater import Updater
from .window import MainWindow

log = logging.getLogger(__name__)


def to_qimage(image: Image.Image) -> QImage:
    data = image.convert("RGB").tobytes("raw", "RGB")
    return QImage(data, image.width, image.height, image.width * 3, QImage.Format.Format_RGB888).copy()


def _short(name: str, size: int = 18) -> str:
    return name if len(name) <= size else f"{name[:size // 2]}…{name[-(size - size // 2 - 1):]}"


def describe(folder: Path) -> dict:
    """Une ligne de l'historique : date (et fichier importé), durée, nombre de captures."""
    try:
        started = datetime.strptime(folder.name[:19], "%Y-%m-%d_%H-%M-%S")
        title = f"{started:%d/%m} · {started:%H:%M}"
    except ValueError:
        title = folder.name
    if not (folder / RETOUR_NAME).exists():
        return {"folder": folder, "title": title, "subtitle": "interrompu · à récupérer", "unfinished": True}
    try:
        data = json.loads((folder / STATE).read_text(encoding="utf-8"))
        count = sum(1 for c in data.get("captures", []) if not c.get("cancelled"))
        subtitle = f"{fmt_duration(data.get('duration', 0))} · {count} capture{'s' if count > 1 else ''}"
        if data.get("source"):
            title = f"{title} · {_short(data['source'])}"
    except (OSError, ValueError):
        count = len(exported_images(folder))
        subtitle = f"{count} capture{'s' if count > 1 else ''}"
    return {"folder": folder, "title": title, "subtitle": subtitle, "unfinished": False}


class Controller(QObject):
    hotkey = Signal(str)
    utterance_ready = Signal(object)
    call = Signal(object)  # exécute une fonction dans le fil de l'interface

    def __init__(self, app: QApplication):
        super().__init__()
        self.app = app
        self.cfg = Config.load()
        self.engines = EngineManager(self.cfg.device)
        self.state = "idle"  # idle, recording, paused, finishing
        self.session: Session | None = None
        self.session_engine = self.cfg.engine
        self.recorder: Recorder | None = None
        self.worker: TranscriptionWorker | None = None
        self.screen: ScreenService | None = None
        self.last_result: ExportResult | None = None
        self.selected: Path | None = None
        self._saves: list[Future] = []
        self.voice: VoiceCaptures | None = None  # captures dites à voix haute pendant l'enregistrement
        self._png = ThreadPoolExecutor(max_workers=2, thread_name_prefix="png")
        self._task: str | None = None
        self._quit_after = False
        self._engine_shown = ""
        self._loudest = -120.0  # niveau le plus fort entendu depuis le début de l'enregistrement
        self._mic_warned = False
        self._live_dirty = False
        self._mic_monitor: MicMonitor | None = None

        self.overlay = Overlay()
        self.overlay.place(self.cfg.overlay_pos)
        self.overlay.capture_clicked.connect(lambda: self.capture("bouton"))
        self.overlay.pause_clicked.connect(self.toggle_pause)
        self.overlay.stop_clicked.connect(self.stop_recording)
        self.overlay.copy_clicked.connect(self.copy_prompt)
        self.overlay.open_clicked.connect(self.open_selected_folder)
        self.overlay.moved.connect(self._overlay_moved)

        w = self.window = MainWindow()
        w.record_clicked.connect(self.toggle_recording)
        w.capture_clicked.connect(lambda: self.capture("bouton"))
        w.pause_clicked.connect(self.toggle_pause)
        w.engine_changed.connect(self.set_engine)
        w.mic_changed.connect(self._mic_changed)
        w.mic_test_toggled.connect(self._mic_test)
        w.settings_clicked.connect(self.open_settings)
        w.session_selected.connect(self._select)
        w.copy_clicked.connect(self.copy_prompt)
        w.open_clicked.connect(self.open_selected_folder)
        w.delete_clicked.connect(self.delete_selected)
        w.recover_clicked.connect(self.recover_selected)
        w.zip_clicked.connect(self.zip_selected)
        w.import_requested.connect(self.import_file)
        w.close_requested.connect(self.quit)
        w.live_image = self._live_image
        self.updater = Updater(self.window, self.cfg, self.force_quit)

        self.hotkeys = winapi.HotkeyManager(self.hotkey.emit)
        self.hotkey.connect(self._on_hotkey)
        self.utterance_ready.connect(self._on_utterance)
        self.call.connect(lambda fn: fn())
        self.ticker = QTimer(self)
        self.ticker.setInterval(150)
        self.ticker.timeout.connect(self._tick)

    # --- démarrage
    def start(self) -> None:
        self.cfg.retours_path.mkdir(parents=True, exist_ok=True)
        sounds.prepare(self.cfg.sound_volume)
        self.hotkeys.start()
        ok = self.hotkeys.register("record", self.cfg.hotkey_record)
        self.window.init_controls(self.cfg, input_devices())
        self.window.set_mode("idle", self.cfg)
        self.refresh_history(select_latest=True)
        self.window.show()
        self._update_engine_status()
        self._preload(self.cfg.engine)
        self.ticker.start()
        if not ok:
            record = winapi.display_hotkey(self.cfg.hotkey_record)
            self.window.notify(f"Le raccourci {record} est déjà pris par une autre appli : change-le dans les réglages.",
                               error=True)
        elif "--updated" in sys.argv:
            self.window.notify(f"Claude Pilot est à jour : version {APP_VERSION}")
        QTimer.singleShot(2500, self.updater.check_on_startup)

    def run(self, fn) -> None:
        self.call.emit(fn)

    def _on_hotkey(self, name: str) -> None:
        if name == "record":
            self.toggle_recording()
        elif name == "capture":
            self.capture("raccourci")

    # --- moteur et micro
    def _preload(self, key: str) -> None:
        """Charge un moteur en arrière-plan ; le télécharge d'abord s'il manque (avec le pourcentage affiché)."""
        if models.is_downloaded(key):
            self.engines.preload(key, self._engine_loaded)
            return

        def work():
            try:
                models.download(key, lambda done, total: self.run(lambda d=done, t=total: self._download_progress(key, d, t)))
            except Exception as exc:
                log.exception("Téléchargement du moteur %s impossible", key)
                self._engine_loaded(key, f"téléchargement impossible ({exc})")
                return
            self.engines.preload(key, self._engine_loaded)

        threading.Thread(target=work, name=f"telechargement-{key}", daemon=True).start()

    def _download_progress(self, key: str, done: int, total: int) -> None:
        if key == self.cfg.engine:
            self.window.set_engine_status(f"téléchargement {done * 100 // max(total, 1)} %", "busy")

    def _engine_loaded(self, key: str, error: str | None) -> None:
        self.run(lambda: self._engine_done(key, error))

    def _engine_done(self, key: str, error: str | None) -> None:
        self._update_engine_status()
        if error:
            self._fail(f"{ENGINE_LABELS[key]} indisponible : {error}")
            return
        if key == self.cfg.engine:
            self._sound("engine")
            if self.state == "idle" and self._task is None:
                self._unload_others()

    def _update_engine_status(self) -> None:
        key = self.cfg.engine
        engine = self.engines.loaded(key)
        if engine is not None:
            self.window.set_engine_status(f"prêt · {'carte graphique' if engine.device == 'cuda' else 'processeur'}", "ok")
        elif self.engines.error(key):
            self.window.set_engine_status("indisponible", "error", self.engines.error(key))
        else:
            self.window.set_engine_status("chargement…", "busy")

    def set_engine(self, key: str) -> None:
        if key == self.cfg.engine or self.live:
            return
        self.cfg.engine = key
        self.cfg.save()
        self._update_engine_status()
        self._preload(key)

    def _mic_changed(self, name: str) -> None:
        self.cfg.microphone = name
        self.cfg.save()
        self.window.notify(f"Micro : {name or 'micro par défaut de Windows'}")
        if self._mic_monitor is not None:
            self._mic_test(True)  # on réécoute le nouveau micro

    def _mic_test(self, on: bool) -> None:
        if self._mic_monitor is not None:
            try:
                self._mic_monitor.close()
            except Exception:
                log.debug("Arrêt du test micro", exc_info=True)
            self._mic_monitor = None
        if on:
            try:
                self._mic_monitor = MicMonitor(self.cfg.microphone)
            except Exception as exc:
                on = False
                self._fail(f"Micro inaccessible : {exc}")
        self.window.set_mic_test(on)

    # --- enregistrement
    @property
    def live(self) -> bool:
        return self.state in ("recording", "paused")

    def toggle_recording(self) -> None:
        if self.live:
            self.stop_recording()
        elif self.state == "idle":
            self.start_recording()

    def start_recording(self) -> None:
        if self.state != "idle" or self._task is not None:
            return
        self._mic_test(False)
        try:
            session = Session.create(self.cfg.retours_path)
        except OSError as exc:
            self._fail(f"Dossier des retours inaccessible : {exc}")
            return
        self.session, self.session_engine, self._saves = session, self.cfg.engine, []
        self.voice = VoiceCaptures(session, self._frame_for, self._store)
        try:
            vad = StreamingVAD()
            recorder = Recorder(self.cfg.microphone)
            worker = TranscriptionWorker(
                self.engines, lambda: self.session_engine, recorder.get,
                lambda: list(self.cfg.lexicon), self.utterance_ready.emit,
            )

            def on_audio(pcm, start):
                for s, e in vad.feed(pcm, start):
                    worker.submit(s, e)

            def on_boundary(position):
                for s, e in vad.flush(position):
                    worker.submit(s, e)

            recorder.on_audio, recorder.on_boundary = on_audio, on_boundary
            self.recorder, self.worker = recorder, worker
            worker.start()
            self.screen = ScreenService(recorder.now, self.cfg.ring_fps, self.cfg.ring_seconds, self._avoid_rects)
            self.screen.start()
            recorder.start(session.audio_path)
        except Exception as exc:
            log.exception("Démarrage de l'enregistrement impossible")
            self._teardown()
            self.session = None
            self._fail(f"Enregistrement impossible : {exc}")
            return
        self.state = "recording"
        if not self.hotkeys.register("capture", self.cfg.hotkey_capture):
            self.window.notify(f"{winapi.display_hotkey(self.cfg.hotkey_capture)} est déjà pris par une autre appli",
                               error=True)
        self._engine_shown = ""
        self._loudest, self._mic_warned = -120.0, False
        self.overlay.show_recording(self._engine_label(), self.cfg.hotkey_capture)
        self.window.set_mode("recording", self.cfg)
        self.window.set_actions("busy")
        self.window.clear_live_images()
        self._live_dirty = True
        self._sound("start")
        if self.engines.loaded(self.session_engine) is None:
            self._preload(self.session_engine)

    def toggle_pause(self) -> None:
        if self.state == "recording":
            self.recorder.pause()
            self.state = "paused"
            self.session.log("pause", t=self.recorder.now())
            self.overlay.show_paused()
            self.window.set_mode("paused", self.cfg)
            self._sound("pause")
        elif self.state == "paused":
            self.recorder.resume()
            self.state = "recording"
            self.session.log("resume", t=self.recorder.now())
            self.overlay.show_recording(self._engine_label(), self.cfg.hotkey_capture)
            self.window.set_mode("recording", self.cfg)
            self._sound("resume")

    def capture(self, source: str) -> None:
        if not self.live or self.screen is None:
            return
        frame = self.screen.capture()
        if frame is None:
            self._fail("Capture d'écran impossible")
            return
        self.voice.remember(self._store(frame, source, ""), frame)

    def _frame_for(self, t: float) -> Frame | None:
        """Image de la boîte noire à ce moment, sinon capture immédiate."""
        frame = self.screen.frame_at(t) if self.screen else None
        if (frame is None or abs(frame.t - t) > 2.0) and self.live and self.screen:
            frame = self.screen.capture()
        return frame

    def _avoid_rects(self) -> list[tuple[int, int, int, int]]:
        """Fenêtres de Claude Pilot à ignorer pour viser l'écran (appelé depuis le fil de capture)."""
        return [r for r in (self.overlay.physical_rect(), self.window.physical_rect()) if r]

    def _store(self, frame: Frame, source: str, trigger: str) -> str:
        session = self.session
        name = f"{len(session.captures) + 1:03d}_{source}_{frame.t:07.2f}s.png"
        path = session.raw_dir / name
        label = f"{fmt_time(frame.t)} · écran {frame.monitor.number}"
        cursor = frame.cursor if self.cfg.cursor_ring else None

        def save() -> None:
            image = frame.image()
            image.save(path, "PNG", compress_level=1)
            qimage = to_qimage(render_capture(image, label, cursor, 900))
            self.run(lambda: self._thumbnail_ready(name, qimage))

        self._saves.append(self._png.submit(save))
        session.add_capture(Capture(
            round(frame.t, 3), source, name, frame.monitor.number,
            (frame.monitor.width, frame.monitor.height), frame.cursor, trigger,
        ))
        self._live_dirty = True
        self._sound("capture")
        self.overlay.flash()
        return name

    def _thumbnail_ready(self, name: str, image: QImage) -> None:
        self.window.add_live_image(f"cap:{name}", image)
        self._live_dirty = True

    def _on_utterance(self, utt: Utterance) -> None:
        session = self.session
        if session is None:
            return
        session.add_utterance(utt)
        self._live_dirty = True
        if self.live and utt.words:
            self.overlay.set_caption(utt.text)
        acted = False
        for action in triggers.detect(utt.words, self.cfg.triggers, self.cfg.triggers_kept, self.cfg.voice_commands):
            if action.kind == "capture":
                acted |= self.voice.capture(action)
            elif action.kind == "cancel":
                cancelled = session.cancel_last_capture(action.t)
                if cancelled is not None:
                    acted = True
                    self._sound("cancel")
                    if self.live:
                        self.overlay.set_caption(f"Capture de {fmt_time(cancelled.t)} annulée")
            elif action.kind == "point":
                acted = True
                self._sound("point")
        if not acted and utt.words and self.live and self.cfg.sound_each_phrase:
            self._sound("phrase")

    def _live_image(self, name: str) -> QImage | None:
        """Capture de l'enregistrement en cours, en grand (l'aperçu n'en garde qu'une réduction)."""
        session = self.session
        cap = next((c for c in session.captures if c.raw_file == name), None) if session else None
        if cap is None:
            return None
        try:
            with Image.open(session.raw_dir / name) as raw:
                cursor = cap.cursor if self.cfg.cursor_ring else None
                return to_qimage(render_capture(raw, f"{fmt_time(cap.t)} · {cap.where}", cursor, 2560))
        except Exception:
            log.debug("Capture %s pas encore enregistrée", name, exc_info=True)
            return None

    def stop_recording(self) -> None:
        if not self.live:
            return
        self.state = "finishing"
        self.hotkeys.unregister("capture")
        self.overlay.show_busy("Transcription des dernières phrases…")
        self.window.set_mode("finishing", self.cfg)
        self._sound("stop")
        recorder, worker, screen = self.recorder, self.worker, self.screen

        def work():
            duration = recorder.stop()
            if not worker.wait_idle(180):
                log.warning("Transcription toujours en cours après 3 minutes")
            worker.stop()
            screen.stop()
            self.run(lambda: self._finish(duration))

        threading.Thread(target=work, name="arret", daemon=True).start()

    def _finish(self, duration: float) -> None:
        session = self.session
        labels = Counter(u.engine for u in session.utterances if u.engine and not u.engine.startswith("erreur"))
        session.finish(duration, labels.most_common(1)[0][0] if labels else ENGINE_LABELS[self.session_engine])
        saves = list(self._saves)
        self._teardown()

        def work():
            for future in saves:
                try:
                    future.result(30)
                except Exception:
                    log.exception("Capture non enregistrée")
            try:
                result = export_session(session, self.cfg)
            except Exception as exc:
                log.exception("Export impossible")
                self.run(lambda e=exc: self._export_failed(session, e))
                return
            self.run(lambda: self._exported(session, result))

        threading.Thread(target=work, name="export", daemon=True).start()

    def _exported(self, session: Session, result: ExportResult) -> None:
        self.last_result = result
        self.session, self.state = None, "idle"
        self.window.set_mode("idle", self.cfg)
        self.refresh_history(select=session.folder)
        count = len(result.images)
        plural = "s" if count > 1 else ""
        if not any(u.words for u in session.utterances):
            text = f"Aucune parole entendue ({count} capture{plural}) : vérifie le micro en haut de la fenêtre"
            self.overlay.show_ready(text)
            self.window.notify(text, error=True)
        elif self.cfg.copy_prompt_on_stop:
            QGuiApplication.clipboard().setText(result.prompt)
            text = f"Retour prêt ({count} capture{plural}) · message copié : Ctrl+V dans Claude Code"
            self.overlay.show_ready(text)
            self.window.notify(text)
        else:
            self.overlay.show_ready(f"Retour prêt ({count} capture{plural})")
        self._sound("ready")
        if self._quit_after:
            self.quit()

    def _export_failed(self, session: Session, exc: Exception) -> None:
        self.session, self.state = None, "idle"
        self.window.set_mode("idle", self.cfg)
        self.refresh_history(select=session.folder)
        self._fail(f"Retour non généré ({exc}) : l'audio et les captures sont gardés, clique sur Récupérer")

    def _teardown(self) -> None:
        for stop in (
            lambda: self.recorder and self.recorder.stop(),
            lambda: self.worker and self.worker.stop(),
            lambda: self.screen and self.screen.stop(),
        ):
            if self.state != "finishing":
                try:
                    stop()
                except Exception:
                    log.debug("Arrêt partiel", exc_info=True)
        self.recorder = self.worker = self.screen = None

    def _tick(self) -> None:
        if self._mic_monitor is not None:
            self.window.mic_level.set_db(self._mic_monitor.level_db)
        if self.session is None:
            return
        if self.recorder is not None:
            count = sum(1 for c in self.session.captures if not c.cancelled)
            level = self.recorder.level_db if self.state == "recording" else -90.0
            now = self.recorder.now()
            self.overlay.update_live(now, count, level)
            self.window.update_live(now, count, level)
            self._loudest = max(self._loudest, level)
            if self.state == "recording" and not self._mic_warned and now > 8.0 and self._loudest < -80.0:
                self._mic_warned = True  # silence numérique : mauvais micro, ou micro coupé
                name = self.cfg.microphone or "micro par défaut de Windows"
                self._fail(f"Le micro ({name}) ne capte rien : choisis-en un autre en haut de la fenêtre")
            label = self._engine_label()
            if label != self._engine_shown:
                self._engine_shown = label
                self.overlay.engine.setText(label)
        if self._live_dirty and self.state in ("recording", "paused", "finishing"):
            self._live_dirty = False
            self._render_live()

    def _render_live(self) -> None:
        session = self.session
        if session is None:
            return
        captures = sorted((c for c in session.captures if not c.cancelled), key=lambda c: c.t)
        names = [c.raw_file for c in captures]
        layout = compute_layout(session.utterances, captures, self.cfg.triggers, self.cfg.triggers_kept,
                                self.cfg.voice_commands)
        duration = self.recorder.now() if self.recorder is not None else session.duration
        md = render_markdown(session.utterances, captures, names, layout, session.started, duration,
                             self._engine_label(), session.folder, image_ref=lambda n: f"cap:{names[n - 1]}",
                             cursor_ring=self.cfg.cursor_ring)
        self.window.show_live(md)

    def _engine_label(self) -> str:
        engine = self.engines.loaded(self.session_engine)
        return engine.label if engine else f"{ENGINE_LABELS[self.session_engine]} (chargement…)"

    # --- historique et retour sélectionné
    def refresh_history(self, select: Path | None = None, select_latest: bool = False) -> None:
        root = self.cfg.retours_path
        folders = []
        if root.exists():
            folders = sorted(
                (p for p in root.iterdir() if p.is_dir() and ((p / RETOUR_NAME).exists() or (p / JOURNAL).exists())),
                key=lambda p: p.name, reverse=True,
            )
        current = self.session.folder if self.session is not None else None
        entries = [describe(f) for f in folders if f != current]
        target = select if select is not None else (entries[0]["folder"] if select_latest and entries else self.selected)
        if target is not None and not any(e["folder"] == target for e in entries):
            target = None
        self.window.set_history(entries, target)
        self._select(target)

    def _select(self, folder: Path | None) -> None:
        self.selected = folder
        if self.live or self.state == "finishing":
            return
        if folder is None or not folder.exists():
            self.window.show_welcome(self.cfg)
            self.window.set_actions(None)
            return
        if not (folder / RETOUR_NAME).exists():
            self.window.show_unfinished()
            self.window.set_actions("unfinished" if self._task is None else "busy")
            return
        try:
            md = (folder / RETOUR_NAME).read_text(encoding="utf-8")
        except OSError as exc:
            self._fail(f"Retour illisible : {exc}")
            return
        self.window.show_retour(folder, md)
        self.window.set_actions("ready" if self._task is None else "busy")

    def last_folder(self) -> Path | None:
        if self.selected is not None and (self.selected / RETOUR_NAME).exists():
            return self.selected
        root = self.cfg.retours_path
        done = [p for p in root.iterdir() if p.is_dir() and (p / RETOUR_NAME).exists()] if root.exists() else []
        return max(done, key=lambda p: p.name) if done else None

    def copy_prompt(self) -> None:
        folder = self.last_folder()
        if folder is None:
            return
        QGuiApplication.clipboard().setText(claude_code_prompt(folder / RETOUR_NAME, len(exported_images(folder))))
        self._sound("copy")
        self._message("Message copié : Ctrl+V dans Claude Code")

    def open_selected_folder(self) -> None:
        folder = self.last_folder()
        if folder is not None:
            os.startfile(folder)

    def zip_selected(self) -> None:
        """Zip du retour, copié comme un fichier : Ctrl+V dans un chat, un mail ou un dossier."""
        folder = self.last_folder()
        if folder is None:
            return
        try:
            path = make_zip(folder)
        except OSError as exc:
            self._fail(f"Zip impossible : {exc}")
            return
        mime = QMimeData()
        mime.setUrls([QUrl.fromLocalFile(str(path))])
        QGuiApplication.clipboard().setMimeData(mime)
        subprocess.Popen(f'explorer /select,"{path}"')
        self._sound("copy")
        self.window.notify(f"Zip créé et copié : Ctrl+V pour le coller dans un chat ou un dossier · {path.name}")

    def import_file(self, path: Path) -> None:
        """Transcrit un audio ou une vidéo déjà enregistré, et en fait un retour comme les autres."""
        if not importer.is_media(path):
            self._fail(f"{path.name} n'est pas un fichier audio ou vidéo")
            return
        if not self._begin_task(f"Import de {path.name}…"):
            return
        title = f"Import de « {path.name} »"
        self.window.show_busy(title, "Chargement du moteur de transcription…")

        def progress(text: str) -> None:
            self.run(lambda t=text: self.window.show_busy(title, t) if self._task else None)

        def work():
            try:
                engine = self.engines.get(self.cfg.engine)
                session = importer.import_media(path, self.cfg.retours_path, engine, self.cfg, progress)
                progress("Préparation du retour…")
                result = export_session(session, self.cfg)

                def done():
                    self.last_result = result
                    count = len(result.images)
                    text = f"{path.name} transcrit ({count} capture{'s' if count > 1 else ''})"
                    if self.cfg.copy_prompt_on_stop:
                        QGuiApplication.clipboard().setText(result.prompt)
                        text += " · message copié : Ctrl+V dans Claude Code"
                    self._end_task(text, select=session.folder)

                self.run(done)
            except Exception as exc:
                log.exception("Import impossible : %s", path)
                self.run(lambda e=exc: self._end_task(f"Import de {path.name} impossible : {e}", error=True))

        threading.Thread(target=work, name="import", daemon=True).start()

    def compare_selected(self) -> None:
        folder = self.selected
        if folder is None or not (folder / RETOUR_NAME).exists() or not self._begin_task("Comparaison des 3 moteurs…"):
            return

        def work():
            try:
                session = Session.load(folder)
                path = compare_engines(
                    session, self.engines, list(ENGINE_LABELS), list(self.cfg.lexicon),
                    lambda text: self.run(lambda t=text: self.window.notify(t)),
                )
                self._unload_others()
                md = path.read_text(encoding="utf-8")
                self.run(lambda: self._end_task(
                    "Comparaison prête (enregistrée dans comparaison.md) · reclique sur le retour pour le revoir",
                    select=folder, markdown=md,
                ))
            except Exception as exc:
                log.exception("Comparaison impossible")
                self.run(lambda e=exc: self._end_task(f"Comparaison impossible : {e}", error=True))

        threading.Thread(target=work, name="comparaison", daemon=True).start()

    def retranscribe_selected(self, key: str) -> None:
        folder = self.selected
        if folder is None or not (folder / RETOUR_NAME).exists():
            return
        if not self._begin_task(f"Retranscription avec {ENGINE_LABELS[key]}…"):
            return

        def work():
            try:
                session = Session.load(folder)
                engine = self.engines.get(key)
                retranscribe(session, engine, list(self.cfg.lexicon))
                result = export_session(session, self.cfg)
                self._unload_others()

                def done():
                    self.last_result = result
                    if self.cfg.copy_prompt_on_stop:
                        QGuiApplication.clipboard().setText(result.prompt)
                    self._end_task(f"Retranscrit avec {engine.label} · message copié pour Claude Code", select=folder)

                self.run(done)
            except Exception as exc:
                log.exception("Retranscription impossible")
                self.run(lambda e=exc: self._end_task(f"Retranscription impossible : {e}", error=True))

        threading.Thread(target=work, name="retranscription", daemon=True).start()

    def recover_selected(self) -> None:
        folder = self.selected
        if folder is None or (folder / RETOUR_NAME).exists():
            return
        if not self._begin_task("Récupération de l'enregistrement…"):
            return

        def work():
            try:
                session = Session.load(folder)
                session.utterances = []  # le journal peut s'arrêter au milieu : on retranscrit tout l'audio
                retranscribe(session, self.engines.get(self.cfg.engine), list(self.cfg.lexicon))
                export_session(session, self.cfg)
                self.run(lambda: self._end_task("Enregistrement récupéré", select=folder))
            except Exception as exc:
                log.exception("Récupération impossible : %s", folder)
                self.run(lambda e=exc: self._end_task(f"Récupération impossible : {e}", error=True))

        threading.Thread(target=work, name="recuperation", daemon=True).start()

    def delete_selected(self) -> None:
        folder = self.selected
        if folder is None or self.state != "idle" or self._task is not None:
            return
        info = describe(folder)
        answer = QMessageBox.question(
            self.window, "Supprimer ce retour", f"Mettre le retour du {info['title']} à la corbeille ?\n\n{folder}"
        )
        if answer != QMessageBox.StandardButton.Yes:
            return
        result = QFile.moveToTrash(str(folder))
        if not (result[0] if isinstance(result, tuple) else bool(result)):
            self._fail("Impossible de le mettre à la corbeille (un de ses fichiers est peut-être ouvert)")
            return
        self.selected = None
        self._refresh_latest_pointer(folder)
        self.refresh_history(select_latest=True)
        self._sound("cancel")
        self.window.notify("Retour mis à la corbeille")

    def _refresh_latest_pointer(self, deleted: Path) -> None:
        """Si le retour supprimé était le dernier, /retour doit pointer vers le précédent."""
        latest = self.cfg.retours_path / LATEST_NAME
        try:
            if not latest.exists() or deleted.as_posix() not in latest.read_text(encoding="utf-8").replace("\\", "/"):
                return
        except OSError:
            return
        remaining = sorted(
            (p for p in self.cfg.retours_path.iterdir() if p.is_dir() and (p / RETOUR_NAME).exists()),
            key=lambda p: p.name,
        )
        if not remaining:
            latest.unlink(missing_ok=True)
            return
        newest = remaining[-1]
        threading.Thread(target=lambda: export_session(Session.load(newest), self.cfg), daemon=True).start()

    def _begin_task(self, text: str) -> bool:
        if self._task is not None or self.state != "idle":
            self.window.notify("Une autre opération est en cours")
            return False
        self._task = text
        self.window.set_mode("busy", self.cfg)
        self.window.set_actions("busy")
        self.window.notify(text)
        return True

    def _end_task(self, text: str, error: bool = False, select: Path | None = None, markdown: str | None = None) -> None:
        self._task = None
        self.window.set_mode("idle", self.cfg)
        self.refresh_history(select=select if select is not None else self.selected)
        if markdown is not None:
            self.window.show_markdown(markdown)
        if error:
            self._fail(text)
        else:
            self.window.notify(text)
            self._sound("ready")

    def _unload_others(self) -> None:
        for key in ENGINE_LABELS:
            if key != self.cfg.engine:
                self.engines.unload(key)

    # --- réglages
    def open_settings(self) -> None:
        selected = self.selected if self.selected is not None and (self.selected / RETOUR_NAME).exists() else None
        dialog = SettingsDialog(
            self.cfg, input_devices(), parent=self.window, on_install=self.install_claude_code,
            selected_label=describe(selected)["title"] if selected is not None else None,
            on_check_updates=self.updater.check_now,
        )
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        before = (self.cfg.hotkey_record, self.cfg.engine, self.cfg.device, self.cfg.sound_volume)
        dialog.apply_to(self.cfg)
        self.cfg.save()
        if self.cfg.sound_volume != before[3]:
            sounds.prepare(self.cfg.sound_volume)
        if self.cfg.hotkey_record != before[0] and not self.hotkeys.register("record", self.cfg.hotkey_record):
            self._fail(f"Le raccourci {winapi.display_hotkey(self.cfg.hotkey_record)} est déjà pris par une autre appli")
        if self.cfg.device != before[2]:
            self.engines.set_device(self.cfg.device)
        if (self.cfg.engine, self.cfg.device) != before[1:3]:
            self._update_engine_status()
            self._preload(self.cfg.engine)
        self.window.init_controls(self.cfg, input_devices())
        if self.state == "idle" and self._task is None:
            self.window.set_mode("idle", self.cfg)
            self.refresh_history(select=self.selected)
        action = dialog.requested_action  # outils de test des moteurs, lancés depuis l'onglet Transcription
        if action is not None and selected is not None:
            self.selected = selected
            if action[0] == "compare":
                self.compare_selected()
            elif action[0] == "retranscribe" and action[1]:
                self.retranscribe_selected(action[1])

    def install_claude_code(self) -> None:
        retours = self.cfg.retours_path
        text = (
            "Claude Pilot va :\n"
            f"• créer la commande /retour ({claude_code.SKILL_PATH}) ;\n"
            f"• autoriser Claude Code à lire le dossier des retours sans te le demander :\n   {retours}\n"
            f"   (ajout dans {claude_code.SETTINGS_PATH}, avec une copie de sauvegarde).\n\n"
            "Ensuite, après un enregistrement, tape /retour dans Claude Code. Continuer ?"
        )
        if QMessageBox.question(self.window, "Intégration Claude Code", text) != QMessageBox.StandardButton.Yes:
            return
        try:
            done = claude_code.install(retours)
        except Exception as exc:
            QMessageBox.warning(self.window, "Intégration Claude Code", f"Rien n'a été modifié : {exc}")
            return
        QMessageBox.information(
            self.window, "Intégration Claude Code",
            "C'est fait :\n• " + "\n• ".join(done) + "\n\nSi /retour n'apparaît pas, relance Claude Code.",
        )

    def quit(self) -> None:
        if self.live:
            answer = QMessageBox.question(
                self.window, "Claude Pilot", "Un enregistrement est en cours. Le terminer, préparer le retour, puis quitter ?",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.Cancel,
            )
            if answer == QMessageBox.StandardButton.Yes:
                self._quit_after = True
                self.stop_recording()
            return
        if self.state == "finishing":
            self._quit_after = True
            self.window.notify("Claude Pilot se fermera dès que le retour sera prêt")
            return
        if self._task is not None:
            answer = QMessageBox.question(self.window, "Claude Pilot",
                                          f"Une opération est en cours ({self._task}). Quitter quand même ?")
            if answer != QMessageBox.StandardButton.Yes:
                return
        self.force_quit()

    def force_quit(self) -> None:
        """Fermeture sans question (aussi utilisée juste avant une mise à jour)."""
        self._mic_test(False)
        self.ticker.stop()
        self.hotkeys.stop()
        self.overlay.hide()
        self._png.shutdown(wait=True)
        self.app.quit()

    # --- petites aides
    def _overlay_moved(self, x: int, y: int) -> None:
        self.cfg.overlay_pos = [x, y]
        self.cfg.save()

    def _sound(self, name: str) -> None:
        if self.cfg.sounds:
            sounds.play(name, self.cfg.sound_volume)

    def _message(self, text: str) -> None:
        self.window.notify(text)
        if self.live:
            self.overlay.set_caption(text)

    def _fail(self, text: str) -> None:
        log.warning(text)
        self._sound("error")
        self.window.notify(text, error=True)
        if self.live:
            self.overlay.set_caption(text)
        elif not self.window.isActiveWindow():
            self.overlay.show_message(text, error=True, timeout_ms=12000)
