# -*- coding: utf-8 -*-
"""
Чтение телеметрии из нашего плагина (plugin/truckplay_telemetry.cpp).

Плагин пишет JSON в %LOCALAPPDATA%\\TruckPlay\\telemetry.json при каждом кадре
(не чаще 10 раз в секунду, атомарная замена файла).
"""

from __future__ import annotations

import json
import os
import threading
import time

STATE_DIR = os.path.join(os.environ.get("LOCALAPPDATA", ""), "TruckPlay")
PATH = os.path.join(STATE_DIR, "telemetry.json")


class Telemetry:
    def __init__(self) -> None:
        self._data: dict | None = None
        self._stamp = 0.0
        self._lock = threading.Lock()
        self._stop = False
        self._seen = False

    def start(self) -> None:
        threading.Thread(target=self._loop, daemon=True).start()

    def stop(self) -> None:
        self._stop = True

    def _loop(self) -> None:
        while not self._stop:
            try:
                with open(PATH, "r", encoding="utf-8") as f:
                    data = json.load(f)
                if isinstance(data, dict) and "speed" in data:
                    with self._lock:
                        self._data = data
                        self._stamp = time.time()
                        self._seen = True
            except (FileNotFoundError, PermissionError, json.JSONDecodeError, OSError):
                pass
            except Exception:
                pass
            time.sleep(0.2)

    def snapshot(self) -> dict | None:
        with self._lock:
            if not self._data:
                return None
            age = time.time() - self._stamp
            # файл мог остаться от прошлой сессии — сверяем и метку от плагина
            try:
                plug = float(self._data.get("t", 0)) / 1000.0
                if plug > 1_600_000_000 and abs(time.time() - plug) < 3.0:
                    age = min(age, 0.0) if age > 3.0 else age
            except Exception:
                pass
            if age > 3.0:
                return None
            return dict(self._data)

    @property
    def ever_seen(self) -> bool:
        return self._seen
