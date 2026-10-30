#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
TruckPlay 2.0 — медиасистема кабины для Euro Truck Simulator 2.

Настоящее приложение на WebView2: окно без рамки, всегда поверх игры,
мышь по умолчанию проходит сквозь экран. Внутри — домашний экран,
YouTube, радио (станции из ETS2), музыка (папка music игры), настройки.

Движок ETS2 не умеет проигрывать видео, поэтому экран рисуется отдельным
окном поверх игры — как «голова» мультимедиа в кабине.
"""

from __future__ import annotations

import ctypes
import ctypes.wintypes as wt
import json
import mimetypes
import os
import re
import subprocess
import sys
import threading
import time
import urllib.parse
from collections import deque
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer

import webview

import telemetry as telemetry_mod

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

FROZEN = bool(getattr(sys, "frozen", False))
_HERE = os.path.dirname(os.path.abspath(__file__))
BASE = os.path.dirname(sys.executable) if FROZEN else os.path.dirname(_HERE)
RES = getattr(sys, "_MEIPASS", _HERE)        # ресурсы (app.html) внутри .exe
CFG_PATH = os.path.join(BASE, "config.json")
STATE_DIR = os.path.join(os.environ.get("LOCALAPPDATA", BASE), "TruckPlay")
STORE_DIR = os.path.join(STATE_DIR, "webview")
TITLE = "TruckPlay"

os.makedirs(STATE_DIR, exist_ok=True)
if FROZEN or sys.stdout is None or sys.stderr is None:   # сборка без консоли
    _log = open(os.path.join(STATE_DIR, "truckplay.log"), "w", encoding="utf-8", buffering=1)
    sys.stdout = _log
    sys.stderr = _log
# --------------------------------------------------------------------------
# конфиг
# --------------------------------------------------------------------------

DEFAULT_CONFIG = {
    "window": {
        "x": None,
        "y": None,
        "width": None,
        "height": None,
        "always_on_top": True,
        "click_through": True,
    },
    "player": {
        "source": "https://www.youtube.com/watch?v=4xDzrJKXOOY",
        "loop": True,
        "volume": 55,
        "seek_step": 10,
    },
    "music": {"folder": None},
    "radio": {"source": "ets2"},
    "look": {"bezel": True, "glare": True, "theme": "carplay"},
    "hotkeys": {
        "click_through": "ctrl+alt+y",
        "visible": "ctrl+alt+h",
        "play_pause": "ctrl+alt+p",
        "next": "ctrl+alt+right",
        "prev": "ctrl+alt+left",
        "seek_fwd": "ctrl+alt+period",
        "seek_back": "ctrl+alt+comma",
        "vol_up": "ctrl+alt+up",
        "vol_down": "ctrl+alt+down",
        "mute": "ctrl+alt+z",
        "browse": "ctrl+alt+s",
        "back": "ctrl+alt+b",
        "reload": "ctrl+alt+r",
        "quit": "ctrl+alt+q",
        "move_up": "ctrl+alt+shift+up",
        "move_down": "ctrl+alt+shift+down",
        "move_left": "ctrl+alt+shift+left",
        "move_right": "ctrl+alt+shift+right",
    },
}


def deep_merge(base: dict, extra: dict) -> dict:
    out = dict(base)
    for k, v in (extra or {}).items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = deep_merge(out[k], v)
        else:
            out[k] = v
    return out


def load_config() -> dict:
    cfg = DEFAULT_CONFIG
    if os.path.exists(CFG_PATH):
        try:
            with open(CFG_PATH, "r", encoding="utf-8-sig") as f:
                cfg = deep_merge(DEFAULT_CONFIG, json.load(f))
        except Exception as e:
            print(f"[!] config.json не прочитан ({e}), беру значения по умолчанию")
    else:
        save_config(DEFAULT_CONFIG)
    return cfg


def save_config(cfg: dict) -> None:
    try:
        os.makedirs(os.path.dirname(CFG_PATH), exist_ok=True)
        with open(CFG_PATH, "w", encoding="utf-8") as f:
            json.dump(cfg, f, ensure_ascii=False, indent=2)
    except Exception as e:
        print(f"[!] не могу сохранить config.json: {e}")


# --------------------------------------------------------------------------
# Win32
# --------------------------------------------------------------------------

user32 = ctypes.WinDLL("user32", use_last_error=True)
kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)

# координаты в физических пикселях (иначе при масштабе 125/150% всё уезжает)
try:
    ctypes.windll.shcore.SetProcessDpiAwareness(2)
except Exception:
    try:
        user32.SetProcessDPIAware()
    except Exception:
        pass

GWL_STYLE = -16
GWL_EXSTYLE = -20
WS_CAPTION = 0x00C00000
WS_THICKFRAME = 0x00040000
WS_POPUP = 0x80000000
WS_VISIBLE = 0x10000000
WS_EX_LAYERED = 0x00080000
WS_EX_TRANSPARENT = 0x00000020
WS_EX_TOPMOST = 0x00000008
HWND_TOPMOST = -1
SWP_NOSIZE = 0x0001
SWP_NOMOVE = 0x0002
SWP_NOACTIVATE = 0x0010
SWP_FRAMECHANGED = 0x0020
SWP_SHOWWINDOW = 0x0040
SW_HIDE = 0
SW_SHOWNOACTIVATE = 4
SW_RESTORE = 9
WM_NCLBUTTONDOWN = 0x00A1
WM_QUIT = 0x0012
WM_HOTKEY = 0x0312
HTCAPTION = 2
MOD_ALT = 0x0001
MOD_CONTROL = 0x0002
MOD_SHIFT = 0x0004
MOD_WIN = 0x0008
MOD_NOREPEAT = 0x4000
VK_LBUTTON = 0x01

GetWindowLongPtr = getattr(user32, "GetWindowLongPtrW", None) or user32.GetWindowLongW
SetWindowLongPtr = getattr(user32, "SetWindowLongPtrW", None) or user32.SetWindowLongW
GetWindowLongPtr.restype = ctypes.c_ssize_t
GetWindowLongPtr.argtypes = [wt.HWND, ctypes.c_int]
SetWindowLongPtr.restype = ctypes.c_ssize_t
SetWindowLongPtr.argtypes = [wt.HWND, ctypes.c_int, ctypes.c_ssize_t]

user32.SetWindowPos.argtypes = [wt.HWND, wt.HWND, ctypes.c_int, ctypes.c_int,
                                ctypes.c_int, ctypes.c_int, ctypes.c_uint]
user32.SetLayeredWindowAttributes.argtypes = [wt.HWND, ctypes.c_uint32, ctypes.c_ubyte, ctypes.c_uint]
user32.GetWindowRect.argtypes = [wt.HWND, ctypes.POINTER(wt.RECT)]
user32.ShowWindow.argtypes = [wt.HWND, ctypes.c_int]
user32.RegisterHotKey.argtypes = [wt.HWND, ctypes.c_int, ctypes.c_uint, ctypes.c_uint]
user32.UnregisterHotKey.argtypes = [wt.HWND, ctypes.c_int]
user32.GetAsyncKeyState.restype = ctypes.c_short
user32.GetAsyncKeyState.argtypes = [ctypes.c_int]
user32.GetCursorPos.argtypes = [ctypes.POINTER(wt.POINT)]
user32.PostMessageW.argtypes = [wt.HWND, ctypes.c_uint, wt.WPARAM, wt.LPARAM]
user32.GetForegroundWindow.restype = wt.HWND
user32.IsWindow.restype = wt.BOOL
user32.IsWindowVisible.restype = wt.BOOL
user32.IsIconic.restype = wt.BOOL
user32.IsIconic.argtypes = [wt.HWND]
user32.WindowFromPoint.restype = wt.HWND
user32.WindowFromPoint.argtypes = [wt.POINT]
user32.GetMessageW.restype = ctypes.c_int
user32.GetMessageW.argtypes = [ctypes.POINTER(wt.MSG), wt.HWND, ctypes.c_uint, ctypes.c_uint]
user32.TranslateMessage.argtypes = [ctypes.POINTER(wt.MSG)]
user32.DispatchMessageW.argtypes = [ctypes.POINTER(wt.MSG)]

CREATE_NO_WINDOW = 0x08000000


def run_quiet(args) -> str:
    try:
        return subprocess.run(args, capture_output=True, text=True, errors="replace",
                              creationflags=CREATE_NO_WINDOW).stdout or ""
    except Exception:
        return ""


def primary_size() -> tuple[int, int]:
    return user32.GetSystemMetrics(0), user32.GetSystemMetrics(1)


def dpi_scale() -> float:
    try:
        gdi32 = ctypes.WinDLL("gdi32")
        gdi32.GetDeviceCaps.argtypes = [ctypes.c_void_p, ctypes.c_int]
        gdi32.GetDeviceCaps.restype = ctypes.c_int
        user32.GetDC.restype = ctypes.c_void_p
        user32.GetDC.argtypes = [ctypes.c_void_p]
        user32.ReleaseDC.argtypes = [ctypes.c_void_p, ctypes.c_void_p]
        hdc = user32.GetDC(None)
        dpi = gdi32.GetDeviceCaps(hdc, 88)  # LOGPIXELSX
        user32.ReleaseDC(None, hdc)
        return (dpi or 96) / 96.0
    except Exception:
        return 1.0


def rect_of(hwnd) -> tuple[int, int, int, int]:
    r = wt.RECT()
    user32.GetWindowRect(hwnd, ctypes.byref(r))
    return r.left, r.top, r.right - r.left, r.bottom - r.top


def enum_windows():
    out = []
    proc = ctypes.WINFUNCTYPE(wt.BOOL, wt.HWND, wt.LPARAM)

    def cb(hwnd, _):
        out.append(hwnd)
        return True

    user32.EnumWindows(proc(cb), 0)
    return out


def enum_children(hwnd):
    out = []
    proc = ctypes.WINFUNCTYPE(wt.BOOL, wt.HWND, wt.LPARAM)

    def cb(h, _):
        out.append(h)
        return True

    user32.EnumChildWindows(hwnd, proc(cb), 0)
    return out


def window_title(hwnd) -> str:
    buf = ctypes.create_unicode_buffer(512)
    user32.GetWindowTextW(hwnd, buf, 512)
    return buf.value


def window_pid(hwnd) -> int:
    pid = wt.DWORD()
    user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
    return pid.value


def find_own_window() -> int:
    """Главное окно нашего приложения (ищем по PID процесса)."""
    pid = os.getpid()
    best = 0
    best_area = 0
    for h in enum_windows():
        if window_pid(h) != pid or not user32.IsWindowVisible(h):
            continue
        t = window_title(h)
        if TITLE.lower() not in t.lower():
            continue
        x, y, w, hh = rect_of(h)
        if w < 200 or hh < 100:
            continue
        if w * hh > best_area:
            best, best_area = h, w * hh
    return best


# --------------------------------------------------------------------------
# состояние + локальный сервер
# --------------------------------------------------------------------------

class State:
    def __init__(self):
        self.lock = threading.Lock()
        self.events: deque = deque(maxlen=64)
        self.next_id = 1
        self.click_through = True
        self.visible = True
        self.mode = "app"

    def push(self, name: str, arg=None):
        with self.lock:
            self.events.append({"id": self.next_id, "name": name, "arg": arg})
            self.next_id += 1

    def snapshot(self, since: int) -> dict:
        with self.lock:
            evs = [e for e in self.events if e["id"] > since]
            return {
                "next": self.next_id - 1,
                "events": evs,
                "state": {"click_through": self.click_through,
                          "visible": self.visible, "mode": self.mode},
            }


STATE = State()
APP = None

DOCS_ETS2 = os.path.join(os.path.expanduser("~"), "Documents", "Euro Truck Simulator 2")
AUDIO_EXT = (".mp3", ".ogg", ".flac", ".wav", ".m4a", ".aac", ".opus")

PLUGIN_DLL = "truckplay_telemetry.dll"


def game_plugins_dir() -> str | None:
    """Папка bin/win_x64/plugins установленной игры."""
    cands = []
    try:
        import winreg
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, r"Software\Valve\Steam") as k:
            steam = winreg.QueryValueEx(k, "SteamPath")[0]
        cands.append(os.path.join(steam, "steamapps", "common", "Euro Truck Simulator 2"))
    except Exception:
        pass
    cands += [
        r"C:\Program Files (x86)\Steam\steamapps\common\Euro Truck Simulator 2",
        r"C:\Program Files\Steam\steamapps\common\Euro Truck Simulator 2",
        r"D:\SteamLibrary\steamapps\common\Euro Truck Simulator 2",
        r"E:\SteamLibrary\steamapps\common\Euro Truck Simulator 2",
        r"C:\SteamLibrary\steamapps\common\Euro Truck Simulator 2",
    ]
    for c in cands:
        p = os.path.join(c, "bin", "win_x64", "plugins")
        if os.path.isdir(p):
            return p
    return None


PLUGIN_DIR = game_plugins_dir() or ""
TELEMETRY = None

BUILTIN_STATIONS = [
    {"name": "Europa Plus", "url": "http://ep128server.streamr.ru:8030/ep128", "genre": "Pop"},
    {"name": "Record", "url": "http://air.radiorecord.ru:8101/rr_320", "genre": "Dance"},
    {"name": "Ретро FM", "url": "http://retroserver.streamr.ru:8043/retro128", "genre": "Retro"},
]


def music_folder() -> str:
    folder = (APP.cfg.get("music") or {}).get("folder") if APP else None
    for c in (folder,
              os.path.join(DOCS_ETS2, "music"),
              os.path.join(os.path.expanduser("~"), "Music")):
        if c and os.path.isdir(c):
            return c
    return ""


def read_stations() -> list:
    src = (APP.cfg.get("radio") or {}).get("source", "ets2") if APP else "ets2"
    sii = os.path.join(DOCS_ETS2, "live_streams.sii")
    out: list = []
    if src == "ets2" and os.path.exists(sii):
        try:
            with open(sii, "r", encoding="utf-8", errors="replace") as f:
                text = f.read()
            for m in re.finditer(r'stream_data\[\d+\]:\s*"([^"]+)"', text):
                parts = m.group(1).split("|")
                if len(parts) >= 2 and parts[0].startswith(("http://", "https://")):
                    out.append({"name": parts[1], "url": parts[0],
                                "genre": parts[2] if len(parts) > 2 else "",
                                "bitrate": parts[4] if len(parts) > 4 else ""})
        except Exception as e:
            print("[!] live_streams.sii:", e)
    return out or BUILTIN_STATIONS


def read_tracks(limit: int = 800) -> list:
    root = music_folder()
    out: list = []
    if not root:
        return out
    for base, dirs, files in os.walk(root):
        if os.path.relpath(base, root).count(os.sep) >= 2:
            dirs[:] = []
        for fn in sorted(files):
            if not fn.lower().endswith(AUDIO_EXT):
                continue
            full = os.path.join(base, fn)
            try:
                size = os.path.getsize(full)
            except OSError:
                continue
            out.append({"name": os.path.splitext(fn)[0],
                        "file": os.path.relpath(full, root).replace("\\", "/"),
                        "size": size})
            if len(out) >= limit:
                return out
    return out


def library() -> dict:
    return {"stations": read_stations(), "tracks": read_tracks(),
            "music_folder": music_folder()}


class Handler(SimpleHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def __init__(self, *a, **kw):
        super().__init__(*a, directory=BASE, **kw)

    def log_message(self, *a):
        pass

    def end_headers(self):
        self.send_header("Cache-Control", "no-store, max-age=0")
        self.send_header("Access-Control-Allow-Origin", "*")
        super().end_headers()

    def _json(self, obj):
        body = json.dumps(obj, ensure_ascii=False).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _file(self, name: str, ctype: str):
        try:
            with open(os.path.join(RES, name), "rb") as f:
                body = f.read()
        except OSError:
            self.send_error(404)
            return
        self.send_response(200)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _media(self, rel: str):
        root = os.path.normpath(music_folder())
        target = os.path.normpath(os.path.join(root, rel)) if rel else ""
        if not root or not target.startswith(root + os.sep) or not os.path.isfile(target):
            self.send_error(404, "not found")
            return
        size = os.path.getsize(target)
        ctype = mimetypes.guess_type(target)[0] or "application/octet-stream"
        start, end, status = 0, size - 1, 200
        rng = self.headers.get("Range")
        if rng:
            m = re.match(r"bytes=(\d*)-(\d*)", rng)
            if m and (m.group(1) or m.group(2)):
                if m.group(1):
                    start = int(m.group(1))
                if m.group(2):
                    end = min(int(m.group(2)), size - 1)
                if start > end:
                    start, end, status = 0, size - 1, 200
                else:
                    status = 206
        length = end - start + 1
        self.send_response(status)
        self.send_header("Content-Type", ctype)
        self.send_header("Accept-Ranges", "bytes")
        self.send_header("Content-Length", str(length))
        if status == 206:
            self.send_header("Content-Range", f"bytes {start}-{end}/{size}")
        self.end_headers()
        try:
            with open(target, "rb") as f:
                f.seek(start)
                left = length
                while left > 0:
                    chunk = f.read(min(65536, left))
                    if not chunk:
                        break
                    self.wfile.write(chunk)
                    left -= len(chunk)
        except (ConnectionError, OSError):
            pass

    def do_GET(self):
        p = urllib.parse.urlparse(self.path)
        q = urllib.parse.parse_qs(p.query)
        if p.path in ("/", "/index.html", "/app.html"):
            return self._file("app.html", "text/html; charset=utf-8")
        if p.path == "/config":
            return self._json(APP.cfg)
        if p.path == "/events":
            try:
                since = int(q.get("since", ["0"])[0])
            except ValueError:
                since = 0
            return self._json(STATE.snapshot(since))
        if p.path == "/api/library":
            return self._json(library())
        if p.path == "/api/install_plugin":
            return self._json({"message": cmd_install_plugin()})
        if p.path == "/api/uninstall_plugin":
            return self._json({"message": cmd_uninstall_plugin()})
        if p.path == "/api/settings":
            return self._json(APP.apply_settings(q) if APP else {"ok": False})
        if p.path == "/api/telemetry":
            tel = TELEMETRY.snapshot() if TELEMETRY else None
            game = "eurotrucks2.exe" in run_quiet(
                ["tasklist", "/FI", "IMAGENAME eq eurotrucks2.exe"]).lower()
            return self._json({
                "ok": tel is not None,
                "game": game,
                "plugin": os.path.exists(os.path.join(PLUGIN_DIR, PLUGIN_DLL)),
                "data": tel,
            })
        if p.path == "/media":
            return self._media(q.get("f", [""])[0])
        if p.path == "/cmd":
            name = q.get("name", [""])[0]
            arg = q.get("arg", [None])[0]
            if name and APP:
                threading.Thread(target=APP.action, args=(name, arg), daemon=True).start()
            return self._json({"ok": bool(name), "cmd": name})
        if p.path == "/eval":
            expr = q.get("e", [""])[0]
            try:
                val = APP.win.evaluate_js(expr) if APP and APP.win else None
            except Exception as e:
                val = f"ERR {e}"
            return self._json({"result": val})
        if p.path == "/debug":
            h = APP.hwnd if APP else 0
            fg = user32.GetForegroundWindow()
            return self._json({
                "hwnd": h, "hwnd_alive": bool(h and user32.IsWindow(h)),
                "rect": rect_of(h) if h else None,
                "exstyle": hex(GetWindowLongPtr(h, GWL_EXSTYLE)) if h else None,
                "foreground": window_title(fg), "foreground_is_ours": fg == h,
                "click_through": APP.click_through if APP else None,
                "dpi": [APP.scale if APP else None],
            })
        return super().do_GET()


def free_port(start: int) -> int:
    import socket
    for p in range(start, start + 40):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            try:
                s.bind(("127.0.0.1", p))
                return p
            except OSError:
                continue
    raise RuntimeError("нет свободного порта")


# --------------------------------------------------------------------------
# приложение
# --------------------------------------------------------------------------

class App:
    def __init__(self, cfg: dict):
        global APP
        APP = self
        self.cfg = cfg
        self.main_tid = kernel32.GetCurrentThreadId()
        self.http_port = free_port(47615)
        self.url = f"http://127.0.0.1:{self.http_port}/"
        self.hwnd = 0
        self.win = None
        self.yt_win = None
        self.running = True
        self.visible = True
        # если игра не запущена — приложение ведёт себя как обычная программа,
        # мышь активна; при запущенной игре экран становится прозрачным для мыши
        game_running = "eurotrucks2.exe" in run_quiet(
            ["tasklist", "/FI", "IMAGENAME eq eurotrucks2.exe"]).lower()
        self.click_through = bool(cfg["window"].get("click_through", True)) and game_running
        self.hotkey_ids: dict[int, str] = {}
        self.dragging = False
        self.scale = dpi_scale()
        self._defaults()

    # -- геометрия ----------------------------------------------------------
    def _defaults(self):
        w = self.cfg["window"]
        sw, sh = primary_size()
        if not w.get("height"):
            w["height"] = int(sh * 0.36) // 2 * 2
        if not w.get("width"):
            w["width"] = int(w["height"] * 16 / 9) // 2 * 2
        if w.get("x") is None:
            w["x"] = max(0, sw - w["width"] - int(sw * 0.035))
        if w.get("y") is None:
            w["y"] = max(0, sh - w["height"] - int(sh * 0.13))
        # защита от «уехавших» или развёрнутых на весь экран значений
        w["width"] = max(520, min(int(w["width"]), sw))
        w["height"] = max(300, min(int(w["height"]), sh))
        w["x"] = max(-w["width"] // 2, min(int(w["x"]), sw - 160))
        w["y"] = max(0, min(int(w["y"]), sh - 120))

    # -- запуск -------------------------------------------------------------
    def start_server(self):
        srv = ThreadingHTTPServer(("127.0.0.1", self.http_port), Handler)
        srv.daemon_threads = True
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        return srv

    def run(self):
        global TELEMETRY
        self.start_server()
        TELEMETRY = telemetry_mod.Telemetry()
        TELEMETRY.start()
        threading.Thread(target=self.hotkey_loop, daemon=True).start()
        threading.Thread(target=self.watchdog, daemon=True).start()

        w = self.cfg["window"]
        sc = self.scale or 1.0
        self.win = webview.create_window(
            TITLE, self.url,
            x=int(w["x"] / sc), y=int(w["y"] / sc),
            width=int(w["width"] / sc), height=int(w["height"] / sc),
            frameless=True, easy_drag=False, on_top=bool(w.get("always_on_top", True)),
            resizable=True, background_color="#08090c", text_select=False, shadow=True,
            focus=False,
        )
        if self.win:
            self.win.events.loaded += self.on_loaded
            self.win.events.closed += self.on_closed
        webview.start(func=self.after_start, private_mode=False, storage_path=STORE_DIR,
                      gui="edgechromium")

    def after_start(self):
        self.hwnd = find_own_window()
        if self.hwnd:
            self.set_click_through(self.click_through)
        print(f"  Экран: {self.cfg['window']['width']}x{self.cfg['window']['height']} "
              f"в точке {self.cfg['window']['x']},{self.cfg['window']['y']}")
        print("  Работает. Выход - Ctrl+Alt+Q\n", flush=True)

    def on_loaded(self):
        threading.Thread(target=self._after_load, daemon=True).start()

    def _after_load(self):
        for _ in range(20):
            h = find_own_window()
            if h:
                self.hwnd = h
                try:
                    self.place_content(*self.content_cfg())
                except Exception:
                    pass
                self.set_click_through(self.click_through)
                return
            time.sleep(0.2)

    def on_closed(self):
        self.running = False

    def content_cfg(self):
        w = self.cfg["window"]
        return w["x"], w["y"], w["width"], w["height"]

    def apply_settings(self, q: dict) -> dict:
        """Изменения из экрана настроек: ?width=..&height=..&x=..&y=..&theme=.."""
        w = self.cfg["window"]

        def num(name):
            try:
                return int(float(q[name][0]))
            except Exception:
                return None

        def flag(name):
            try:
                return bool(int(q[name][0]))
            except Exception:
                return None

        sw, sh = primary_size()
        nw, nh = num("width"), num("height")
        if nw and nh:
            w["width"] = max(520, min(nw, sw))
            w["height"] = max(300, min(nh, sh))
        x, y = num("x"), num("y")
        pos = q.get("pos", [""])[0]
        if pos in ("tl", "tr", "bl", "br", "center"):
            ww, hh = w["width"], w["height"]
            m = int(sw * 0.02)
            if pos == "tl":
                x, y = m, m
            elif pos == "tr":
                x, y = sw - ww - m, m
            elif pos == "bl":
                x, y = m, sh - hh - m
            elif pos == "br":
                x, y = sw - ww - m, sh - hh - m
            else:
                x, y = (sw - ww) // 2, (sh - hh) // 2
        if x is None:
            x = w["x"]
        if y is None:
            y = w["y"]
        if nw or nh or num("x") is not None or num("y") is not None or pos in ("tl", "tr", "bl", "br", "center"):
            self.place_content(x, y, w["width"], w["height"])

        top = flag("always_on_top")
        if top is not None:
            w["always_on_top"] = top
            self.set_topmost(top)

        ct = flag("click_through")
        if ct is not None:
            w["click_through"] = ct
            self.set_click_through(ct)

        look = self.cfg.setdefault("look", {})
        for key in ("theme", "bezel", "glare"):
            if key in q:
                val = q[key][0]
                look[key] = (val not in ("0", "false", "")) if key != "theme" else val

        if "volume" in q:
            try:
                self.cfg["player"]["volume"] = max(0, min(100, int(float(q["volume"][0]))))
            except Exception:
                pass

        save_config(self.cfg)
        self.save_rect()
        return {"ok": True, "config": self.cfg}

    def place_content(self, x, y, w, h):
        user32.SetWindowPos(self.hwnd, HWND_TOPMOST if self.cfg["window"]["always_on_top"] else None,
                            int(x), int(y), int(w), int(h),
                            SWP_NOACTIVATE | SWP_SHOWWINDOW)

    def set_topmost(self, on: bool):
        if not self.hwnd:
            return
        user32.SetWindowPos(self.hwnd, HWND_TOPMOST if on else None,
                            0, 0, 0, 0, SWP_NOMOVE | SWP_NOSIZE | SWP_NOACTIVATE)

    def set_click_through(self, on: bool):
        if not self.hwnd:
            self.click_through = bool(on)
            STATE.click_through = self.click_through
            return
        self.click_through = bool(on)
        STATE.click_through = self.click_through
        targets = [self.hwnd]
        for h in targets:
            try:
                ex = GetWindowLongPtr(h, GWL_EXSTYLE)
                if on:
                    ex |= WS_EX_TRANSPARENT
                else:
                    ex &= ~WS_EX_TRANSPARENT
                SetWindowLongPtr(h, GWL_EXSTYLE, ex)
            except Exception:
                pass
        self.set_topmost(bool(self.cfg["window"].get("always_on_top", True)))
        if not on:
            user32.SetForegroundWindow(self.hwnd)
        self.toast("МЫШЬ: " + ("ВКЛ" if on else "АКТИВНА"))

    def show(self, on: bool):
        self.visible = bool(on)
        STATE.visible = self.visible
        if not self.hwnd:
            return
        user32.ShowWindow(self.hwnd, SW_SHOWNOACTIVATE if on else SW_HIDE)
        if on:
            self.set_topmost(bool(self.cfg["window"].get("always_on_top", True)))

    def ensure_shown(self):
        fg = user32.GetForegroundWindow()
        user32.ShowWindow(self.hwnd, SW_SHOWNOACTIVATE)
        if user32.IsIconic(self.hwnd):
            user32.ShowWindow(self.hwnd, SW_RESTORE)
            if fg and fg != self.hwnd:
                user32.SetForegroundWindow(fg)
        self.place_content(*self.content_cfg())
        self.set_topmost(bool(self.cfg["window"].get("always_on_top", True)))

    # -- действия -----------------------------------------------------------
    def toast(self, text: str):
        STATE.push("toast", text)

    def action(self, name: str, arg=None):
        try:
            if name == "click_through":
                self.set_click_through(not self.click_through)
            elif name == "visible":
                self.show(not self.visible)
            elif name in ("play_pause", "next", "prev", "vol_up", "vol_down", "mute",
                          "seek_fwd", "seek_back", "view", "playvid", "search"):
                STATE.push(name, arg)
            elif name == "browse":
                self.open_youtube()
            elif name == "back":
                self.close_youtube()
                if self.hwnd:
                    user32.SetForegroundWindow(self.hwnd)
                self.toast("Экран в кабине")
            elif name == "reload":
                STATE.push("reload")
            elif name == "quit":
                self.quit()
            elif name in ("move_up", "move_down", "move_left", "move_right"):
                step = 16
                dx, dy = {"move_up": (0, -step), "move_down": (0, step),
                          "move_left": (-step, 0), "move_right": (step, 0)}[name]
                self.move(dx, dy)
        except Exception as e:
            print(f"[!] {name}: {e}")

    def open_youtube(self):
        """Отдельное окно с настоящим youtube.com — выбрать видео."""
        if self.yt_win is not None:
            try:
                self.yt_win.show()
                return
            except Exception:
                self.yt_win = None
        w = self.cfg["window"]
        sc = self.scale or 1.0
        try:
            self.yt_win = webview.create_window(
                "YouTube", "https://www.youtube.com",
                x=int(w["x"] / sc), y=int(w["y"] / sc),
                width=int(w["width"] / sc), height=int(w["height"] / sc),
                frameless=False, on_top=True, background_color="#0f0f0f",
            )
            self.yt_win.events.closed += self._yt_closed
            self.toast("Выберите видео — вернётся в кабину")
        except Exception as e:
            print("[!] youtube окно:", e)

    def _yt_closed(self):
        self.yt_win = None

    def close_youtube(self):
        if self.yt_win is not None:
            try:
                self.yt_win.destroy()
            except Exception:
                pass
            self.yt_win = None

    def yt_poll(self):
        """Если в окне YouTube открыли ролик — забираем его в наш плеер."""
        if self.yt_win is None:
            return
        try:
            url = self.yt_win.get_current_url() or ""
        except Exception:
            return
        m = re.search(r"[?&]v=([\w-]{11})", url)
        if m and "/watch" in url:
            vid = m.group(1)
            self.close_youtube()
            STATE.push("playvid", vid)
            if self.hwnd:
                user32.SetForegroundWindow(self.hwnd)
            self.toast("Играю в кабине")

    def reload(self):
        STATE.push("reload")
        self.toast("Перезагрузка")

    def move(self, dx, dy):
        x, y, w, h = self.content_cfg()
        self.cfg["window"].update({"x": max(-w + 100, x + dx), "y": max(-40, y + dy)})
        self.place_content(*self.content_cfg())
        self.save_rect()

    def save_rect(self):
        save_config(self.cfg)

    def quit(self):
        self.running = False
        self.save_rect()
        self.close_youtube()
        try:
            if self.win:
                self.win.destroy()
        except Exception:
            pass

    # -- горячие клавиши -----------------------------------------------------
    def register_hotkeys(self):
        mods = {"ctrl": MOD_CONTROL, "alt": MOD_ALT, "shift": MOD_SHIFT, "win": MOD_WIN}
        hkid = 1
        failed = []
        for action, combo in self.cfg["hotkeys"].items():
            try:
                m, vk = self.parse_combo(combo, mods)
            except Exception:
                failed.append(combo)
                continue
            repeat = action in ("vol_up", "vol_down", "move_up", "move_down",
                                "move_left", "move_right")
            if user32.RegisterHotKey(None, hkid, m | (0 if repeat else MOD_NOREPEAT), vk):
                self.hotkey_ids[hkid] = action
                hkid += 1
            else:
                failed.append(combo)
        if failed:
            print("[!] Не удалось занять комбинации (уже заняты другой программой):",
                  ", ".join(failed), flush=True)

    @staticmethod
    def parse_combo(combo: str, mods: dict):
        m = 0
        vk = None
        named = {"up": 0x26, "down": 0x28, "left": 0x25, "right": 0x27, "space": 0x20,
                 "enter": 0x0D, "esc": 0x1B, "comma": 0xBC, "period": 0xBE,
                 "minus": 0xBD, "plus": 0xBB, "tab": 0x09}
        for part in combo.lower().split("+"):
            part = part.strip()
            if part in mods:
                m |= mods[part]
            elif part in named:
                vk = named[part]
            elif len(part) == 1:
                vk = ord(part.upper())
            elif re.fullmatch(r"f\d{1,2}", part):
                vk = 0x70 + int(part[1:]) - 1
        if vk is None:
            raise ValueError(combo)
        return m, vk

    def hotkey_loop(self):
        # горячие клавиши регистрируются в том же потоке, который читает очередь
        self.register_hotkeys()
        msg = wt.MSG()
        while self.running:
            r = user32.GetMessageW(ctypes.byref(msg), None, 0, 0)
            if r <= 0:
                break
            if msg.message == WM_HOTKEY:
                action = self.hotkey_ids.get(int(msg.wParam))
                if action:
                    threading.Thread(target=self.action, args=(action,), daemon=True).start()
            user32.TranslateMessage(ctypes.byref(msg))
            user32.DispatchMessageW(ctypes.byref(msg))

    # -- фоновый сторож ------------------------------------------------------
    def watchdog(self):
        last_top = 0.0
        last_style = None
        while self.running:
            time.sleep(0.15)
            if not self.hwnd or not user32.IsWindow(self.hwnd):
                h = find_own_window()
                if h:
                    self.hwnd = h
                else:
                    continue
            try:
                self.yt_poll()
            except Exception:
                pass
            if self.visible and user32.IsIconic(self.hwnd):
                self.ensure_shown()
            now = time.time()
            if now - last_top > 1.5 and self.visible:
                self.set_topmost(bool(self.cfg["window"].get("always_on_top", True)))
                last_top = now
                if self.click_through and last_style != "ct":
                    self.set_click_through(True)   # WebView2 мог пересоздать дочерние окна
                last_style = "ct" if self.click_through else "ia"
            self.drag_watch()

    def drag_watch(self):
        if self.click_through or not self.visible:
            return
        if (user32.GetAsyncKeyState(VK_LBUTTON) & 0x8000) == 0:
            self.dragging = False
            return
        if self.dragging or user32.GetForegroundWindow() != self.hwnd:
            return
        pt = wt.POINT()
        user32.GetCursorPos(ctypes.byref(pt))
        x, y, w, h = self.content_cfg()
        if x <= pt.x <= x + w and y <= pt.y <= y + 26:
            self.dragging = True
            user32.PostMessageW(self.hwnd, WM_NCLBUTTONDOWN, HTCAPTION, 0)


# --------------------------------------------------------------------------
# сервисные команды
# --------------------------------------------------------------------------

def cmd_borderless() -> str:
    cfg_path = os.path.join(DOCS_ETS2, "config.cfg")
    if not os.path.exists(cfg_path):
        return "Не нашёл config.cfg игры:\n" + cfg_path
    if "eurotrucks2.exe" in run_quiet(["tasklist"]).lower():
        return "ETS2 запущена.\nЗакройте игру и повторите — иначе настройки затрутся."
    sw, sh = primary_size()
    with open(cfg_path, "r", encoding="utf-8", errors="replace") as f:
        text = f.read()
    backup = cfg_path + ".truckplay.bak"
    if not os.path.exists(backup):
        with open(backup, "w", encoding="utf-8") as f:
            f.write(text)

    def put(txt, key, val):
        if re.search(rf'^uset {key} ".*"$', txt, flags=re.M):
            return re.sub(rf'^uset {key} ".*"$', f'uset {key} "{val}"', txt, flags=re.M)
        return txt + f'\nuset {key} "{val}"\n'

    for key, val in (("r_fullscreen", "0"), ("r_windowed_borderless", "1"),
                     ("r_mode_width", str(sw)), ("r_mode_height", str(sh))):
        text = put(text, key, val)
    with open(cfg_path, "w", encoding="utf-8") as f:
        f.write(text)
    return (f"Готово: ETS2 будет в borderless {sw}x{sh}.\n"
            f"Резервная копия: {backup}\n\n"
            "Проверьте в игре: Настройки → Графика → режим экрана Borderless.")


def cmd_install_plugin() -> str:
    import shutil
    src = os.path.join(RES, "plugin", PLUGIN_DLL)
    if not os.path.exists(src):
        src = os.path.join(BASE, "plugin", PLUGIN_DLL)
    if not os.path.exists(src):
        return "Не нашёл файл плагина:\n" + src
    folder = game_plugins_dir()
    if not folder:
        return ("Не нашёл папку плагинов ETS2\n"
                "(...\\Euro Truck Simulator 2\\bin\\win_x64\\plugins).\n"
                "Скопируйте plugin\\truckplay_telemetry.dll туда вручную.")
    dst = os.path.join(folder, PLUGIN_DLL)
    try:
        shutil.copy2(src, dst)
    except PermissionError:
        return ("Нет прав на запись в папку игры.\n"
                "Запустите установку от имени администратора\n"
                "или скопируйте вручную:\n" + src + "\n  ->  " + folder)
    except Exception as e:
        return f"Ошибка установки: {e}"
    return ("Плагин телеметрии установлен:\n" + dst +
            "\n\nПерезапустите игру — данные пойдут на экран «Поездка».")


def cmd_uninstall_plugin() -> str:
    folder = game_plugins_dir()
    if not folder:
        return "Папка плагинов игры не найдена."
    dst = os.path.join(folder, PLUGIN_DLL)
    try:
        if os.path.exists(dst):
            os.remove(dst)
            return "Плагин телеметрии удалён из игры."
        return "Плагин не установлен."
    except Exception as e:
        return f"Ошибка: {e}"


def print_help():
    hk = DEFAULT_CONFIG["hotkeys"]
    print(f"""
  TruckPlay - медиасистема кабины для ETS2
  ------------------------------------------------------------
  {hk['visible']:<18} показать / скрыть экран
  {hk['click_through']:<18} мышь сквозь экран / снова в приложение
  {hk['play_pause']:<18} пауза / играть
  {hk['next']:<18} следующий
  {hk['prev']:<18} предыдущий
  {hk['seek_fwd']:<18} +10 секунд
  {hk['seek_back']:<18} -10 секунд
  {hk['vol_up']:<18} громче
  {hk['vol_down']:<18} тише
  {hk['mute']:<18} звук вкл/выкл
  {hk['browse']:<18} открыть YouTube для выбора видео
  {hk['back']:<18} вернуться к экрану
  {hk['reload']:<18} перезагрузить
  {hk['quit']:<18} выход
  ctrl+alt+shift+стрелки  подвинуть окно
  ------------------------------------------------------------
""")


def wait_for_game(poll=4.0):
    while True:
        if "eurotrucks2.exe" in run_quiet(["tasklist", "/FI", "IMAGENAME eq eurotrucks2.exe"]).lower():
            return True
        time.sleep(poll)


_MUTEX = None


def single_instance() -> bool:
    global _MUTEX
    kernel32.CreateMutexW.restype = ctypes.c_void_p
    kernel32.CreateMutexW.argtypes = [ctypes.c_void_p, wt.BOOL, wt.LPCWSTR]
    _MUTEX = kernel32.CreateMutexW(None, False, "TruckPlay_single_instance")
    return ctypes.get_last_error() != 183


def main():
    args = sys.argv[1:]
    if "--borderless" in args:
        msg = cmd_borderless()
        if FROZEN:
            user32.MessageBoxW(None, msg, "TruckPlay — borderless", 0x40)
        else:
            print(msg)
        return
    if "--install-plugin" in args or "--uninstall-plugin" in args:
        msg = cmd_install_plugin() if "--install-plugin" in args else cmd_uninstall_plugin()
        if FROZEN:
            user32.MessageBoxW(None, msg, "TruckPlay — телеметрия", 0x40)
        else:
            print(msg)
        return
    cfg = load_config()
    if "--rect" in args:
        i = args.index("--rect")
        try:
            x, y, w, h = (int(v) for v in args[i + 1:i + 5])
            cfg["window"].update({"x": x, "y": y, "width": w, "height": h})
            save_config(cfg)
            print(f"Позиция сохранена: {x},{y} {w}x{h}")
        except Exception:
            print("Использование: --rect X Y ШИРИНА ВЫСОТА")
        return
    if "--help" in args or "-h" in args:
        print_help()
        return

    if not single_instance():
        msg = "TruckPlay уже запущен.\n\nCtrl+Alt+H — показать экран\nCtrl+Alt+Q — выход"
        if FROZEN:
            user32.MessageBoxW(None, msg, "TruckPlay", 0x40)
        else:
            print("[!] " + msg.replace("\n", " "))
        return

    print_help()
    app = App(cfg)
    if "--watch" in args:
        print("  Жду запуска Euro Truck Simulator 2...", flush=True)
        wait_for_game()
        print("  Игра найдена, поднимаю экран.", flush=True)
        app.click_through = bool(cfg["window"].get("click_through", True))

    try:
        app.run()
    except KeyboardInterrupt:
        pass
    finally:
        app.running = False
        app.save_rect()
        for hk in list(app.hotkey_ids):
            user32.UnregisterHotKey(None, hk)
        print("  Остановлено.")


if __name__ == "__main__":
    main()
