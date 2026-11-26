"""Intégration Windows de l'installation (sans Qt : sert aussi à l'installateur).

Tout est par utilisateur (pas de droits admin) : raccourcis menu Démarrer / bureau et
entrée « Applications installées » (HKCU) pour la désinstallation.

Disposition du dossier d'installation :
  app\\       le code de l'appli (remplacé à chaque mise à jour)
  venv\\      Python et les composants (installés par uv)
  runtime\\   uv et le Python géré par uv
  models\\    les moteurs de transcription
  data\\      réglages et journaux
  install.json, claude-pilot.ico, Claude-Pilot-Setup.exe (sert à désinstaller)
"""
import ctypes
import json
import os
import subprocess
import winreg
from ctypes import wintypes
from pathlib import Path

UNINSTALL_KEY = r"Software\Microsoft\Windows\CurrentVersion\Uninstall\ClaudePilot"
SHORTCUT_NAME = "Claude Pilot.lnk"
SETUP_NAME = "Claude-Pilot-Setup.exe"
INFO_NAME = "install.json"
DICTATION_SHORTCUT = "Claude Pilot Dictée.lnk"  # dans le dossier Démarrage de Windows
DICTATION_PIDS = ("dictee.pid", "dictee_moteur.pid")  # dans data\ : la dictée et son moteur


def default_install_dir() -> Path:
    base = os.environ.get("LOCALAPPDATA") or str(Path.home() / "AppData" / "Local")
    return Path(base) / "Programs" / "Claude Pilot"


def app_dir(install_dir: Path) -> Path:
    return install_dir / "app"


def pythonw(install_dir: Path) -> Path:
    return install_dir / "venv" / "Scripts" / "pythonw.exe"


def python(install_dir: Path) -> Path:
    return install_dir / "venv" / "Scripts" / "python.exe"


def icon_path(install_dir: Path) -> Path:
    return install_dir / "claude-pilot.ico"


def refresh_icons() -> None:
    """L'icône a changé (mise à jour) : l'Explorateur redessine raccourcis et épingles au lieu de garder l'ancienne."""
    try:
        ctypes.windll.shell32.SHChangeNotify(0x08000000, 0, None, None)  # SHCNE_ASSOCCHANGED
    except (AttributeError, OSError):
        pass


def read_info(install_dir: Path) -> dict:
    try:
        return json.loads((install_dir / INFO_NAME).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def write_info(install_dir: Path, **values) -> None:
    info = read_info(install_dir) | values
    (install_dir / INFO_NAME).write_text(json.dumps(info, ensure_ascii=False, indent=2), encoding="utf-8")


def _ps(script: str) -> bool:
    try:
        r = subprocess.run(["powershell", "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass",
                            "-Command", script], capture_output=True, text=True, timeout=30,
                           creationflags=subprocess.CREATE_NO_WINDOW)
        return r.returncode == 0
    except (OSError, subprocess.SubprocessError):
        return False


def _ps_str(s: str) -> str:
    return "'" + s.replace("'", "''") + "'"


def create_shortcuts(install_dir: Path, desktop: bool, start_menu: bool = True) -> bool:
    folders = []
    if start_menu:
        folders.append("[Environment]::GetFolderPath('Programs')")
    if desktop:
        folders.append("[Environment]::GetFolderPath('Desktop')")
    if not folders:
        return True
    script = "$w = New-Object -ComObject WScript.Shell; "
    for folder in folders:
        # Parenthèses obligatoires : sans elles PowerShell passe « [Environment]::… » comme du texte.
        script += (f"$s = $w.CreateShortcut((Join-Path ({folder}) {_ps_str(SHORTCUT_NAME)})); "
                   f"$s.TargetPath = {_ps_str(str(pythonw(install_dir)))}; $s.Arguments = '-m claudepilot'; "
                   f"$s.WorkingDirectory = {_ps_str(str(app_dir(install_dir)))}; "
                   f"$s.IconLocation = {_ps_str(str(icon_path(install_dir)) + ',0')}; "
                   "$s.Description = 'Claude Pilot'; $s.Save(); ")
    return _ps(script)


def remove_shortcuts() -> None:
    _ps("foreach ($f in @([Environment]::GetFolderPath('Programs'), [Environment]::GetFolderPath('Desktop'))) "
        f"{{ $p = Join-Path $f {_ps_str(SHORTCUT_NAME)}; if (Test-Path $p) {{ Remove-Item $p -Force }} }}")


def create_dictation_shortcut(pythonw_exe: Path, workdir: Path, icon: Path) -> bool:
    """La dictée se lance avec Windows (dossier Démarrage : visible et désactivable dans le Gestionnaire des tâches)."""
    return _ps("$w = New-Object -ComObject WScript.Shell; "
               f"$s = $w.CreateShortcut((Join-Path ([Environment]::GetFolderPath('Startup')) "
               f"{_ps_str(DICTATION_SHORTCUT)})); "
               f"$s.TargetPath = {_ps_str(str(pythonw_exe))}; $s.Arguments = '-m claudepilot.dictation'; "
               f"$s.WorkingDirectory = {_ps_str(str(workdir))}; $s.IconLocation = {_ps_str(str(icon) + ',0')}; "
               "$s.Description = 'Dictée Claude Pilot'; $s.Save()")


def remove_dictation_shortcut() -> None:
    _ps(f"$p = Join-Path ([Environment]::GetFolderPath('Startup')) {_ps_str(DICTATION_SHORTCUT)}; "
        "if (Test-Path $p) { Remove-Item $p -Force }")


def stop_dictation(data_dir: Path, inside: Path) -> bool:
    """Ferme la dictée et son moteur (avant une mise à jour ou une désinstallation) : sinon ils garderaient des
    fichiers de l'installation ouverts. Ne touche qu'à des processus lancés depuis « inside »."""
    stopped = False
    for name in DICTATION_PIDS:
        path = data_dir / name
        try:
            pid = int(path.read_text(encoding="utf-8").strip())
        except (OSError, ValueError):
            continue
        stopped |= _terminate_if_inside(pid, inside)
        path.unlink(missing_ok=True)
    return stopped


def _terminate_if_inside(pid: int, inside: Path) -> bool:
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.OpenProcess.restype = wintypes.HANDLE
    kernel32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    kernel32.QueryFullProcessImageNameW.argtypes = [wintypes.HANDLE, wintypes.DWORD, wintypes.LPWSTR,
                                                    ctypes.POINTER(wintypes.DWORD)]
    terminate, query, synchronize = 0x0001, 0x1000, 0x00100000
    handle = kernel32.OpenProcess(terminate | query | synchronize, False, pid)
    if not handle:
        return False
    try:
        buf, size = ctypes.create_unicode_buffer(32768), wintypes.DWORD(32768)
        if not kernel32.QueryFullProcessImageNameW(handle, 0, buf, ctypes.byref(size)):
            return False
        if not Path(buf.value).resolve().is_relative_to(inside.resolve()):
            return False  # le numéro a été réutilisé par un autre programme : on n'y touche pas
        kernel32.TerminateProcess(handle, 0)
        kernel32.WaitForSingleObject(handle, 5000)
        return True
    finally:
        kernel32.CloseHandle(handle)


def register(install_dir: Path, version: str) -> None:
    setup = install_dir / SETUP_NAME
    with winreg.CreateKey(winreg.HKEY_CURRENT_USER, UNINSTALL_KEY) as k:
        values = {
            "DisplayName": "Claude Pilot",
            "DisplayVersion": version,
            "Publisher": "StundZow",
            "InstallLocation": str(install_dir),
            "DisplayIcon": f"{icon_path(install_dir)},0",
            "UninstallString": f'"{setup}" --uninstall --dir "{install_dir}"',
            "URLInfoAbout": "https://github.com/StundZow/claude-pilot",
        }
        for name, value in values.items():
            winreg.SetValueEx(k, name, 0, winreg.REG_SZ, value)
        winreg.SetValueEx(k, "EstimatedSize", 0, winreg.REG_DWORD, 6 * 1024 * 1024)  # ≈ 6 Go, en Ko
        winreg.SetValueEx(k, "NoModify", 0, winreg.REG_DWORD, 1)
        winreg.SetValueEx(k, "NoRepair", 0, winreg.REG_DWORD, 1)


def registered_location() -> Path | None:
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, UNINSTALL_KEY) as k:
            return Path(winreg.QueryValueEx(k, "InstallLocation")[0])
    except OSError:
        return None


def update_registered_version(version: str, install_dir: Path) -> None:
    """Après une mise à jour : version affichée dans « Applications installées »."""
    loc = registered_location()
    if loc and install_dir.resolve() == loc.resolve():
        try:
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, UNINSTALL_KEY, 0, winreg.KEY_SET_VALUE) as k:
                winreg.SetValueEx(k, "DisplayVersion", 0, winreg.REG_SZ, version)
        except OSError:
            pass


def unregister() -> None:
    try:
        winreg.DeleteKey(winreg.HKEY_CURRENT_USER, UNINSTALL_KEY)
    except OSError:
        pass
