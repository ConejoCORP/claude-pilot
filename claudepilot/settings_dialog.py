"""Réglages de Claude Pilot, rangés par onglets. Les outils de test des moteurs agissent sur le retour sélectionné."""
from __future__ import annotations

from typing import Callable

from PySide6.QtCore import Qt
from PySide6.QtGui import QKeySequence
from PySide6.QtWidgets import (
    QCheckBox, QComboBox, QDialog, QDialogButtonBox, QDoubleSpinBox, QFileDialog, QFormLayout, QHBoxLayout,
    QKeySequenceEdit, QLabel, QLineEdit, QPlainTextEdit, QPushButton, QSlider, QSpinBox, QTabWidget, QVBoxLayout,
    QWidget,
)

from . import APP_VERSION, claude_code, sounds, winapi
from .config import ENGINE_LABELS, Config


class SettingsDialog(QDialog):
    def __init__(
        self,
        cfg: Config,
        microphones: list[str],
        parent=None,
        on_install: Callable[[], None] | None = None,
        selected_label: str | None = None,
        on_check_updates: Callable[[], None] | None = None,
    ):
        super().__init__(parent)
        self.setWindowTitle("Réglages de Claude Pilot")
        self.setMinimumWidth(640)
        self._on_install = on_install
        self._on_check_updates = on_check_updates
        self.requested_action: tuple[str, str | None] | None = None  # ("compare", None) ou ("retranscribe", moteur)

        tabs = QTabWidget()
        tabs.addTab(self._general_tab(cfg, microphones), "Général")
        tabs.addTab(self._captures_tab(cfg), "Captures")
        tabs.addTab(self._engines_tab(cfg, selected_label), "Transcription")
        tabs.addTab(self._sounds_tab(cfg), "Sons")
        tabs.addTab(self._claude_tab(), "Claude Code")
        self.tabs = tabs
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout = QVBoxLayout(self)
        layout.addWidget(tabs)
        layout.addWidget(buttons)

    @staticmethod
    def _page() -> tuple[QWidget, QFormLayout]:
        page = QWidget()
        form = QFormLayout(page)
        form.setContentsMargins(18, 18, 18, 18)
        form.setVerticalSpacing(12)
        return page, form

    @staticmethod
    def _note(text: str) -> QLabel:
        label = QLabel(text)
        label.setObjectName("muted")
        label.setWordWrap(True)
        return label

    def _general_tab(self, cfg: Config, microphones: list[str]) -> QWidget:
        page, form = self._page()
        self.mic = QComboBox()
        self.mic.addItem("Micro par défaut de Windows", "")
        for name in microphones:
            self.mic.addItem(name, name)
        self._select(self.mic, cfg.microphone)
        form.addRow("Micro", self.mic)
        self.hk_record = self._hotkey_edit(cfg.hotkey_record)
        form.addRow("Démarrer / terminer", self.hk_record)
        self.hk_capture = self._hotkey_edit(cfg.hotkey_capture)
        form.addRow("Capturer (pendant l'enregistrement)", self.hk_capture)
        self.copy_prompt = QCheckBox("Copier le message pour Claude Code dès que le retour est prêt")
        self.copy_prompt.setChecked(cfg.copy_prompt_on_stop)
        form.addRow("À l'arrêt", self.copy_prompt)
        self.retours = QLineEdit(cfg.retours_dir)
        browse = QPushButton("Parcourir…")
        browse.clicked.connect(self._browse)
        row = QHBoxLayout()
        row.addWidget(self.retours)
        row.addWidget(browse)
        form.addRow("Dossier des retours", row)
        self.check_updates = QCheckBox("Chercher une nouvelle version à chaque lancement")
        self.check_updates.setChecked(cfg.check_updates)
        check_now = QPushButton("Vérifier maintenant")
        check_now.clicked.connect(lambda: self._on_check_updates and self._on_check_updates())
        updates = QHBoxLayout()
        updates.addWidget(self.check_updates, 1)
        updates.addWidget(check_now)
        form.addRow("Mises à jour", updates)
        form.addRow("", self._note(f"Claude Pilot {APP_VERSION}"))
        return page

    def _captures_tab(self, cfg: Config) -> QWidget:
        page, form = self._page()
        self.triggers = QLineEdit(", ".join(cfg.triggers))
        form.addRow("Mots qui déclenchent une capture", self.triggers)
        self.kept = QLineEdit(", ".join(cfg.triggers_kept))
        form.addRow("…gardés dans le texte", self.kept)
        form.addRow("", self._note(
            "Parmi les mots gardés, « regarde » capture à chaque fois. Ceux de tous les jours (« ici », "
            "« tu vois ») ne capturent que si l'écran ou la souris ont bougé depuis la dernière capture."
        ))
        self.commands = QCheckBox("« retire la capture » (ou annule, supprime…), « point suivant »")
        self.commands.setChecked(cfg.voice_commands)
        form.addRow("Commandes vocales", self.commands)
        self.max_px = QSpinBox()
        self.max_px.setRange(800, 2576)
        self.max_px.setSingleStep(100)
        self.max_px.setValue(cfg.export_max_px)
        self.max_px.setSuffix(" px")
        form.addRow("Taille max des captures", self.max_px)
        self.cursor_ring = QCheckBox("Entourer ma souris en rouge")
        self.cursor_ring.setChecked(cfg.cursor_ring)
        self.cursor_ring.setToolTip("Montre ce que tu pointes, mais l'IA peut prendre le cercle pour un élément "
                                    "de l'écran : désactivé par défaut.")
        form.addRow("Sur les captures", self.cursor_ring)
        self.ring_fps = QDoubleSpinBox()
        self.ring_fps.setRange(0.0, 5.0)
        self.ring_fps.setSingleStep(0.5)
        self.ring_fps.setValue(cfg.ring_fps)
        self.ring_fps.setSuffix(" images/s")
        form.addRow("Boîte noire", self.ring_fps)
        form.addRow("", self._note(
            "La boîte noire garde les dernières secondes d'écran en mémoire : une capture dite à voix haute prend "
            "l'image du moment exact du mot. 0 = désactivée."
        ))
        return page

    def _engines_tab(self, cfg: Config, selected_label: str | None) -> QWidget:
        page, form = self._page()
        self.engine = QComboBox()
        for key, label in ENGINE_LABELS.items():
            self.engine.addItem(label, key)
        self._select(self.engine, cfg.engine)
        form.addRow("Moteur", self.engine)
        self.device = QComboBox()
        for label, value in (("Automatique (carte graphique si possible)", "auto"),
                             ("Carte graphique", "cuda"), ("Processeur", "cpu")):
            self.device.addItem(label, value)
        self._select(self.device, cfg.device)
        form.addRow("Calcul", self.device)
        self.lexicon = QPlainTextEdit("\n".join(cfg.lexicon))
        self.lexicon.setFixedHeight(90)
        form.addRow("Lexique (Whisper)", self.lexicon)
        form.addRow("", self._note("Un terme par ligne : noms de projets, jargon. Parakeet ne s'en sert pas."))

        title = QLabel("TESTER LES MOTEURS")
        title.setObjectName("section")
        form.addRow(title)
        if selected_label:
            form.addRow(self._note(f"Sur le retour du {selected_label}, sélectionné dans la liste."))
        else:
            form.addRow(self._note("Sélectionne d'abord un retour dans la liste de gauche."))
        compare = QPushButton("Comparer les 3 moteurs")
        compare.setToolTip("Retranscrit le même audio avec les 3 moteurs et affiche le résultat phrase par phrase")
        compare.clicked.connect(lambda: self._request("compare", None))
        form.addRow("Comparer", compare)
        self.retranscribe_engine = QComboBox()
        for key, label in ENGINE_LABELS.items():
            self.retranscribe_engine.addItem(label, key)
        retranscribe = QPushButton("Retranscrire")
        retranscribe.clicked.connect(lambda: self._request("retranscribe", self.retranscribe_engine.currentData()))
        row = QHBoxLayout()
        row.addWidget(self.retranscribe_engine, 1)
        row.addWidget(retranscribe)
        form.addRow("Retranscrire avec", row)
        for widget in (compare, retranscribe, self.retranscribe_engine):
            widget.setEnabled(bool(selected_label))
        return page

    def _sounds_tab(self, cfg: Config) -> QWidget:
        page, form = self._page()
        self.sounds = QCheckBox("Activés")
        self.sounds.setChecked(cfg.sounds)
        form.addRow("Bruitages", self.sounds)
        self.volume = QSlider(Qt.Orientation.Horizontal)
        self.volume.setRange(5, 100)
        self.volume.setValue(cfg.sound_volume)
        listen = QPushButton("Écouter")
        listen.clicked.connect(lambda: sounds.demo(self.volume.value()))
        row = QHBoxLayout()
        row.addWidget(self.volume, 1)
        row.addWidget(listen)
        form.addRow("Volume", row)
        self.phrase_sound = QCheckBox("Petit « plip » à chaque phrase transcrite")
        self.phrase_sound.setChecked(cfg.sound_each_phrase)
        form.addRow("", self.phrase_sound)
        return page

    def _claude_tab(self) -> QWidget:
        page, form = self._page()
        form.addRow(self._note(
            "Après un enregistrement, tu peux coller le message avec Ctrl+V dans Claude Code. Pour taper /retour à la "
            "place, installe la commande : elle s'ajoute à Claude Code (dans tous tes projets) et l'autorise à lire "
            "le dossier des retours sans te demander la permission."
        ))
        install = QPushButton("Installer la commande /retour")
        install.clicked.connect(self._install)
        self.install_status = QLabel()
        self._refresh_install_status()
        row = QHBoxLayout()
        row.addWidget(install)
        row.addSpacing(12)
        row.addWidget(self.install_status, 1)
        form.addRow(row)
        return page

    def _request(self, action: str, argument: str | None) -> None:
        self.requested_action = (action, argument)
        self.accept()

    def _install(self) -> None:
        if self._on_install is not None:
            self._on_install()
        self._refresh_install_status()

    def _refresh_install_status(self) -> None:
        self.install_status.setText("installée ✓" if claude_code.is_installed() else "pas encore installée")

    @staticmethod
    def _select(combo: QComboBox, value: str) -> None:
        index = combo.findData(value)
        combo.setCurrentIndex(max(0, index))

    @staticmethod
    def _hotkey_edit(combo: str) -> QKeySequenceEdit:
        edit = QKeySequenceEdit(QKeySequence(combo))
        edit.setMaximumSequenceLength(1)
        return edit

    def _browse(self) -> None:
        folder = QFileDialog.getExistingDirectory(self, "Dossier des retours", self.retours.text())
        if folder:
            self.retours.setText(folder)

    @staticmethod
    def _hotkey_text(edit: QKeySequenceEdit, fallback: str) -> str:
        text = edit.keySequence().toString(QKeySequence.SequenceFormat.PortableText)
        try:
            winapi.parse_hotkey(text)
            return text
        except ValueError:
            return fallback

    def apply_to(self, cfg: Config) -> None:
        cfg.microphone = self.mic.currentData()
        cfg.engine = self.engine.currentData()
        cfg.device = self.device.currentData()
        cfg.hotkey_record = self._hotkey_text(self.hk_record, cfg.hotkey_record)
        cfg.hotkey_capture = self._hotkey_text(self.hk_capture, cfg.hotkey_capture)
        cfg.triggers = [t.strip() for t in self.triggers.text().split(",") if t.strip()]
        cfg.triggers_kept = [t.strip() for t in self.kept.text().split(",") if t.strip()]
        cfg.voice_commands = self.commands.isChecked()
        cfg.lexicon = [t.strip() for t in self.lexicon.toPlainText().splitlines() if t.strip()]
        cfg.export_max_px = self.max_px.value()
        cfg.cursor_ring = self.cursor_ring.isChecked()
        cfg.ring_fps = self.ring_fps.value()
        cfg.copy_prompt_on_stop = self.copy_prompt.isChecked()
        cfg.sounds = self.sounds.isChecked()
        cfg.sound_volume = self.volume.value()
        cfg.sound_each_phrase = self.phrase_sound.isChecked()
        cfg.check_updates = self.check_updates.isChecked()
        cfg.retours_dir = self.retours.text().strip() or cfg.retours_dir
