"""Appels directs aux API Windows (ctypes) : raccourcis globaux, souris, écrans, fenêtres."""
from __future__ import annotations

import ctypes
import logging
import queue
import re
import threading
from ctypes import wintypes
from typing import Callable

log = logging.getLogger(__name__)

user32 = ctypes.WinDLL("user32", use_last_error=True)
kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)

MONITORENUMPROC = ctypes.WINFUNCTYPE(
    wintypes.BOOL, wintypes.HMONITOR, wintypes.HDC, ctypes.POINTER(wintypes.RECT), wintypes.LPARAM
)

user32.RegisterHotKey.argtypes = [wintypes.HWND, ctypes.c_int, wintypes.UINT, wintypes.UINT]
user32.RegisterHotKey.restype = wintypes.BOOL
user32.UnregisterHotKey.argtypes = [wintypes.HWND, ctypes.c_int]
user32.UnregisterHotKey.restype = wintypes.BOOL
user32.GetMessageW.argtypes = [ctypes.POINTER(wintypes.MSG), wintypes.HWND, wintypes.UINT, wintypes.UINT]
user32.GetMessageW.restype = ctypes.c_int
user32.PeekMessageW.argtypes = [
    ctypes.POINTER(wintypes.MSG), wintypes.HWND, wintypes.UINT, wintypes.UINT, wintypes.UINT
]
user32.PeekMessageW.restype = wintypes.BOOL
user32.PostThreadMessageW.argtypes = [wintypes.DWORD, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM]
user32.PostThreadMessageW.restype = wintypes.BOOL
user32.SetWindowDisplayAffinity.argtypes = [wintypes.HWND, wintypes.DWORD]
user32.SetWindowDisplayAffinity.restype = wintypes.BOOL
user32.GetCursorPos.argtypes = [ctypes.POINTER(wintypes.POINT)]
user32.GetCursorPos.restype = wintypes.BOOL
user32.GetWindowRect.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.RECT)]
user32.GetWindowRect.restype = wintypes.BOOL
user32.GetMonitorInfoW.argtypes = [wintypes.HMONITOR, ctypes.c_void_p]
user32.GetMonitorInfoW.restype = wintypes.BOOL
user32.EnumDisplayMonitors.argtypes = [wintypes.HDC, ctypes.POINTER(wintypes.RECT), MONITORENUMPROC, wintypes.LPARAM]
user32.EnumDisplayMonitors.restype = wintypes.BOOL
kernel32.GetCurrentThreadId.restype = wintypes.DWORD
kernel32.CreateMutexW.argtypes = [ctypes.c_void_p, wintypes.BOOL, wintypes.LPCWSTR]
kernel32.CreateMutexW.restype = wintypes.HANDLE

class KEYBDINPUT(ctypes.Structure):
    _fields_ = [("wVk", wintypes.WORD), ("wScan", wintypes.WORD), ("dwFlags", wintypes.DWORD),
                ("time", wintypes.DWORD), ("dwExtraInfo", ctypes.c_size_t)]


class MOUSEINPUT(ctypes.Structure):
    _fields_ = [("dx", wintypes.LONG), ("dy", wintypes.LONG), ("mouseData", wintypes.DWORD),
                ("dwFlags", wintypes.DWORD), ("time", wintypes.DWORD), ("dwExtraInfo", ctypes.c_size_t)]


class _INPUTUNION(ctypes.Union):
    _fields_ = [("mi", MOUSEINPUT), ("ki", KEYBDINPUT)]


class INPUT(ctypes.Structure):
    _fields_ = [("type", wintypes.DWORD), ("u", _INPUTUNION)]


user32.SendInput.argtypes = [wintypes.UINT, ctypes.POINTER(INPUT), ctypes.c_int]
user32.SendInput.restype = wintypes.UINT
user32.GetWindowLongPtrW.argtypes = [wintypes.HWND, ctypes.c_int]
user32.GetWindowLongPtrW.restype = ctypes.c_ssize_t
user32.SetWindowLongPtrW.argtypes = [wintypes.HWND, ctypes.c_int, ctypes.c_ssize_t]
user32.SetWindowLongPtrW.restype = ctypes.c_ssize_t

WM_QUIT = 0x0012
WM_HOTKEY = 0x0312
WM_APP = 0x8000
MOD_ALT, MOD_CONTROL, MOD_SHIFT, MOD_WIN, MOD_NOREPEAT = 0x1, 0x2, 0x4, 0x8, 0x4000
WDA_EXCLUDEFROMCAPTURE = 0x11
INPUT_KEYBOARD, KEYEVENTF_KEYUP, KEYEVENTF_UNICODE, VK_RETURN = 1, 0x2, 0x4, 0x0D
GWL_EXSTYLE, WS_EX_TOPMOST, WS_EX_TOOLWINDOW, WS_EX_NOACTIVATE = -20, 0x8, 0x80, 0x08000000
ERROR_ALREADY_EXISTS = 183

_MODIFIERS = {
    "ctrl": MOD_CONTROL, "control": MOD_CONTROL, "alt": MOD_ALT,
    "shift": MOD_SHIFT, "maj": MOD_SHIFT, "win": MOD_WIN, "meta": MOD_WIN,
}
_NAMED_KEYS = {
    "space": 0x20, "espace": 0x20, "pause": 0x13, "insert": 0x2D, "ins": 0x2D, "home": 0x24,
    "end": 0x23, "pgup": 0x21, "pageup": 0x21, "pgdown": 0x22, "pagedown": 0x22,
    "scrolllock": 0x91, "print": 0x2C, "enter": 0x0D, "return": 0x0D, "tab": 0x09,
    "backspace": 0x08, "esc": 0x1B, "escape": 0x1B, "del": 0x2E, "delete": 0x2E,
}


class MONITORINFOEXW(ctypes.Structure):
    _fields_ = [
        ("cbSize", wintypes.DWORD),
        ("rcMonitor", wintypes.RECT),
        ("rcWork", wintypes.RECT),
        ("dwFlags", wintypes.DWORD),
        ("szDevice", wintypes.WCHAR * 32),
    ]


def parse_hotkey(combo: str) -> tuple[int, int]:
    """« Ctrl+Alt+Space » -> (modificateurs, code de touche virtuelle)."""
    parts = [p.strip().lower() for p in combo.split("+")]
    if not parts or not parts[-1]:
        raise ValueError(f"raccourci vide : {combo!r}")
    mods = 0
    for part in parts[:-1]:
        if part not in _MODIFIERS:
            raise ValueError(f"modificateur inconnu : {part}")
        mods |= _MODIFIERS[part]
    key = parts[-1]
    if key in _NAMED_KEYS:
        vk = _NAMED_KEYS[key]
    elif re.fullmatch(r"f([1-9]|1\d|2[0-4])", key):
        vk = 0x70 + int(key[1:]) - 1
    elif re.fullmatch(r"[a-z0-9]", key):
        vk = ord(key.upper())
    else:
        raise ValueError(f"touche inconnue : {key}")
    return mods, vk


class HotkeyManager:
    """Raccourcis clavier globaux par RegisterHotKey : pas de hook clavier, rien de suspect pour l'antivirus."""

    def __init__(self, on_hotkey: Callable[[str], None]):
        self._on_hotkey = on_hotkey
        self._commands: queue.Queue = queue.Queue()
        self._ids: dict[str, int] = {}
        self._names: dict[int, str] = {}
        self._next_id = 1
        self._thread_id = 0
        self._ready = threading.Event()
        self._thread = threading.Thread(target=self._run, name="hotkeys", daemon=True)

    def start(self) -> None:
        self._thread.start()
        self._ready.wait(5)

    def register(self, name: str, combo: str) -> bool:
        return self._call("register", name, combo)

    def unregister(self, name: str) -> None:
        self._call("unregister", name, "")

    def stop(self) -> None:
        if self._thread_id:
            user32.PostThreadMessageW(self._thread_id, WM_QUIT, 0, 0)
            self._thread.join(2)

    def _call(self, action: str, name: str, combo: str) -> bool:
        done, box = threading.Event(), {}
        self._commands.put((action, name, combo, done, box))
        user32.PostThreadMessageW(self._thread_id, WM_APP, 0, 0)
        done.wait(2)
        return box.get("ok", False)

    def _run(self) -> None:
        msg = wintypes.MSG()
        user32.PeekMessageW(ctypes.byref(msg), None, 0, 0, 0)  # crée la file de messages du thread
        self._thread_id = kernel32.GetCurrentThreadId()
        self._ready.set()
        while user32.GetMessageW(ctypes.byref(msg), None, 0, 0) > 0:
            if msg.message == WM_HOTKEY:
                name = self._names.get(int(msg.wParam))
                if name:
                    try:
                        self._on_hotkey(name)
                    except Exception:
                        log.exception("Erreur dans le raccourci %s", name)
            elif msg.message == WM_APP:
                self._drain_commands()
        for hk_id in list(self._names):
            user32.UnregisterHotKey(None, hk_id)

    def _drain_commands(self) -> None:
        while True:
            try:
                action, name, combo, done, box = self._commands.get_nowait()
            except queue.Empty:
                return
            self._remove(name)
            if action == "register":
                try:
                    mods, vk = parse_hotkey(combo)
                except ValueError as exc:
                    log.warning("Raccourci invalide %r : %s", combo, exc)
                    box["ok"] = False
                else:
                    hk_id = self._next_id
                    self._next_id += 1
                    ok = bool(user32.RegisterHotKey(None, hk_id, mods | MOD_NOREPEAT, vk))
                    if ok:
                        self._ids[name], self._names[hk_id] = hk_id, name
                    else:
                        log.warning("Raccourci %s déjà utilisé par une autre appli (erreur %s)",
                                    combo, ctypes.get_last_error())
                    box["ok"] = ok
            else:
                box["ok"] = True
            done.set()

    def _remove(self, name: str) -> None:
        hk_id = self._ids.pop(name, None)
        if hk_id is not None:
            user32.UnregisterHotKey(None, hk_id)
            self._names.pop(hk_id, None)


def key_events(text: str) -> list[tuple[int, int, int]]:
    """Frappes (touche, caractère UTF-16, drapeaux) qui écrivent ce texte, quelle que soit la disposition du clavier."""
    events = []
    for char in text:
        if char == "\n":
            events += [(VK_RETURN, 0, 0), (VK_RETURN, 0, KEYEVENTF_KEYUP)]
            continue
        data = char.encode("utf-16-le")
        for i in range(0, len(data), 2):  # deux unités pour un emoji
            unit = int.from_bytes(data[i:i + 2], "little")
            events += [(0, unit, KEYEVENTF_UNICODE), (0, unit, KEYEVENTF_UNICODE | KEYEVENTF_KEYUP)]
    return events


def type_text(text: str) -> bool:
    """Écrit le texte là où est le curseur, dans la fenêtre active. False : Windows l'a refusé (appli lancée en
    administrateur, par exemple)."""
    events = key_events(text)
    if not events:
        return True
    inputs = (INPUT * len(events))()
    for slot, (vk, scan, flags) in zip(inputs, events):
        slot.type = INPUT_KEYBOARD
        slot.u.ki = KEYBDINPUT(vk, scan, flags, 0, 0)
    return user32.SendInput(len(events), inputs, ctypes.sizeof(INPUT)) == len(events)


def no_activate(hwnd: int) -> None:
    """Fenêtre qui reste au premier plan sans jamais prendre le clavier : le texte va toujours au bon endroit."""
    style = user32.GetWindowLongPtrW(wintypes.HWND(hwnd), GWL_EXSTYLE)
    user32.SetWindowLongPtrW(wintypes.HWND(hwnd), GWL_EXSTYLE,
                             style | WS_EX_NOACTIVATE | WS_EX_TOOLWINDOW | WS_EX_TOPMOST)


def set_dpi_awareness() -> None:
    """Coordonnées en vrais pixels sur tous les écrans (Qt le fait déjà ; utile hors interface)."""
    try:
        user32.SetProcessDpiAwarenessContext(ctypes.c_void_p(-4))  # PER_MONITOR_AWARE_V2
    except (AttributeError, OSError):
        pass


def cursor_pos() -> tuple[int, int] | None:
    pt = wintypes.POINT()
    if not user32.GetCursorPos(ctypes.byref(pt)):
        return None
    return pt.x, pt.y


def window_rect(hwnd: int) -> tuple[int, int, int, int] | None:
    rect = wintypes.RECT()
    if not user32.GetWindowRect(wintypes.HWND(hwnd), ctypes.byref(rect)):
        return None
    return rect.left, rect.top, rect.right, rect.bottom


def exclude_from_capture(hwnd: int) -> bool:
    """Rend une fenêtre invisible dans les captures d'écran (Windows 10 2004 et plus)."""
    return bool(user32.SetWindowDisplayAffinity(wintypes.HWND(hwnd), WDA_EXCLUDEFROMCAPTURE))


def monitor_device_names() -> dict[tuple[int, int, int, int], str]:
    """(left, top, largeur, hauteur) -> « \\\\.\\DISPLAY2 », pour numéroter les écrans comme Windows."""
    result: dict[tuple[int, int, int, int], str] = {}

    def callback(hmon, _hdc, _rect, _lparam):
        info = MONITORINFOEXW()
        info.cbSize = ctypes.sizeof(MONITORINFOEXW)
        if user32.GetMonitorInfoW(hmon, ctypes.byref(info)):
            r = info.rcMonitor
            result[(r.left, r.top, r.right - r.left, r.bottom - r.top)] = info.szDevice
        return True

    user32.EnumDisplayMonitors(None, None, MONITORENUMPROC(callback), 0)
    return result


def acquire_single_instance(name: str) -> int | None:
    """Mutex nommé : None si l'application tourne déjà."""
    handle = kernel32.CreateMutexW(None, False, name)
    if not handle or ctypes.get_last_error() == ERROR_ALREADY_EXISTS:
        return None
    return handle


def display_hotkey(combo: str) -> str:
    """Raccourci affiché en français : « Ctrl+Alt+Espace », « Maj+F8 »."""
    names = {
        "space": "Espace", "espace": "Espace", "shift": "Maj", "maj": "Maj", "del": "Suppr", "delete": "Suppr",
        "ins": "Inser", "insert": "Inser", "esc": "Échap", "escape": "Échap", "enter": "Entrée",
        "return": "Entrée", "print": "Impr. écran", "meta": "Win", "win": "Win",
    }
    return "+".join(names.get(part.strip().lower(), part.strip()) for part in combo.split("+"))


def dark_title_bar(hwnd: int) -> None:
    """Barre de titre sombre (Windows 11), assortie au reste de la fenêtre."""
    try:
        value = ctypes.c_int(1)
        ctypes.windll.dwmapi.DwmSetWindowAttribute(wintypes.HWND(hwnd), 20, ctypes.byref(value), ctypes.sizeof(value))
    except (AttributeError, OSError):
        pass


def set_app_id(app_id: str) -> None:
    """Icône et regroupement propres dans la barre des tâches (sinon Windows y montre l'icône de Python)."""
    try:
        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(app_id)
    except (AttributeError, OSError):
        pass
