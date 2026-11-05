from copy import deepcopy
import getpass
from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from renderer_recovery import RendererRecovery


class RendererRecoveryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.base = Path(self.temp.name)
        self.engine = self.base / 'engine'
        self.project = self.engine / 'projects/myprojects/AGT-Live-Command'
        self.file = str(self.project / 'index.html')
        self.selected = {'Monitor0': {'file': self.file}}
        self.processes = [{'pid': 1, 'parent': 0, 'cmd': 'wallpaper64.exe', 'path': str(self.engine / 'wallpaper64.exe')}]
        self._browser(0, 10, True)
        self.now, self.wait_now = 0, 0
        self.heartbeat = {'seen': True, 'hostPaused': False, 'pageHidden': False, 'age': 0}
        self.desktop, self.restore_renderer = True, True
        self.actions, self.sleeps = [], []
        self.log = self.base / 'recovery.log'
        self.recovery = RendererRecovery(self.engine, self.project, lambda: dict(self.heartbeat),
            sample_processes=lambda: deepcopy(self.processes), action=self.action,
            desktop_state=lambda: self.desktop, clock=lambda: self.now,
            config_reader=lambda: {getpass.getuser(): {'general': {'wallpaperconfig': {'selectedwallpapers': deepcopy(self.selected)}}}},
            sleep=self.sleep, deadline_clock=lambda: self.wait_now, diagnostic_path=self.log, autostart=False)

    def sleep(self, seconds):
        self.sleeps.append(seconds)
        self.wait_now += seconds

    def tearDown(self):
        self.recovery.close()
        self.temp.cleanup()

    def _browser(self, monitor, pid, renderer):
        path = str(self.engine / 'bin/webwallpaper64.exe')
        self.processes += [{'pid': pid, 'parent': 1, 'path': path, 'cmd': f'webwallpaper64.exe -cacheId monitor{monitor}'},
                           {'pid': pid+1, 'parent': pid, 'path': path, 'cmd': '--type=gpu-process'},
                           {'pid': pid+2, 'parent': pid, 'path': path, 'cmd': '--type=utility'}]
        if renderer:
            self.processes.append({'pid': pid+3, 'parent': pid, 'path': path, 'cmd': '--type=renderer'})

    def _lose_renderer(self, parent=10):
        self.processes[:] = [p for p in self.processes if not (p['parent'] == parent and p['cmd'] == '--type=renderer')]

    def action(self, command, file, monitor):
        self.actions.append((command, file, monitor))
        if command == 'closeWallpaper':
            self.selected.pop(f'Monitor{monitor}', None)
            old = self.recovery._browser(self.processes, 1, monitor)
            if old:
                self.processes[:] = [p for p in self.processes if p['pid'] != old['pid'] and p['parent'] != old['pid']]
        else:
            self.selected[f'Monitor{monitor}'] = {'file': file}
            old = self.recovery._browser(self.processes, 1, monitor)
            if old:
                self.processes[:] = [p for p in self.processes if p['pid'] != old['pid'] and p['parent'] != old['pid']]
            self._browser(monitor, 100 + len(self.actions)*10, self.restore_renderer)

    def poll(self, now):
        self.now = now
        self.recovery._poll_once()

    def crash(self):
        self.poll(0)
        self._lose_renderer()
        self.poll(30)
        self.poll(40)
        self.poll(50)

    def test_healthy_renderer_never_reloads_even_with_old_heartbeat(self):
        self.heartbeat['age'] = 100000
        for now in range(0, 1000, 10):
            self.poll(now)
        self.assertEqual(self.actions, [])
        self.assertEqual(self.recovery.status()['phase'], 'healthy')

    def test_three_missing_samples_and_mature_root_restore_only_same_monitor_file(self):
        self.poll(0)
        self._lose_renderer()
        self.poll(30);self.poll(40)
        self.assertEqual(self.actions, [])
        self.poll(50)
        self.assertEqual(self.actions, [('closeWallpaper', self.file, 0), ('openWallpaper', self.file, 0)])
        self.assertEqual(self.recovery.status()['phase'], 'awaiting_renderer')
        self.poll(60)
        self.assertEqual(self.recovery.status()['phase'], 'healthy')

    def test_new_browser_is_given_at_least_thirty_seconds(self):
        self._lose_renderer()
        self.poll(0);self.poll(10);self.poll(20)
        self.assertEqual(self.actions, [])
        self.poll(30)
        self.assertEqual(len(self.actions), 2)

    def test_renderer_return_breaks_consecutive_missing_samples(self):
        self.poll(0);self._lose_renderer();self.poll(30);self.poll(40)
        self.processes.append({'pid': 13, 'parent': 10, 'path': str(self.engine/'bin/webwallpaper64.exe'), 'cmd': '--type=renderer'})
        self.poll(50);self._lose_renderer();self.poll(60);self.poll(70)
        self.assertEqual(self.actions, [])

    def test_failed_sample_breaks_consecutive_missing_samples(self):
        self.poll(0);self._lose_renderer();self.poll(30);self.poll(40)
        sample = self.recovery._sample
        def fail():raise TimeoutError('process sampling timed out')
        self.recovery._sample = fail
        self.poll(50)
        self.recovery._sample = sample
        self.poll(60);self.poll(70)
        self.assertEqual(self.actions, [])
        self.poll(80)
        self.assertEqual(len(self.actions), 2)

    def test_user_wallpaper_change_main_exit_and_manual_browser_stop_are_respected(self):
        for mode in ('selection', 'main', 'browser'):
            with self.subTest(mode=mode):
                self.poll(0);self._lose_renderer()
                old_selected, old_processes = deepcopy(self.selected), deepcopy(self.processes)
                if mode == 'selection':self.selected['Monitor0']['file'] = str(self.base/'other/index.html')
                elif mode == 'main':self.processes[:] = [p for p in self.processes if p['pid'] != 1]
                else:self.processes[:] = [p for p in self.processes if p['pid'] != 10]
                for now in (30, 40, 50, 60):self.poll(now)
                self.assertEqual(self.actions, [])
                self.selected, self.processes = old_selected, old_processes

    def test_host_pause_hidden_unseen_and_nondefault_desktop_prevent_recovery(self):
        self.poll(0);self._lose_renderer()
        cases = [('hostPaused', True), ('pageHidden', True), ('seen', False), ('desktop', False)]
        for field, value in cases:
            original = self.desktop if field == 'desktop' else self.heartbeat[field]
            if field == 'desktop':self.desktop = value
            else:self.heartbeat[field] = value
            for now in (30, 40, 50, 60):self.poll(now)
            self.assertEqual(self.actions, [])
            if field == 'desktop':self.desktop = original
            else:self.heartbeat[field] = original

    def test_gpu_and_utility_must_still_exist_and_paths_must_match(self):
        self.poll(0);self._lose_renderer()
        self.processes[:] = [p for p in self.processes if p['cmd'] != '--type=gpu-process']
        for now in (30, 40, 50):self.poll(now)
        self.assertEqual(self.actions, [])
        self.processes.append({'pid': 11, 'parent': 10, 'path': str(self.base/'wrong/bin/webwallpaper64.exe'), 'cmd': '--type=gpu-process'})
        for now in (60, 70, 80):self.poll(now)
        self.assertEqual(self.actions, [])

    def test_other_monitor_and_other_wallpaper_are_never_changed(self):
        self.selected['Monitor1'] = {'file': str(self.base/'other/index.html')}
        self._browser(1, 30, False)
        self.crash()
        self.assertTrue(all(action[2] == 0 and action[1] == self.file for action in self.actions))
        self.assertEqual(self.selected['Monitor1']['file'], str(self.base/'other/index.html'))

    def test_cooldown_and_frequency_limit_latch_instead_of_restart_loop(self):
        self.restore_renderer = False
        self.crash()
        for now in range(60, 170, 10):self.poll(now)
        self.assertEqual(len(self.actions), 2)
        self.poll(170)
        self.assertEqual(len(self.actions), 4)
        for now in range(180, 900, 10):self.poll(now)
        self.assertEqual(len(self.actions), 4)
        self.assertEqual(self.recovery.status()['phase'], 'rate_limited')
        self.assertEqual(self.log.read_text(encoding='utf-8').count('rate_limited'), 1)

    def test_close_must_be_confirmed_and_wait_is_bounded(self):
        self.recovery._action = lambda *args: self.actions.append(args)
        self.crash()
        self.assertEqual(self.actions, [('closeWallpaper', self.file, 0)])
        self.assertAlmostEqual(sum(self.sleeps), 5.0)
        self.assertEqual(self.recovery.status()['phase'], 'failed')

    def test_selection_changed_during_close_is_never_overwritten(self):
        def action(command, file, monitor):
            self.actions.append((command, file, monitor))
            self.selected['Monitor0'] = {'file': str(self.base/'user-picked/index.html')}
        self.recovery._action = action
        self.crash()
        self.assertEqual(len(self.actions), 1)
        self.assertEqual(Path(self.selected['Monitor0']['file']).parts[-2:], ('user-picked', 'index.html'))

    def test_main_exit_between_close_and_open_does_not_launch_wallpaper_engine(self):
        def action(command, file, monitor):
            self.actions.append((command, file, monitor))
            self.selected.pop('Monitor0')
            self.processes[:] = [p for p in self.processes if p['pid'] != 1]
        self.recovery._action = action
        self.crash()
        self.assertEqual(self.actions, [('closeWallpaper', self.file, 0)])
        self.assertEqual(self.recovery.status()['phase'], 'failed')

    def test_close_generated_paused_hidden_or_missing_page_does_not_cancel_latched_reopen(self):
        normal_action = self.action
        def action(command, file, monitor):
            normal_action(command, file, monitor)
            if command == 'closeWallpaper':
                self.heartbeat.update(hostPaused=True, pageHidden=True, seen=False)
        self.recovery._action = action
        self.crash()
        self.assertEqual(self.actions, [('closeWallpaper', self.file, 0), ('openWallpaper', self.file, 0)])
        self.assertEqual(self.recovery.status()['phase'], 'awaiting_renderer')

    def test_config_clear_is_not_enough_if_old_browser_root_still_exists(self):
        def action(command, file, monitor):
            self.actions.append((command, file, monitor))
            self.selected.pop('Monitor0', None)
        self.recovery._action = action
        self.crash()
        self.assertEqual(self.actions, [('closeWallpaper', self.file, 0)])
        self.assertAlmostEqual(self.wait_now, 5)
        self.assertEqual(self.recovery.status()['phase'], 'failed')

    def test_locking_during_close_prevents_reopen(self):
        normal_action = self.action
        def action(command, file, monitor):
            normal_action(command, file, monitor)
            if command == 'closeWallpaper':self.desktop = False
        self.recovery._action = action
        self.crash()
        self.assertEqual(self.actions, [('closeWallpaper', self.file, 0)])

    def test_open_requires_selection_confirmation(self):
        normal_action = self.action
        def action(command, file, monitor):
            if command == 'closeWallpaper':normal_action(command, file, monitor)
            else:self.actions.append((command, file, monitor))
        self.recovery._action = action
        self.crash()
        self.assertEqual(len(self.actions), 2)
        self.assertEqual(self.recovery.status()['phase'], 'failed')
        self.assertIn('did not restore', self.recovery.status()['reason'])


if __name__ == '__main__':
    unittest.main()
