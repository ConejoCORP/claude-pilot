"""Installateur de Claude Pilot : le seul fichier à télécharger sur GitHub.

Il n'embarque pas l'appli : il télécharge la dernière version publiée sur GitHub, installe un Python
privé et les composants avec uv (embarqué), télécharge le moteur de transcription, puis crée les
raccourcis et l'entrée « Applications installées ». Avant d'installer, s'il existe un installateur plus
récent sur GitHub, il le télécharge et lui passe la main.

Installation par utilisateur, sans droits administrateur : %LOCALAPPDATA%\\Programs\\Claude Pilot
(modifiable). Les retours vont dans Documents\\Claude Pilot\\retours et restent à la désinstallation.

Options en ligne de commande :
  --silent  --dir <dossier>  --no-start-menu  --no-desktop  --no-shortcuts  --no-register  --no-launch
  --no-online                                   (ne cherche pas d'installateur plus récent)
  --update --dir <dossier> [--wait-pid <pid>]   (mise à jour lancée par l'appli)
  --uninstall [--dir <dossier>] [--silent]
  --app-zip <fichier>                           (tests : installe ce zip au lieu de celui de GitHub)
"""
import ctypes
import os
import re
import shutil
import stat
import subprocess
import sys
import tempfile
import threading
import time
import tkinter as tk
import zipfile
from pathlib import Path
from tkinter import filedialog, messagebox, ttk

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from claudepilot import APP_VERSION, GITHUB_REPO, github, installinfo  # noqa: E402

FROZEN = getattr(sys, "frozen", False)
NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)
PYTHON_VERSION = "3.13"
DEFAULT_ENGINE = "parakeet-v3"
BG, BASE, INPUT, TEXT, MUTED, ACCENT, ACCENT_HI = (
    "#18181B", "#232327", "#2F2F35", "#E8E8EA", "#9A9AA2", "#6D28D9", "#7C3AED")


def child_env(**extra: str) -> dict[str, str]:
    """Environnement des programmes que lance l'installateur, sans les variables du lanceur PyInstaller : sinon un
    installateur relancé se croirait notre sous-processus et prendrait nos fichiers temporaires, effacés dès notre
    fermeture (et l'appli les transmettrait à l'installateur de sa prochaine mise à jour)."""
    env = {k: v for k, v in os.environ.items() if not k.startswith("_PYI_")}
    env["PYINSTALLER_RESET_ENVIRONMENT"] = "1"
    return env | extra


def resource(name: str) -> Path:
    """Fichier embarqué dans l'installateur (uv.exe, icône)."""
    if FROZEN:
        return Path(sys._MEIPASS) / name
    if name == "uv.exe":
        found = shutil.which("uv")
        if found:
            return Path(found)
    return ROOT / "claudepilot" / "assets" / "icon.ico" if name == "icon.ico" else ROOT / name


# --- GitHub
def newer_setup() -> dict | None:
    """Release GitHub dont l'installateur est plus récent que celui-ci (None si à jour ou hors ligne)."""
    try:
        release = github.latest_release(GITHUB_REPO)
    except Exception:
        return None
    if github.is_newer(release.get("tag_name", ""), APP_VERSION) and github.find_asset(release, github.SETUP_ASSET):
        return release
    return None


def hand_over(release: dict, args: list[str], progress=lambda d, t: None) -> None:
    """Télécharge l'installateur plus récent et le lance avec les mêmes options."""
    asset = github.find_asset(release, github.SETUP_ASSET)
    dest = Path(tempfile.gettempdir()) / f"Claude-Pilot-Setup-{release.get('tag_name', 'new')}.exe"
    github.download_asset(asset, dest, progress, private=bool(release.get("_token_used")))
    subprocess.Popen([str(dest), *args, "--no-online"], close_fds=True, env=child_env())


def fetch_app(zip_override: str | None, progress) -> tuple[Path, str]:
    """Télécharge le code de la dernière version (ou prend le zip donné en test). Renvoie (zip, version)."""
    if zip_override:
        return Path(zip_override), APP_VERSION
    release = github.latest_release(GITHUB_REPO)
    asset = github.find_asset(release, github.APP_ZIP_ASSET)
    if asset is None:
        raise RuntimeError(f"la dernière version ne contient pas {github.APP_ZIP_ASSET}")
    version = (release.get("tag_name") or "").lstrip("v") or APP_VERSION
    dest = Path(tempfile.gettempdir()) / f"Claude-Pilot-app-{version}.zip"
    github.download_asset(asset, dest, progress, private=bool(release.get("_token_used")))
    return dest, version


# --- étapes
def leave_install_dir(target: Path) -> bool:
    """Lancé par l'appli, l'installateur hérite de son dossier courant (app\\) : tant qu'un processus s'y trouve,
    Windows refuse de renommer ou de supprimer ce dossier. On se relance donc depuis le dossier temporaire (en exe,
    le lanceur de PyInstaller garderait sinon le dossier de départ). True : la relance a pris le relais."""
    try:
        inside = Path.cwd().resolve().is_relative_to(target.resolve())
    except OSError:
        return False
    if not inside:
        return False
    if not FROZEN:
        os.chdir(tempfile.gettempdir())
        return False
    subprocess.Popen([sys.executable, *sys.argv[1:]], cwd=tempfile.gettempdir(), close_fds=True, env=child_env())
    return True


def wait_for_pid(pid: int, timeout: float = 60) -> None:
    synchronize = 0x00100000
    handle = ctypes.windll.kernel32.OpenProcess(synchronize, False, pid)
    if handle:
        ctypes.windll.kernel32.WaitForSingleObject(handle, int(timeout * 1000))
        ctypes.windll.kernel32.CloseHandle(handle)


def _remove_tree(path: Path) -> None:
    def retry(func, target, _exc):
        if func is os.path.islink:
            # Jonction (uv en crée une : cpython-3.13 -> cpython-3.13.x) : on retire le lien, pas ce qu'il vise.
            os.rmdir(target)
            return
        # Fichier en lecture seule, ou suppression encore « en attente » (l'antivirus inspecte les fichiers
        # récents) : le dossier paraît plein une fraction de seconde. On réessaie un court moment.
        for attempt in range(20):
            try:
                os.chmod(target, stat.S_IWRITE)
                func(target)
                return
            except FileNotFoundError:
                return
            except OSError:
                if attempt == 19:
                    raise
                time.sleep(0.1)

    # Chemin étendu « \\?\ » : au fond du Python privé, certains fichiers dépassent la limite des 260 caractères
    # de Windows dès que le dossier d'installation est un peu profond ; sans ce préfixe on ne peut plus les effacer.
    target = str(path.resolve())
    if not target.startswith("\\\\?\\"):
        target = "\\\\?\\" + target
    if os.path.isjunction(target) or os.path.islink(target):
        os.rmdir(target)
    elif os.path.exists(target):
        shutil.rmtree(target, onexc=retry)


def put_app(install_dir: Path, zip_path: Path) -> None:
    """Remplace le code de l'appli ; l'ancien reste en place tant que le nouveau n'est pas prêt."""
    install_dir.mkdir(parents=True, exist_ok=True)
    app, new, old = installinfo.app_dir(install_dir), install_dir / "app.new", install_dir / "app.old"
    _remove_tree(new)
    with zipfile.ZipFile(zip_path) as archive:
        archive.extractall(new)
    _remove_tree(old)
    for _ in range(20):  # l'appli peut mettre un instant à libérer ses fichiers
        try:
            if app.exists():
                app.rename(old)
            new.rename(app)
            break
        except OSError:
            time.sleep(0.5)
    else:
        raise RuntimeError("Claude Pilot est ouvert : ferme-le, puis relance l'installation.")
    _remove_tree(old)
    shutil.copyfile(app / "claudepilot" / "assets" / "icon.ico", installinfo.icon_path(install_dir))


def put_uv(install_dir: Path) -> Path:
    target = install_dir / "runtime" / "uv.exe"
    source = resource("uv.exe")
    target.parent.mkdir(parents=True, exist_ok=True)
    if not target.exists() or target.stat().st_size != source.stat().st_size:
        shutil.copyfile(source, target)
    return target


def describe_uv_line(line: str) -> str | None:
    """Traduit les lignes de uv en messages lisibles (les autres sont ignorées)."""
    line = line.strip()
    if line.startswith("Downloading "):
        name = line.split()[1]
        sizes = re.findall(r"\(([^)]*\d[^)]*)\)", line)  # la dernière parenthèse avec un chiffre = la taille
        size = f" ({sizes[-1]})" if sizes else ""
        return f"Téléchargement de Python{size}…" if name.startswith("cpython") else f"Téléchargement de {name}{size}…"
    match = re.match(r"(Prepared|Installed|Uninstalled) (\d+) package", line)
    if match:
        verb = {"Prepared": "téléchargés", "Installed": "installés", "Uninstalled": "retirés"}[match.group(1)]
        return f"{match.group(2)} composants {verb}"
    if line.startswith("Creating virtual environment"):
        return "Création de l'environnement Python…"
    return None


def sync_env(install_dir: Path, status) -> None:
    """Installe (ou met à jour) Python et les composants, exactement aux versions du fichier uv.lock."""
    uv = put_uv(install_dir)
    env = child_env(UV_PROJECT_ENVIRONMENT=str(install_dir / "venv"),
                    UV_PYTHON_INSTALL_DIR=str(install_dir / "runtime" / "python"),
                    UV_PYTHON_PREFERENCE="only-managed")
    cmd = [str(uv), "sync", "--frozen", "--no-dev", "--python", PYTHON_VERSION,
           "--project", str(installinfo.app_dir(install_dir))]
    proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, encoding="utf-8",
                            errors="replace", env=env, creationflags=NO_WINDOW)
    tail: list[str] = []
    for line in proc.stdout:
        tail = (tail + [line.strip()])[-12:]
        text = describe_uv_line(line)
        if text:
            status(text)
    if proc.wait() != 0:
        raise RuntimeError("installation des composants impossible :\n" + "\n".join(t for t in tail if t))


def fetch_model(install_dir: Path, progress) -> bool:
    """Moteur de transcription par défaut. Un échec n'est pas bloquant : l'appli le téléchargera elle-même."""
    proc = subprocess.Popen([str(installinfo.python(install_dir)), "-m", "claudepilot.models", DEFAULT_ENGINE],
                            cwd=str(installinfo.app_dir(install_dir)), stdout=subprocess.PIPE,
                            stderr=subprocess.DEVNULL, text=True, creationflags=NO_WINDOW, env=child_env())
    for line in proc.stdout:
        parts = line.split()
        if len(parts) == 3 and parts[0] == "PROGRESS":
            progress(int(parts[1]), int(parts[2]))
    return proc.wait() == 0


def keep_setup(install_dir: Path) -> None:
    """Garde l'installateur dans le dossier : c'est lui qui désinstalle."""
    if FROZEN:
        target = install_dir / installinfo.SETUP_NAME
        if Path(sys.executable).resolve() != target.resolve():
            shutil.copyfile(sys.executable, target)


def install(target: Path, desktop: bool, start_menu: bool, shortcuts: bool, register: bool,
            status, progress, zip_override: str | None = None) -> None:
    status("Recherche de la dernière version de Claude Pilot…")
    zip_path, version = fetch_app(zip_override, progress)
    status(f"Installation de Claude Pilot {version}…")
    put_app(target, zip_path)
    installinfo.write_info(target, version=version, desktop=desktop, start_menu=start_menu)
    status("Installation de Python et des composants (≈ 2 Go la première fois)…")
    progress(0, 0)
    sync_env(target, status)
    status("Téléchargement du moteur de transcription Parakeet (2,5 Go)…")
    progress(0, 0)
    if not fetch_model(target, progress):
        status("Le moteur de transcription sera téléchargé au premier lancement.")
    keep_setup(target)
    if shortcuts and (desktop or start_menu):
        status("Création des raccourcis…")
        installinfo.create_shortcuts(target, desktop, start_menu)
    if register:
        installinfo.register(target, version)
    progress(1, 1)
    status(f"Claude Pilot {version} est installé.")


def update(target: Path, wait_pid: int | None, launch_after: bool, status, progress) -> None:
    """Mise à jour lancée par l'appli : attend sa fermeture, remplace le code, met les composants à jour."""
    if wait_pid:
        status("Fermeture de Claude Pilot…")
        wait_for_pid(wait_pid)
    installinfo.stop_dictation(target / "data", target)  # l'appli la relance à son démarrage
    status("Téléchargement de la nouvelle version…")
    zip_path, version = fetch_app(None, progress)
    status(f"Installation de Claude Pilot {version}…")
    put_app(target, zip_path)
    installinfo.write_info(target, version=version)
    status("Mise à jour des composants…")
    progress(0, 0)
    sync_env(target, status)
    keep_setup(target)
    installinfo.update_registered_version(version, target)
    progress(1, 1)
    status(f"Claude Pilot {version} est installé.")
    if launch_after:
        launch(target, "--updated")


def launch(install_dir: Path, *args: str) -> None:
    subprocess.Popen([str(installinfo.pythonw(install_dir)), "-m", "claudepilot", *args],
                     cwd=str(installinfo.app_dir(install_dir)), close_fds=True, env=child_env())


def uninstall(target: Path, silent: bool) -> int:
    running_inside = FROZEN and Path(sys.executable).resolve().is_relative_to(target.resolve())
    if running_inside and "--from-temp" not in sys.argv:  # un exe ne peut pas supprimer son propre dossier
        copy = Path(tempfile.gettempdir()) / "Claude-Pilot-Uninstall.exe"
        shutil.copyfile(sys.executable, copy)
        subprocess.Popen([str(copy), *sys.argv[1:], "--from-temp"], cwd=tempfile.gettempdir(), close_fds=True,
                         env=child_env())
        return 0
    root = None
    if not silent:
        root = tk.Tk()
        root.withdraw()
        if not messagebox.askyesno(
                "Désinstaller Claude Pilot",
                f"Supprimer Claude Pilot, ses composants et ses moteurs de transcription ?\n\n{target}\n\n"
                "Tes retours (Documents\\Claude Pilot\\retours) sont conservés."):
            return 0
    installinfo.remove_shortcuts()
    installinfo.remove_dictation_shortcut()
    installinfo.stop_dictation(target / "data", target)
    for attempt in range(3):
        try:
            _remove_tree(target)
            break
        except OSError:
            if attempt == 2:
                if root is not None:
                    messagebox.showerror("Claude Pilot", "Ferme Claude Pilot, puis relance la désinstallation.")
                return 1
            time.sleep(2)
    installinfo.unregister()
    if root is not None:
        messagebox.showinfo("Claude Pilot", "Claude Pilot a été désinstallé. Tes retours sont toujours dans "
                                            "Documents\\Claude Pilot\\retours.")
    return 0


# --- fenêtres
def style_window(root: tk.Tk, title: str) -> None:
    root.title(title)
    root.configure(bg=BG)
    root.resizable(False, False)
    try:
        root.iconbitmap(default=str(resource("icon.ico")))
    except tk.TclError:
        pass
    style = ttk.Style(root)
    style.theme_use("clam")
    style.configure(".", background=BG, foreground=TEXT, fieldbackground=INPUT, font=("Segoe UI", 10))
    style.configure("TFrame", background=BG)
    style.configure("TLabel", background=BG, foreground=TEXT)
    style.configure("Muted.TLabel", foreground=MUTED)
    style.configure("Title.TLabel", font=("Segoe UI", 15, "bold"))
    style.configure("TCheckbutton", background=BG, foreground=TEXT)
    style.map("TCheckbutton", background=[("active", BG)])
    style.configure("TButton", background=INPUT, foreground=TEXT, borderwidth=0, padding=(14, 6))
    style.map("TButton", background=[("active", "#3A3A42"), ("disabled", BASE)], foreground=[("disabled", MUTED)])
    style.configure("Accent.TButton", background=ACCENT)
    style.map("Accent.TButton", background=[("active", ACCENT_HI), ("disabled", BASE)])
    style.configure("TEntry", fieldbackground=INPUT, foreground=TEXT, insertcolor=TEXT, borderwidth=0)
    style.configure("Horizontal.TProgressbar", background=ACCENT, troughcolor=INPUT, borderwidth=0)


class ProgressMixin:
    root: tk.Tk
    bar: ttk.Progressbar
    status: ttk.Label

    def set_status(self, text: str) -> None:
        self.root.after(0, lambda: self.status.configure(text=text))

    def set_progress(self, done: int, total: int) -> None:
        def apply():
            if total <= 0:  # durée inconnue : barre animée
                if str(self.bar.cget("mode")) != "indeterminate":
                    self.bar.configure(mode="indeterminate")
                    self.bar.start(12)
            else:
                if str(self.bar.cget("mode")) != "determinate":
                    self.bar.stop()
                    self.bar.configure(mode="determinate")
                self.bar.configure(maximum=total, value=done)
        self.root.after(0, apply)


class SetupWindow(ProgressMixin):
    def __init__(self, root: tk.Tk, online: bool, zip_override: str | None):
        self.root = root
        self.online = online
        self.zip_override = zip_override
        style_window(root, "Installation de Claude Pilot")
        frm = ttk.Frame(root, padding=22)
        frm.grid(sticky="nsew")
        ttk.Label(frm, text="Claude Pilot", style="Title.TLabel").grid(row=0, column=0, columnspan=3, sticky="w")
        ttk.Label(frm, text="Tes retours à Claude Code, à la voix, avec les captures au bon moment.",
                  style="Muted.TLabel").grid(row=1, column=0, columnspan=3, sticky="w", pady=(2, 16))
        ttk.Label(frm, text="Dossier d'installation").grid(row=2, column=0, columnspan=3, sticky="w")
        self.dir_var = tk.StringVar(value=str(installinfo.registered_location() or installinfo.default_install_dir()))
        ttk.Entry(frm, textvariable=self.dir_var, width=56).grid(row=3, column=0, columnspan=2, sticky="we",
                                                                  pady=(4, 10), ipady=4)
        ttk.Button(frm, text="Changer…", command=self.browse).grid(row=3, column=2, padx=(8, 0), pady=(4, 10))
        self.start_menu = tk.BooleanVar(value=True)
        self.desktop = tk.BooleanVar(value=True)
        self.launch = tk.BooleanVar(value=True)
        ttk.Checkbutton(frm, text="Ajouter au menu Démarrer", variable=self.start_menu).grid(
            row=4, column=0, columnspan=3, sticky="w")
        ttk.Checkbutton(frm, text="Raccourci sur le bureau", variable=self.desktop).grid(
            row=5, column=0, columnspan=3, sticky="w", pady=(2, 0))
        ttk.Checkbutton(frm, text="Lancer Claude Pilot à la fin", variable=self.launch).grid(
            row=6, column=0, columnspan=3, sticky="w", pady=(2, 10))
        ttk.Label(frm, text="Première installation : environ 6 Go d'espace et quelques minutes de téléchargement.\n"
                            "Carte graphique NVIDIA conseillée (sinon tout tourne sur le processeur).",
                  style="Muted.TLabel", justify="left").grid(row=7, column=0, columnspan=3, sticky="w", pady=(0, 14))
        self.bar = ttk.Progressbar(frm, length=470, mode="determinate")
        self.bar.grid(row=8, column=0, columnspan=3, sticky="we")
        self.status = ttk.Label(frm, text="Prêt à installer.", style="Muted.TLabel", wraplength=470)
        self.status.grid(row=9, column=0, columnspan=3, sticky="w", pady=(6, 14))
        self.cancel_btn = ttk.Button(frm, text="Fermer", command=root.destroy)
        self.cancel_btn.grid(row=10, column=1, sticky="e", padx=(0, 8))
        self.go_btn = ttk.Button(frm, text="Installer", style="Accent.TButton", command=self.start)
        self.go_btn.grid(row=10, column=2, sticky="e")
        frm.columnconfigure(0, weight=1)

    def browse(self) -> None:
        chosen = filedialog.askdirectory(initialdir=self.dir_var.get())
        if chosen:
            path = Path(chosen)
            self.dir_var.set(str(path if path.name.lower() == "claude pilot" else path / "Claude Pilot"))

    def start(self) -> None:
        self.go_btn.configure(state="disabled")
        self.cancel_btn.configure(state="disabled")
        target = Path(self.dir_var.get())
        desktop, launch_after, start_menu = self.desktop.get(), self.launch.get(), self.start_menu.get()

        def work():
            try:
                if self.online:
                    self.set_status("Recherche d'un installateur plus récent…")
                    release = newer_setup()
                    if release:
                        self.set_status(f"Version {release['tag_name'].lstrip('v')} disponible : téléchargement…")
                        args = ["--dir", str(target)] + ([] if desktop else ["--no-desktop"]) \
                            + ([] if start_menu else ["--no-start-menu"]) + ([] if launch_after else ["--no-launch"])
                        hand_over(release, args, self.set_progress)
                        self.root.after(500, self.root.destroy)
                        return
                install(target, desktop, start_menu, True, True, self.set_status, self.set_progress, self.zip_override)
            except Exception as exc:  # message clair pour l'utilisateur
                self.set_status(f"Échec : {exc}")
                self.root.after(0, lambda: (self.bar.stop(), self.go_btn.configure(state="normal", text="Réessayer"),
                                            self.cancel_btn.configure(state="normal")))
                return
            if launch_after:
                launch(target)
                self.root.after(1500, self.root.destroy)
            else:
                self.root.after(0, lambda: (self.cancel_btn.configure(state="normal", text="Terminer"),
                                            self.go_btn.grid_remove()))

        threading.Thread(target=work, daemon=True).start()


class UpdateWindow(ProgressMixin):
    def __init__(self, root: tk.Tk, target: Path, wait_pid: int | None, launch_after: bool):
        self.root = root
        style_window(root, "Mise à jour de Claude Pilot")
        frm = ttk.Frame(root, padding=22)
        frm.grid(sticky="nsew")
        ttk.Label(frm, text="Mise à jour de Claude Pilot", style="Title.TLabel").grid(row=0, column=0, sticky="w")
        self.bar = ttk.Progressbar(frm, length=420, mode="determinate")
        self.bar.grid(row=1, column=0, sticky="we", pady=(16, 0))
        self.status = ttk.Label(frm, text="Préparation…", style="Muted.TLabel", wraplength=420)
        self.status.grid(row=2, column=0, sticky="w", pady=(6, 4))
        self.close_btn = ttk.Button(frm, text="Fermer", command=root.destroy)

        def work():
            try:
                update(target, wait_pid, launch_after, self.set_status, self.set_progress)
            except Exception as exc:
                self.set_status(f"Échec de la mise à jour : {exc}")
                self.root.after(0, lambda: (self.bar.stop(), self.close_btn.grid(row=3, column=0, sticky="e", pady=(10, 0))))
                return
            self.root.after(1500, self.root.destroy)

        threading.Thread(target=work, daemon=True).start()


def _arg(args: list[str], name: str) -> str | None:
    return args[args.index(name) + 1] if name in args and args.index(name) + 1 < len(args) else None


def main() -> int:
    args = sys.argv[1:]
    target = Path(_arg(args, "--dir") or installinfo.registered_location() or installinfo.default_install_dir())
    silent = "--silent" in args
    if "--uninstall" in args:
        return 0 if leave_install_dir(target) else uninstall(target, silent)
    if "--update" in args:
        if leave_install_dir(target):
            return 0
        pid = _arg(args, "--wait-pid")
        wait_pid, launch_after = (int(pid) if pid else None), "--no-launch" not in args
        if silent:
            try:
                update(target, wait_pid, launch_after, print, lambda d, t: None)
            except Exception as exc:
                print(f"ERREUR : {exc}")
                return 1
            return 0
        root = tk.Tk()
        UpdateWindow(root, target, wait_pid, launch_after)
        root.mainloop()
        return 0
    zip_override = _arg(args, "--app-zip")
    online = "--no-online" not in args and zip_override is None
    if silent:
        try:
            if online:
                release = newer_setup()
                if release:
                    hand_over(release, [a for a in args if a != "--no-online"])
                    return 0
            install(target, "--no-desktop" not in args, "--no-start-menu" not in args, "--no-shortcuts" not in args,
                    "--no-register" not in args, print,
                    lambda done, total: total > 1 and print(f"  {done * 100 // total} %", end="\r"), zip_override)
        except Exception as exc:
            print(f"ERREUR : {exc}")
            return 1
        if "--no-launch" not in args:
            launch(target)
        return 0
    root = tk.Tk()
    window = SetupWindow(root, online, zip_override)
    if "--dir" in args:
        window.dir_var.set(str(target))
    if "--no-desktop" in args:
        window.desktop.set(False)
    if "--no-start-menu" in args:
        window.start_menu.set(False)
    if "--no-launch" in args:
        window.launch.set(False)
    root.mainloop()
    return 0


if __name__ == "__main__":
    sys.exit(main())
