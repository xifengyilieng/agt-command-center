"""Read-only DeskGo mouse sampling for explicitly published workbench controls.

No hooks, keyboard reads, native input injection, or interception are used.
The authenticated HTTP layer owns publication and command delivery.
"""
from __future__ import annotations

from collections import deque
import math
import ntpath
import os
import threading
import time


class _Win32MouseSource:
    def __init__(self, clock):
        import ctypes
        from ctypes import wintypes
        self.c, self.w, self.clock = ctypes, wintypes, clock
        self.user = ctypes.WinDLL('user32', use_last_error=True)
        self.kernel = ctypes.WinDLL('kernel32', use_last_error=True)
        self.user.GetAsyncKeyState.argtypes = [ctypes.c_int]
        self.user.GetAsyncKeyState.restype = ctypes.c_short
        self.user.GetCursorPos.argtypes = [ctypes.POINTER(wintypes.POINT)]
        self.user.GetCursorPos.restype = wintypes.BOOL
        self.user.WindowFromPoint.argtypes = [wintypes.POINT]
        self.user.WindowFromPoint.restype = wintypes.HWND
        self.user.GetClassNameW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
        self.user.GetWindowThreadProcessId.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]
        self.kernel.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
        self.kernel.OpenProcess.restype = wintypes.HANDLE
        self.kernel.QueryFullProcessImageNameW.argtypes = [wintypes.HANDLE, wintypes.DWORD, wintypes.LPWSTR, ctypes.POINTER(wintypes.DWORD)]
        self.kernel.CloseHandle.argtypes = [wintypes.HANDLE]
        self._process_cache = {}
        self._dpi_thread = None

    def _is_deskgo(self, window):
        c, w = self.c, self.w
        name = c.create_unicode_buffer(256)
        if not self.user.GetClassNameW(window, name, len(name)) or name.value != 'TXMiniSkin':
            return False
        pid = w.DWORD()
        self.user.GetWindowThreadProcessId(window, c.byref(pid))
        now = self.clock()
        cached = self._process_cache.get(pid.value)
        if cached and cached[0] > now:
            return cached[1]
        process = self.kernel.OpenProcess(0x1000, False, pid.value)
        result = False
        if process:
            try:
                path, length = c.create_unicode_buffer(32768), w.DWORD(32768)
                if self.kernel.QueryFullProcessImageNameW(process, 0, path, c.byref(length)):
                    result = ntpath.basename(path.value).casefold() == 'desktopmgr64.exe'
            finally:
                self.kernel.CloseHandle(process)
        if len(self._process_cache) >= 32:
            self._process_cache = {key: value for key, value in self._process_cache.items() if value[0] > now}
        self._process_cache[pid.value] = (now + 2, result)
        return result

    def sample(self):
        # Only this sampler thread opts into physical monitor coordinates.
        thread_id = threading.get_ident()
        if self._dpi_thread != thread_id:
            setter = getattr(self.user, 'SetThreadDpiAwarenessContext', None)
            if setter:
                setter.argtypes = [self.c.c_void_p]
                setter.restype = self.c.c_void_p
                setter(self.c.c_void_p(-4))
            self._dpi_thread = thread_id
        down = bool(self.user.GetAsyncKeyState(0x01) & 0x8000)
        point = self.w.POINT()
        if not self.user.GetCursorPos(self.c.byref(point)):
            return None
        window = self.user.WindowFromPoint(point)
        return down, (point.x, point.y), bool(window and self._is_deskgo(window))


def _number(value, name, minimum, maximum):
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        raise ValueError(f'{name} must be a finite number')
    if not minimum <= value <= maximum:
        raise ValueError(f'{name} is outside its allowed range')
    return float(value)


def _key(value, name, limit):
    if not isinstance(value, str) or not value or len(value) > limit or any(ord(c) < 32 for c in value):
        raise ValueError(f'{name} must be a nonempty string of at most {limit} characters')
    return value


def _validate(payload):
    if not isinstance(payload, dict):
        raise ValueError('publication must be an object')
    client = _key(payload.get('clientId'), 'clientId', 120)
    screen, viewport, regions = payload.get('screen'), payload.get('viewport'), payload.get('regions')
    if not isinstance(screen, dict) or not isinstance(viewport, dict):
        raise ValueError('screen and viewport must be objects')
    screen = {key: _number(screen.get(key), 'screen.' + key,
                          -65536 if key in ('x', 'y') else 1, 65536 if key in ('x', 'y') else 32768)
              for key in ('x', 'y', 'width', 'height')}
    viewport = {key: _number(viewport.get(key), 'viewport.' + key, 1, 32768)
                for key in ('width', 'height')}
    if not isinstance(regions, list) or len(regions) > 200:
        raise ValueError('regions must be an array of at most 200 controls')
    valid = []
    for region in regions:
        if not isinstance(region, dict):
            raise ValueError('each region must be an object')
        key = _key(region.get('key'), 'region.key', 80)
        kind = region.get('kind')
        if kind not in ('click', 'range') or not isinstance(region.get('disabled', False), bool):
            raise ValueError('region.kind or region.disabled is invalid')
        rect = {name: _number(region.get(name), 'region.' + name,
                             -65536 if name in ('x', 'y') else .01,
                             65536 if name in ('x', 'y') else 32768)
                for name in ('x', 'y', 'width', 'height')}
        if not region.get('disabled', False):
            valid.append({'key': key, 'kind': kind, **rect})
    return client, screen, viewport, valid


class DesktopInputBridge:
    """Publish CSS hit regions; drain semantic commands for one live file client.

    Regions are viewport CSS coordinates. ``screen`` is the physical monitor
    area corresponding to that viewport. Publications expire after five seconds.
    Non-Windows hosts are inert; injectable sources permit deterministic tests.
    """
    MAX_AGE = 5.0

    def __init__(self, *, input_source=None, clock=time.monotonic, autostart=True):
        self._clock = clock
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._client = None
        self._screen = self._viewport = None
        self._regions = []
        self._updated = float('-inf')
        self._queue = deque(maxlen=128)
        self._last_down = None
        self._gesture = None
        self._emitted = 0
        self._acknowledged = 0
        self._host_paused = True
        self._page_hidden = True
        self._render_stats = {}
        self._source = input_source
        if self._source is None and os.name == 'nt':
            try:
                self._source = _Win32MouseSource(clock)
            except (OSError, AttributeError):
                self._source = None
        self._thread = None
        if autostart and self._source is not None:
            self._thread = threading.Thread(target=self._run, name='agt-deskgo-input', daemon=True)
            self._thread.start()

    def publish(self, payload):
        client, screen, viewport, regions = _validate(payload)
        now = self._clock()
        with self._lock:
            if client != self._client or now - self._updated > self.MAX_AGE:
                self._queue.clear()
                self._gesture = None
                self._last_down = None
            self._client, self._screen, self._viewport, self._regions = client, screen, viewport, regions
            self._updated = now
            self._host_paused = payload.get('hostPaused') is not False
            self._page_hidden = payload.get('pageHidden') is not False
            stats = payload.get('renderStats')
            if isinstance(stats, dict):
                # Bounded numerical diagnostics only, published by the authenticated page.
                self._render_stats = {key: value for key in
                    ('fps', 'targetFps', 'frames', 'rawRafFps', 'measuredFps', 'limiterSkips',
                     'workMs', 'effectsLevel', 'frameP50Ms', 'frameP95Ms', 'frameP99Ms',
                     'frameMaxMs', 'frameSamples', 'cardCanvases', 'cardPixels', 'cardBackingResizes', 'stageDpr')
                    if isinstance(value := stats.get(key), (int, float)) and not isinstance(value, bool)
                    and 0 <= value <= 10**9 and math.isfinite(value)}
                if isinstance(stats.get('fallback'), bool):
                    self._render_stats['fallback'] = stats['fallback']
            if self._host_paused or self._page_hidden:
                self._queue.clear()
                self._gesture = None
                self._last_down = None
            acknowledged=payload.get('acknowledged', 0)
            if isinstance(acknowledged, int) and 0 <= acknowledged <= 10**9:
                self._acknowledged=acknowledged
        return {'clientId': client, 'active': self._source is not None and not self._stop.is_set(), 'regions': len(regions)}

    def drain(self, client_id):
        with self._lock:
            if client_id != self._client:
                return []
            if self._host_paused or self._page_hidden or self._clock() - self._updated > self.MAX_AGE:
                self._queue.clear()
                return []
            commands = list(self._queue)
            self._queue.clear()
            return commands

    def status(self):
        with self._lock:
            return {'available': self._source is not None,
                    'connected': self._client is not None and self._clock()-self._updated <= self.MAX_AGE,
                    'regions': len(self._regions), 'screen': self._screen, 'viewport': self._viewport,
                    'emitted': self._emitted, 'acknowledged': self._acknowledged,
                    'rendering': dict(self._render_stats)}

    def recovery_state(self):
        with self._lock:
            return {'seen': self._client is not None, 'hostPaused': self._host_paused,
                    'pageHidden': self._page_hidden, 'age': self._clock()-self._updated}

    def close(self):
        self._stop.set()
        if self._thread and self._thread is not threading.current_thread():
            self._thread.join(timeout=1)

    def _enqueue(self, command):
        self._emitted += 1
        if command['type'] == 'input' and self._queue and self._queue[-1].get('type') == 'input' and self._queue[-1]['key'] == command['key']:
            self._queue[-1] = command
        else:
            self._queue.append(command)

    def _hit(self, point):
        screen, viewport = self._screen, self._viewport
        x, y = point
        if not screen['x'] <= x < screen['x'] + screen['width'] or not screen['y'] <= y < screen['y'] + screen['height']:
            return None, None
        x = (x - screen['x']) * viewport['width'] / screen['width']
        y = (y - screen['y']) * viewport['height'] / screen['height']
        for region in reversed(self._regions):
            if region['x'] <= x < region['x'] + region['width'] and region['y'] <= y < region['y'] + region['height']:
                return region, x
        return None, None

    def _poll_once(self):
        if self._source is None or self._stop.is_set():
            return False
        with self._lock:
            if self._client is None or self._host_paused or self._page_hidden or self._clock() - self._updated > self.MAX_AGE:
                self._gesture = None
                self._last_down = None
                return False
            client = self._client
        try:
            sample = self._source.sample()
        except (OSError, ValueError):
            sample = None
        with self._lock:
            if (client != self._client or self._host_paused or self._page_hidden
                    or self._clock() - self._updated > self.MAX_AGE or sample is None):
                self._gesture = None
                self._last_down = None
                return False
            down, point, deskgo = sample
            previous, self._last_down = self._last_down, down
            if previous is None:
                return down
            hit, local_x = self._hit(point) if deskgo else (None, None)
            if not hit:
                self._gesture = None
                return down
            if down and not previous:
                self._gesture = {'key': hit['key'], 'kind': hit['kind']}
            gesture = self._gesture
            matching = gesture and gesture['key'] == hit['key'] and gesture['kind'] == hit['kind']
            if matching and hit['kind'] == 'range' and (down or previous):
                ratio = max(0.0, min(1.0, (local_x - hit['x']) / hit['width']))
                self._enqueue({'key': hit['key'], 'type': 'input', 'ratio': round(ratio, 5)})
            if previous and not down:
                if matching and hit['kind'] == 'click':
                    self._enqueue({'key': hit['key'], 'type': 'click'})
                self._gesture = None
            return down

    def _run(self):
        while not self._stop.is_set():
            with self._lock:
                active = (self._client is not None and not self._host_paused and not self._page_hidden
                          and self._clock() - self._updated <= self.MAX_AGE)
            if active:
                down = self._poll_once()
                self._stop.wait(.016 if down else .020)
            else:
                self._stop.wait(.200)
