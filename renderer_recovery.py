"""Conservative recovery only for a vanished renderer of this selected wallpaper."""
from __future__ import annotations

from collections import deque
import copy
import datetime as dt
import getpass
import json
import os
from pathlib import Path
import re
import subprocess
import threading
import time


CREATE_NO_WINDOW = getattr(subprocess, 'CREATE_NO_WINDOW', 0)


def _processes(timeout=4):
    script = r"""
    [Console]::OutputEncoding = [System.Text.UTF8Encoding]::new()
    @(Get-CimInstance Win32_Process -Filter "Name='wallpaper32.exe' OR Name='wallpaper64.exe' OR Name='webwallpaper64.exe'" |
      Select-Object @{n='pid';e={$_.ProcessId}},@{n='parent';e={$_.ParentProcessId}},@{n='cmd';e={$_.CommandLine}},@{n='path';e={$_.ExecutablePath}}) | ConvertTo-Json -Compress
    """
    result = subprocess.run(['powershell.exe', '-NoProfile', '-NonInteractive', '-Command', script],
                            capture_output=True, text=True, encoding='utf-8', errors='replace',
                            timeout=timeout, check=True, creationflags=CREATE_NO_WINDOW)
    value = json.loads(result.stdout.strip() or '[]')
    return value if isinstance(value, list) else [value]


def _default_desktop():
    """An active interactive session on the Default input desktop is required."""
    if os.name != 'nt':
        return False
    import ctypes as c
    from ctypes import wintypes as w
    kernel, user, wts = c.WinDLL('kernel32'), c.WinDLL('user32'), c.WinDLL('wtsapi32')
    session = w.DWORD()
    kernel.ProcessIdToSessionId.argtypes = [w.DWORD, c.POINTER(w.DWORD)]
    if not kernel.ProcessIdToSessionId(os.getpid(), c.byref(session)) or not session.value:
        return False
    state, length = c.c_void_p(), w.DWORD()
    wts.WTSQuerySessionInformationW.argtypes = [w.HANDLE, w.DWORD, c.c_int, c.POINTER(c.c_void_p), c.POINTER(w.DWORD)]
    wts.WTSFreeMemory.argtypes = [c.c_void_p]
    if not wts.WTSQuerySessionInformationW(None, session.value, 8, c.byref(state), c.byref(length)):
        return False
    try:
        if length.value < 4 or c.cast(state, c.POINTER(w.DWORD)).contents.value != 0:
            return False
    finally:
        wts.WTSFreeMemory(state)
    user.OpenInputDesktop.argtypes = [w.DWORD, w.BOOL, w.DWORD]
    user.OpenInputDesktop.restype = w.HANDLE
    user.CloseDesktop.argtypes = [w.HANDLE]
    user.GetUserObjectInformationW.argtypes = [w.HANDLE, c.c_int, c.c_void_p, w.DWORD, c.POINTER(w.DWORD)]
    desktop = user.OpenInputDesktop(0, False, 0x0001)
    if not desktop:
        return False
    try:
        name, needed = c.create_unicode_buffer(256), w.DWORD()
        return bool(user.GetUserObjectInformationW(desktop, 2, name, c.sizeof(name), c.byref(needed)) and name.value.casefold() == 'default')
    finally:
        user.CloseDesktop(desktop)


def _normal(path):
    return os.path.normcase(str(Path(path).resolve())).casefold()


class RendererRecovery:
    """No periodic reloads: act only after three proven missing-renderer samples.

    ``action(command, file, monitor)`` is injectable for tests. It receives the
    official ``closeWallpaper`` / ``openWallpaper`` command names. No main
    Wallpaper Engine process is started by this service.
    """
    INTERVAL = 10
    ROOT_MIN_AGE = 30
    COOLDOWN = 120
    WINDOW = 600
    MAX_ATTEMPTS = 2

    def __init__(self, engine, project, heartbeat_state, *, sample_processes=None,
                 action=None, desktop_state=None, clock=time.monotonic,
                 config_reader=None, sleep=time.sleep, diagnostic_path=None,
                 deadline_clock=time.monotonic, autostart=True):
        self.engine, self.project = Path(engine).resolve(), Path(project).resolve()
        self._main_paths = {_normal(self.engine / name) for name in ('wallpaper32.exe', 'wallpaper64.exe')}
        self._browser_path = _normal(self.engine / 'bin/webwallpaper64.exe')
        self._heartbeat = heartbeat_state
        self._sample = sample_processes or _processes
        self._action, self._clock, self._sleep = action, clock, sleep
        self._deadline_clock = deadline_clock
        self._desktop = desktop_state or _default_desktop
        self._read_config = config_reader or (lambda: json.loads((self.engine / 'config.json').read_text(encoding='utf-8-sig')))
        self._diagnostic_path = Path(diagnostic_path) if diagnostic_path else Path(__file__).with_name('renderer-recovery.log')
        self._enabled = os.name == 'nt' or sample_processes is not None
        self._lock, self._stop = threading.Lock(), threading.Event()
        self._roots, self._attempts = {}, deque()
        self._blocked_roots = set()
        self._last_attempt = float('-inf')
        self._last_diagnostic = None
        self._status = {'enabled': self._enabled, 'phase': 'idle' if self._enabled else 'unsupported',
                        'reason': 'waiting for process observations', 'monitors': {}, 'attemptsInWindow': 0,
                        'lastRecoveryAt': None, 'lastDiagnostic': None}
        self._thread = None
        if autostart and self._enabled:
            self._thread = threading.Thread(target=self._run, name='agt-renderer-recovery', daemon=True)
            self._thread.start()

    def status(self):
        with self._lock:
            result = copy.deepcopy(self._status)
            result['attemptsInWindow'] = sum(self._clock() - value < self.WINDOW for value in self._attempts)
        return result

    def close(self):
        self._stop.set()
        if self._thread and self._thread is not threading.current_thread():
            self._thread.join(timeout=1)

    def _set_status(self, phase, reason):
        with self._lock:
            self._status.update(phase=phase, reason=reason)

    def _selected(self):
        config = self._read_config()
        user = next((v for k, v in config.items() if k.casefold() == getpass.getuser().casefold()), {})
        selected = user.get('general', {}).get('wallpaperconfig', {}).get('selectedwallpapers', {})
        if not isinstance(selected, dict):
            raise ValueError('invalid selectedwallpapers')
        return {int(match.group(1)): value.get('file') for name, value in selected.items()
                if (match := re.fullmatch(r'Monitor(\d+)', name)) and isinstance(value, dict)}

    def _owned_file(self, file):
        if not isinstance(file, str) or not file:
            return False
        try:
            Path(file).resolve().relative_to(self.project)
            return True
        except (ValueError, OSError):
            return False

    def _main(self, processes, pid=None):
        return next((p for p in processes if p.get('path') and _normal(p['path']) in self._main_paths
                     and (pid is None or p.get('pid') == pid)), None)

    def _browser(self, processes, main_pid, monitor):
        for process in processes:
            if (not process.get('path') or _normal(process['path']) != self._browser_path
                    or process.get('parent') != main_pid or re.search(r'--type(?:=|\s)', process.get('cmd') or '')):
                continue
            match = re.search(r'(?:^|\s)-cacheId\s+"?(monitor\d+)"?(?=\s|$)', process.get('cmd') or '', re.I)
            if match and match.group(1).casefold() == f'monitor{monitor}':
                return process
        return None

    def _children(self, processes, browser_pid):
        kinds = set()
        for process in processes:
            if process.get('parent') != browser_pid or not process.get('path') or _normal(process['path']) != self._browser_path:
                continue
            match = re.search(r'(?:^|\s)--type(?:=|\s)"?([\w-]+)', process.get('cmd') or '')
            if match:
                kinds.add(match.group(1))
        return kinds

    def _heartbeat_allows(self):
        state = self._heartbeat()
        return isinstance(state, dict) and state.get('seen') is True and state.get('hostPaused') is False and state.get('pageHidden') is False

    def _note(self, phase, monitor, reason):
        key = (phase, monitor, reason)
        if key == self._last_diagnostic:
            return
        self._last_diagnostic = key
        stamp = dt.datetime.now(dt.timezone.utc).isoformat(timespec='seconds')
        line = f'{stamp} {phase} Monitor{monitor}: {reason}\n'
        with self._lock:
            self._status['lastDiagnostic'] = line.strip()
        try:
            if self._diagnostic_path.exists() and self._diagnostic_path.stat().st_size > 32768:
                self._diagnostic_path.write_bytes(self._diagnostic_path.read_bytes()[-16384:])
            with self._diagnostic_path.open('a', encoding='utf-8') as handle:
                handle.write(line)
        except OSError:
            pass

    def _invoke(self, command, file, monitor, main, expected_browser_pid=None, latched_intent=False):
        if self._stop.is_set() or not self._desktop() or (not latched_intent and not self._heartbeat_allows()):
            raise RuntimeError('interactive session or playback intent changed')
        # Recheck immediately before invoking the CLI so a stopped main app is
        # never deliberately launched. Match the original main process identity.
        fresh = self._sample()
        if not self._main(fresh, main['pid']):
            raise RuntimeError('main Wallpaper Engine process exited')
        selected = self._selected().get(monitor)
        if command == 'closeWallpaper':
            browser = self._browser(fresh, main['pid'], monitor)
            kinds = self._children(fresh, browser['pid']) if browser else set()
            if (selected != file or not browser or browser['pid'] != expected_browser_pid
                    or 'renderer' in kinds or not {'gpu-process', 'utility'} <= kinds):
                raise RuntimeError('selected browser changed or renderer returned before close')
        elif selected:
            raise RuntimeError('wallpaper selection changed before reopen')
        if self._action is not None:
            self._action(command, file, monitor)
            return
        args = [main['path'], '-control', command, '-monitor', str(monitor)]
        if command == 'openWallpaper':
            args += ['-file', file]
        subprocess.run(args, check=True, timeout=4, creationflags=CREATE_NO_WINDOW,
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

    def _bounded_sample(self, deadline):
        remaining = deadline - self._deadline_clock()
        if remaining <= 0:
            raise RuntimeError('close was not confirmed within five seconds')
        result = _processes(timeout=min(4, remaining)) if self._sample is _processes else self._sample()
        if self._deadline_clock() > deadline:
            raise RuntimeError('close process verification exceeded five seconds')
        return result

    def _wait_closed(self, monitor, file, main, old_browser_pid):
        deadline = self._deadline_clock() + 5
        while self._deadline_clock() < deadline:
            if self._stop.is_set() or not self._desktop():
                raise RuntimeError('recovery stopped or interactive desktop changed')
            selected = self._selected().get(monitor)
            if selected and selected != file:
                raise RuntimeError('user selected a different wallpaper')
            if not selected:
                # No WMI polling while config still contains the old selection.
                # Once clear, sample with the remaining real deadline budget.
                processes = self._bounded_sample(deadline)
                if not self._main(processes, main['pid']):
                    raise RuntimeError('main Wallpaper Engine process exited')
                if not any(p.get('pid') == old_browser_pid and p.get('path') and _normal(p['path']) == self._browser_path for p in processes):
                    return
            self._sleep(min(.2 if not selected else .1, max(0, deadline-self._deadline_clock())))
        raise RuntimeError('close was not confirmed within five seconds')

    def _wait_open_selected(self, monitor, file):
        deadline = self._deadline_clock() + 5
        while self._deadline_clock() < deadline:
            if self._stop.is_set() or not self._desktop():
                raise RuntimeError('recovery stopped or interactive desktop changed after reopen')
            selected = self._selected().get(monitor)
            if selected == file:
                return
            if selected:
                raise RuntimeError('user selected a different wallpaper after reopen')
            self._sleep(min(.1, max(0, deadline-self._deadline_clock())))
        raise RuntimeError('open command did not restore the selected file within five seconds')

    def _recover(self, monitor, file, main, browser):
        now = self._clock()
        if (monitor, browser['pid']) in self._blocked_roots:
            self._set_status('rate_limited', 'repeated renderer loss is latched; awaiting a healthy or new browser root')
            return
        with self._lock:
            self._attempts = deque(value for value in self._attempts if now - value < self.WINDOW)
            attempts = len(self._attempts)
        if now - self._last_attempt < self.COOLDOWN:
            self._set_status('cooldown', 'recovery cooldown is active')
            return
        if attempts >= self.MAX_ATTEMPTS:
            self._blocked_roots.add((monitor, browser['pid']))
            self._set_status('rate_limited', 'two recovery attempts in ten minutes; no further reload')
            self._note('rate_limited', monitor, 'renderer still absent; automatic actions suppressed')
            return
        fresh = self._sample()
        current = self._browser(fresh, main['pid'], monitor)
        kinds = self._children(fresh, current['pid']) if current else set()
        if (not self._main(fresh, main['pid']) or not current or current['pid'] != browser['pid']
                or 'renderer' in kinds or not {'gpu-process', 'utility'} <= kinds
                or self._selected().get(monitor) != file or not self._desktop() or not self._heartbeat_allows()):
            self._set_status('skipped', 'selection, process, desktop or playback changed before recovery')
            return
        with self._lock:
            self._attempts.append(now)
            self._last_attempt = now
        self._set_status('recovering', f'recovering missing renderer on Monitor{monitor}')
        try:
            self._invoke('closeWallpaper', file, monitor, main, browser['pid'])
            self._wait_closed(monitor, file, main, browser['pid'])
            if self._selected().get(monitor):
                raise RuntimeError('wallpaper selection changed before reopen')
            # Our own close destroys/pauses the page. Preserve the explicitly
            # observed pre-close run intent for this transaction; desktop,
            # original main PID and user selection remain mandatory gates.
            self._invoke('openWallpaper', file, monitor, main, latched_intent=True)
            self._wait_open_selected(monitor, file)
            with self._lock:
                self._status['lastRecoveryAt'] = dt.datetime.now(dt.timezone.utc).isoformat(timespec='seconds')
            self._set_status('awaiting_renderer', f'reselected the same project on Monitor{monitor}; awaiting process verification')
            self._note('reopened', monitor, 'renderer disappeared while browser/GPU/utility remained; same selected file restored, renderer confirmation pending')
            self._roots.pop(monitor, None)
        except (OSError, ValueError, RuntimeError, subprocess.SubprocessError) as error:
            self._set_status('failed', str(error))
            self._note('failed', monitor, str(error))

    def _poll_once(self):
        if not self._enabled or self._stop.is_set():
            return
        try:
            selected = {n: file for n, file in self._selected().items() if self._owned_file(file)}
            processes = self._sample()
            main = self._main(processes)
            if not selected or not main:
                self._roots.clear()
                self._blocked_roots.clear()
                self._set_status('idle', 'project is not selected or main Wallpaper Engine is not running')
                return
            allowed = self._desktop() and self._heartbeat_allows()
            now, observed = self._clock(), {}
            for monitor, file in selected.items():
                browser = self._browser(processes, main['pid'], monitor)
                if not browser:
                    self._roots.pop(monitor, None)
                    continue
                state = self._roots.get(monitor)
                if not state or state['pid'] != browser['pid']:
                    state = self._roots[monitor] = {'pid': browser['pid'], 'first': now, 'missing': 0}
                kinds = self._children(processes, browser['pid'])
                if 'renderer' in kinds:
                    self._blocked_roots.discard((monitor, browser['pid']))
                proven_missing = 'renderer' not in kinds and {'gpu-process', 'utility'} <= kinds
                state['missing'] = state['missing'] + 1 if allowed and proven_missing else 0
                observed[str(monitor)] = {'rootPid': browser['pid'], 'missingSamples': state['missing'],
                                          'observedSeconds': round(now-state['first'], 1)}
                if allowed and state['missing'] >= 3 and now-state['first'] >= self.ROOT_MIN_AGE:
                    self._recover(monitor, file, main, browser)
            self._roots = {n: state for n, state in self._roots.items() if n in selected}
            self._blocked_roots.intersection_update((n, state['pid']) for n, state in self._roots.items())
            with self._lock:
                self._status['monitors'] = observed
            if not allowed:
                self._set_status('suspended', 'no active Default desktop, or host/page is paused or unobserved')
            elif not observed:
                self._set_status('idle', 'selected project has no running browser root; no process will be started')
            elif not any(state['missingSamples'] for state in observed.values()):
                self._set_status('healthy', 'selected project has a renderer or no recoverable browser root')
        except (OSError, ValueError, TypeError, KeyError, AttributeError, RuntimeError, subprocess.SubprocessError) as error:
            for state in self._roots.values():
                state['missing'] = 0
            self._set_status('sampling_error', str(error))

    def _run(self):
        while not self._stop.is_set():
            self._poll_once()
            self._stop.wait(self.INTERVAL)
