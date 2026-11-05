from copy import deepcopy
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from desktop_input import DesktopInputBridge


class FakeMouse:
    def __init__(self):
        self.value = (False, (0, 0), True)
        self.samples = 0

    def sample(self):
        self.samples += 1
        return self.value


class DesktopInputTests(unittest.TestCase):
    def setUp(self):
        self.now = 100.0
        self.mouse = FakeMouse()
        self.bridge = DesktopInputBridge(input_source=self.mouse, clock=lambda: self.now, autostart=False)
        self.payload = {'clientId': 'client-one',
                        'screen': {'x': -1000, 'y': 0, 'width': 2000, 'height': 1000},
                        'viewport': {'width': 1000, 'height': 500},
                        'regions': [{'key': 'settings', 'x': 900, 'y': 20, 'width': 80, 'height': 30, 'kind': 'click'},
                                    {'key': 'brightness', 'x': 100, 'y': 100, 'width': 100, 'height': 20, 'kind': 'range'}]}
        self.bridge.publish(self.payload)
        self.sample(False, (850, 60))

    def tearDown(self):
        self.bridge.close()

    def sample(self, down, point, deskgo=True):
        self.mouse.value = (down, point, deskgo)
        self.bridge._poll_once()

    def click(self, point, deskgo=True):
        self.sample(True, point, deskgo)
        self.sample(False, point, deskgo)

    def test_scaled_negative_monitor_click_is_semantic_and_delivered_once(self):
        self.click((850, 60))
        self.assertEqual(self.bridge.drain('client-one'), [{'key': 'settings', 'type': 'click'}])
        self.assertEqual(self.bridge.drain('client-one'), [])

    def test_external_app_and_unpublished_area_never_emit(self):
        self.click((850, 60), deskgo=False)
        self.click((20, 400))
        self.click((2000, 60))
        self.assertEqual(self.bridge.drain('client-one'), [])

    def test_click_requires_down_and_up_on_same_control(self):
        self.sample(True, (850, 60))
        self.sample(False, (0, 0))
        self.sample(True, (0, 0))
        self.sample(False, (850, 60))
        self.assertEqual(self.bridge.drain('client-one'), [])

    def test_range_drag_coalesces_and_never_returns_coordinates(self):
        self.sample(True, (-750, 220))
        self.sample(True, (-650, 220))
        self.sample(False, (-650, 220))
        self.assertEqual(self.bridge.drain('client-one'), [{'key': 'brightness', 'type': 'input', 'ratio': .75}])

    def test_overlay_loss_cancels_gesture(self):
        self.sample(True, (850, 60))
        self.sample(True, (850, 60), deskgo=False)
        self.sample(False, (850, 60))
        self.assertEqual(self.bridge.drain('client-one'), [])

    def test_disabled_and_topmost_regions(self):
        top = {'key': 'dialogClose', 'x': 900, 'y': 20, 'width': 80, 'height': 30, 'kind': 'click'}
        self.payload['regions'].append(top)
        self.bridge.publish(self.payload)
        self.click((850, 60))
        self.assertEqual(self.bridge.drain('client-one'), [{'key': 'dialogClose', 'type': 'click'}])
        top['disabled'] = True
        self.payload['regions'][0]['disabled'] = True
        self.bridge.publish(self.payload)
        self.click((850, 60))
        self.assertEqual(self.bridge.drain('client-one'), [])

    def test_only_latest_client_can_drain_and_old_queue_is_removed(self):
        self.click((850, 60))
        other = deepcopy(self.payload)
        other['clientId'] = 'client-two'
        self.bridge.publish(other)
        self.assertEqual(self.bridge.drain('client-one'), [])
        self.assertEqual(self.bridge.drain('client-two'), [])
        self.sample(False, (850, 60))
        self.click((850, 60))
        self.assertEqual(self.bridge.drain('client-one'), [])
        self.assertEqual(self.bridge.drain('client-two'), [{'key': 'settings', 'type': 'click'}])

    def test_expired_client_does_not_sample_or_deliver(self):
        self.click((850, 60))
        count = self.mouse.samples
        self.now += 5.1
        self.bridge._poll_once()
        self.assertEqual(self.mouse.samples, count)
        self.assertEqual(self.bridge.drain('client-one'), [])

    def test_initial_held_button_does_not_create_a_click(self):
        self.bridge.close()
        self.bridge = DesktopInputBridge(input_source=self.mouse, clock=lambda: self.now, autostart=False)
        self.bridge.publish(self.payload)
        self.sample(True, (850, 60))
        self.sample(False, (850, 60))
        self.assertEqual(self.bridge.drain('client-one'), [])

    def test_publication_validation_is_atomic(self):
        mutations = [lambda p: p.update(regions=p['regions'] * 101),
                     lambda p: p['screen'].update(width=0),
                     lambda p: p['viewport'].update(height=float('nan')),
                     lambda p: p['regions'][0].update(key='x' * 81),
                     lambda p: p['regions'][0].update(kind='keyboard'),
                     lambda p: p['regions'][0].update(width=True),
                     lambda p: p['regions'][0].update(disabled='yes')]
        for mutate in mutations:
            payload = deepcopy(self.payload)
            mutate(payload)
            with self.assertRaises(ValueError):
                self.bridge.publish(payload)
        self.click((850, 60))
        self.assertEqual(self.bridge.drain('client-one'), [{'key': 'settings', 'type': 'click'}])

    def test_non_windows_default_is_inert(self):
        with patch('desktop_input.os.name', 'posix'):
            bridge = DesktopInputBridge(clock=lambda: self.now)
        try:
            self.assertFalse(bridge.publish(self.payload)['active'])
            self.assertFalse(bridge._poll_once())
            self.assertEqual(bridge.drain('client-one'), [])
            self.assertIsNone(bridge._thread)
        finally:
            bridge.close()


if __name__ == '__main__':
    unittest.main()
