"""Offline fake-backend tests. Never load a DLL or enumerate/open a real port."""
import ctypes
import json
from pathlib import Path
import queue
import tempfile
import threading
import time
from types import SimpleNamespace
import unittest
from unittest.mock import patch

import iotpower_output_control as c


def port(device='COM7', vid=0x10c4, pid=0xea60, serial='0001', description='CP210x'):
    return SimpleNamespace(device=device, vid=vid, pid=pid,
                           serial_number=serial, description=description)


class FakeBackend:
    dll_sha256 = 'a' * 64

    def __init__(self, mode='', on=1):
        self.state = {'current': 54321, 'unit': 1, 'voltage': 65432, 'on': on}
        self.original = dict(self.state)
        self.mode = mode
        self.events = queue.Queue()
        self.calls = []
        self.read_threads = set()
        self.initials = 0

    def open(self, name):
        self.calls.append(('open', name))

    def initialize(self):
        self.initials += 1
        self.calls.append(('initial',))
        if self.mode == 'initial_timeout':
            return
        if ((self.mode == 'off_no_ack_query_timeout' and self.state['on'] == 0) or
                (self.mode == 'restore_no_ack' and any(x[0] == 'set' and x[2] == 1 for x in self.calls))):
            return
        state = dict(self.state)
        if self.mode == 'initial_mismatch' and self.initials == 2:
            state['voltage'] -= 1
        self.events.put((4, state))

    def read(self):
        self.read_threads.add(threading.get_ident())
        try:
            return self.events.get(timeout=.002)
        except queue.Empty:
            return 1, None  # V1 measurement progress continues while output OFF.

    def set_output(self, original, on):
        self.calls.append(('set', dict(original), on))
        self.state = dict(original, on=on)
        if self.mode == 'off_exception' and not on:
            raise RuntimeError('native send failed after possible side effect')
        if self.mode == 'disconnect' and not on:
            self.events.put((255, None))
            return
        if self.mode == 'off_mismatch' and not on:
            self.state['voltage'] -= 1
        if self.mode == 'off_no_ack_query_mismatch' and not on:
            self.state['voltage'] -= 1
        if self.mode == 'restore_mismatch' and on:
            self.state['current'] -= 1
        if self.mode == 'v1_off_status_on_silent':
            if not on:
                self.events.put((4, dict(self.state)))
            return
        if (self.mode.startswith('off_no_ack') and not on) or (self.mode == 'restore_no_ack' and on):
            return
        self.events.put((6, None))

    def close(self):
        self.calls.append(('close',))
        self.events.put((255, None))


class ControlTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix='.output-tests-', dir=c.ROOT)
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.output = self.root / 'audit.jsonl'

    def run_backend(self, backend, off=1, **kw):
        with patch.object(c.Controller, 'hold', return_value=None):
            return c.run(backend, 'COM7', [port()], self.output, off,
                         bool(off), timeout=.04, lock_directory=self.root, **kw)

    def audit(self):
        return [json.loads(line) for line in self.output.read_text().splitlines()]

    def test_snapshot_default_no_writes_and_closed(self):
        b = FakeBackend(on=0)
        result = self.run_backend(b, off=0)
        self.assertEqual(result['snapshot'], b.original)
        self.assertFalse(any(x[0] == 'set' for x in b.calls))
        self.assertEqual(b.calls[-1], ('close',))
        self.assertEqual(len(b.read_threads), 1)
        self.assertNotIn(threading.get_ident(), b.read_threads)
        self.assertTrue(self.audit()[-1]['complete'])
        self.assertFalse(list(self.root.glob('*.lock')))

    def test_toggle_only_on_preserves_exact_raw_limits_and_restores(self):
        b = FakeBackend()
        result = self.run_backend(b)
        writes = [x for x in b.calls if x[0] == 'set']
        self.assertEqual([x[2] for x in writes], [0, 1])
        self.assertTrue(all(x[1] == b.original for x in writes))
        self.assertEqual(b.state, b.original)
        self.assertTrue(result['restored_on_verified'])
        events = [x['event'] for x in self.audit()]
        self.assertEqual(events.count('verified'), 2)
        self.assertIn('restored_on_verified', events)

    def test_request_response_device_drops_overlapping_setter_but_new_flow_serializes(self):
        class RequestResponse(FakeBackend):
            def __init__(self):
                super().__init__()
                self.pending = None
                self.overlaps = []
                self.order = []

            def initialize(self):
                if self.pending:
                    self.overlaps.append('initial')
                    return
                self.pending = 4
                self.order.append('initial')
                super().initialize()

            def set_output(self, original, on):
                if self.pending:
                    self.overlaps.append('set')
                    return  # Model V1 ignoring a setter before prior 04 consumed.
                self.pending = 6
                self.order.append('set_on' if on else 'set_off')
                super().set_output(original, on)

        b = RequestResponse()
        receive = c.Controller.receive
        def consume(controller, kind, marker):
            result = receive(controller, kind, marker)
            received_kind = result[0] if isinstance(kind, tuple) else kind
            self.assertEqual(b.pending, received_kind)
            b.order.append('consumed_' + str(received_kind))
            b.pending = None
            return result
        with patch.object(c.Controller, 'receive', consume):
            result = self.run_backend(b)
        self.assertTrue(result['restored_on_verified'])
        self.assertEqual(b.overlaps, [])
        self.assertEqual(b.order, ['initial', 'consumed_4', 'initial', 'consumed_4',
                                   'set_off', 'consumed_6', 'initial', 'consumed_4',
                                   'set_on', 'consumed_6', 'initial', 'consumed_4'])
        self.assertFalse(any(x.get('operation') == 'readonly_epoch_sync' for x in self.audit()))

    def test_bootstrap_initial_precedes_reader_and_consumes_reply(self):
        b = FakeBackend()
        controller = c.Controller(b, lambda *args, **kw: None, .04)
        initialize = b.initialize
        def initial_before_start():
            self.assertIsNone(controller.thread.ident)
            self.assertEqual(controller.epoch, 1)
            initialize()
        b.initialize = initial_before_start
        try:
            self.assertEqual(controller.bootstrap(), b.original)
            self.assertEqual(b.initials, 1)
        finally:
            controller.close()

    def test_no_measurement_progress_prevents_off_but_recovery_sends_on_once(self):
        class NoProgress(FakeBackend):
            def read(self):
                return self.events.get()
        b = NoProgress()
        controller = c.Controller(b, lambda *args, **kw: None, .03)
        controller.start()
        with controller.condition:
            self.assertTrue(controller.condition.wait_for(lambda: controller.read_entered_epoch == 0, timeout=1))
        try:
            with self.assertRaisesRegex(RuntimeError, 'timeout synchronizing'):
                controller.toggle(b.original, 0)
            self.assertFalse(any(x[0] in ('initial', 'set') for x in b.calls))
            with self.assertRaisesRegex(RuntimeError, 'timeout'):
                controller.toggle(b.original, 1, recovery=True)
            self.assertEqual([x[2] for x in b.calls if x[0] == 'set'], [1])
            self.assertFalse(any(x[0] == 'initial' for x in b.calls))
        finally:
            controller.close()

    def test_off_failure_or_no_ack_always_attempts_on_and_is_non_success(self):
        for mode in ('off_no_ack_query_timeout', 'off_no_ack_query_mismatch', 'off_exception', 'off_mismatch'):
            self.output = self.root / (mode + '.jsonl')
            b = FakeBackend(mode)
            with self.subTest(mode=mode), self.assertRaises(RuntimeError):
                self.run_backend(b)
            self.assertEqual([x[2] for x in b.calls if x[0] == 'set'], [0, 1])
            self.assertEqual(b.state, b.original)
            self.assertFalse(self.audit()[-1]['complete'])
            self.assertTrue(self.audit()[-1]['restored_on_verified'])

    def test_no_06_but_exact_fresh_query_succeeds_without_set_retry(self):
        b = FakeBackend('off_no_ack')
        self.assertTrue(self.run_backend(b)['complete'])
        self.assertEqual([x[2] for x in b.calls if x[0] == 'set'], [0, 1])
        verified = [x for x in self.audit() if x['event'] == 'verified']
        self.assertEqual(verified[0]['confirmation_source'], 'query_only')
        self.assertFalse(verified[0]['ack_seen'])
        self.assertEqual(verified[1]['confirmation_source'], '06+query')
        self.assertTrue(verified[1]['ack_seen'])

    def test_observed_v1_off_status04_on_silent_then_query(self):
        b = FakeBackend('v1_off_status_on_silent')
        self.assertTrue(self.run_backend(b)['restored_on_verified'])
        self.assertEqual([x[2] for x in b.calls if x[0] == 'set'], [0, 1])
        verified = [x for x in self.audit() if x['event'] == 'verified']
        self.assertEqual([x['confirmation_source'] for x in verified],
                         ['status04+query', 'query_only'])
        self.assertEqual([x['ack_seen'] for x in verified], [False, False])
        self.assertEqual(b.state, b.original)

    def test_mismatched_unsolicited_status_fails_and_restores_on(self):
        class WrongStatus(FakeBackend):
            def set_output(self, original, on):
                if not on:
                    self.calls.append(('set', dict(original), on))
                    self.state = dict(original, on=0, voltage=original['voltage']-1)
                    self.events.put((4, dict(self.state)))
                else:
                    super().set_output(original, on)
        b = WrongStatus()
        with self.assertRaisesRegex(RuntimeError, 'unsolicited status mismatch'):
            self.run_backend(b)
        self.assertEqual([x[2] for x in b.calls if x[0] == 'set'], [0, 1])
        self.assertTrue(self.audit()[-1]['restored_on_verified'])

    def test_external_stop_callback_during_off_restores_on(self):
        b = FakeBackend()
        calls = []
        def stop_requested():
            calls.append(time.monotonic())
            return len(calls) >= 2
        with self.assertRaisesRegex(RuntimeError, 'external stop requested'):
            c.run(b, 'COM7', [port()], self.output, 1, True, .04, self.root,
                  stop_requested=stop_requested)
        self.assertEqual(len(calls), 2)
        self.assertEqual([x[2] for x in b.calls if x[0] == 'set'], [0, 1])
        self.assertTrue(self.audit()[-1]['restored_on_verified'])

    def test_external_stop_callback_failure_still_restores_on(self):
        b = FakeBackend()
        calls = []
        def broken_guard():
            calls.append(True)
            if len(calls) == 1:
                return False  # Pre-OFF admission passes; failure occurs in hold.
            raise ValueError('guard unavailable')
        with self.assertRaisesRegex(RuntimeError, 'guard unavailable'):
            c.run(b, 'COM7', [port()], self.output, 1, True, .04, self.root,
                  stop_requested=broken_guard)
        self.assertTrue(self.audit()[-1]['restored_on_verified'])

    def test_external_stop_before_off_sends_no_setters(self):
        b = FakeBackend()
        with self.assertRaisesRegex(RuntimeError, 'external stop requested before off'):
            self.run_backend(b, stop_requested=lambda: True)
        self.assertFalse(any(x[0] == 'set' for x in b.calls))
        self.assertEqual(b.calls[-1], ('close',))
        self.assertFalse((self.root / 'COM7.output-control.lock').exists())

    def test_external_stop_failure_before_off_sends_no_setters(self):
        b = FakeBackend()
        def broken_guard():
            raise ValueError('guard unavailable before off')
        with self.assertRaisesRegex(RuntimeError, 'guard unavailable before off'):
            self.run_backend(b, stop_requested=broken_guard)
        self.assertFalse(any(x[0] == 'set' for x in b.calls))
        self.assertEqual(b.calls[-1], ('close',))
        self.assertFalse((self.root / 'COM7.output-control.lock').exists())

    def test_restore_failure_marks_state_unknown(self):
        for mode in ('restore_no_ack', 'restore_mismatch', 'disconnect'):
            self.output = self.root / (mode + '.jsonl')
            b = FakeBackend(mode)
            with self.subTest(mode=mode), self.assertRaisesRegex(RuntimeError, 'STATE UNKNOWN'):
                self.run_backend(b)
            self.assertEqual([x[2] for x in b.calls if x[0] == 'set'], [0, 1])
            self.assertFalse(self.audit()[-1]['restored_on_verified'])

    def test_interrupt_during_off_restores(self):
        b = FakeBackend()
        with patch.object(c.Controller, 'hold', side_effect=KeyboardInterrupt), self.assertRaisesRegex(RuntimeError, 'KeyboardInterrupt'):
            c.run(b, 'COM7', [port()], self.output, 1, True, .04, self.root)
        self.assertEqual(b.state, b.original)
        self.assertTrue(self.audit()[-1]['restored_on_verified'])

    def test_bad_initial_state_never_sends_off(self):
        for mode, on in (('initial_timeout', 1), ('initial_mismatch', 1), ('', 0)):
            self.output = self.root / (mode + str(on) + '.jsonl')
            b = FakeBackend(mode, on)
            with self.assertRaises(RuntimeError):
                self.run_backend(b)
            self.assertFalse(any(x[0] == 'set' for x in b.calls))
            self.assertEqual(b.calls[-1], ('close',))

    def test_queued_stale_ack_cannot_satisfy_new_command(self):
        b = FakeBackend('off_no_ack_query_timeout')
        logs = []
        controller = c.Controller(b, lambda event, **data: logs.append((event, data)), .03)
        controller.events.put((1, time.monotonic(), 6, None))
        controller.sequence = 1
        controller.start()
        try:
            with self.assertRaisesRegex(RuntimeError, 'timeout'):
                controller.toggle(b.original, 0)
            self.assertTrue(any(e == 'discard_stale' for e, _ in logs))
        finally:
            controller.close()

    def test_inflight_old_status_or_ack_never_upgraded_to_new_request_epoch(self):
        for old_kind in (4, 6):
            class InFlight(FakeBackend):
                def __init__(self):
                    super().__init__('off_no_ack')
                    self.parsed = threading.Event()
                    self.release = threading.Event()
                    self.first = True

                def read(self):
                    if self.first:
                        self.first = False
                        old_payload = dict(self.state) if old_kind == 4 else None
                        self.parsed.set()  # Old packet parsed; publication is paused.
                        self.release.wait()
                        return old_kind, old_payload
                    return super().read()

                def initialize(self):
                    self.calls.append(('initial',))
                    self.release.set()  # Release the old response, never emit a fresh one.

            b = InFlight()
            controller = c.Controller(b, lambda *args, **kw: None, .04)
            controller.start()
            self.assertTrue(b.parsed.wait(1))
            def release_old():
                with controller.condition:
                    controller.condition.wait_for(lambda: controller.epoch > 0, timeout=1)
                b.release.set()  # Measurement progress, not a wake-up command.
            release_thread = threading.Thread(target=release_old)
            release_thread.start()
            try:
                with self.subTest(kind=old_kind), self.assertRaisesRegex(RuntimeError, 'timeout'):
                    if old_kind == 4:
                        controller.snapshot()
                    else:
                        controller.toggle(b.original, 0)
                self.assertEqual(controller.cross_epoch_drops, 1)
                self.assertLessEqual(sum(x[0] == 'initial' for x in b.calls), 1)
                self.assertLessEqual(sum(x[0] == 'set' for x in b.calls), 1)
            finally:
                b.release.set()
                controller.close()
                release_thread.join(1)

    def test_close_exception_retains_com_lock(self):
        b = FakeBackend()
        def broken_close():
            b.calls.append(('close',))
            b.events.put((255, None))
            raise OSError('close operation failed')
        b.close = broken_close
        with self.assertRaisesRegex(RuntimeError, 'close failed'):
            self.run_backend(b, off=0)
        self.assertTrue((self.root / 'COM7.output-control.lock').exists())
        self.assertTrue(self.audit()[-1]['com_lock_retained'])

    def test_same_epoch_buffered_reply_read_before_target_not_fresh(self):
        real_counter = time.perf_counter_ns
        main_ident = threading.get_ident()
        for old_kind in (4, 6):
            class Buffered(FakeBackend):
                def __init__(self):
                    super().__init__('off_no_ack')
                    self.initial_read = threading.Event()
                    self.wake_old_read = threading.Event()
                    self.allow_buffered = threading.Event()
                    self.timestamp_captured = threading.Event()
                    self.reads = 0
                    self.buffered_returned = False

                def initialize(self):
                    self.calls.append(('initial',))
                    self.wake_old_read.set()  # Sync only; no fresh target response.

                def read(self):
                    self.reads += 1
                    if self.reads == 1:
                        self.initial_read.set()
                        self.wake_old_read.wait()
                        return 0, None
                    if self.reads == 2:
                        self.allow_buffered.wait()
                        payload = dict(self.state) if old_kind == 4 else None
                        self.buffered_returned = True
                        return old_kind, payload
                    return super().read()

            b = Buffered()
            def log(event, **data):
                if event == 'request' and data.get('operation') in ('initial', 'set_off'):
                    # begin holds publication lock: let an old same-epoch packet
                    # finish read and acquire its timestamp before target action.
                    b.allow_buffered.set()
                    self.assertTrue(b.timestamp_captured.wait(1))

            def marked_counter():
                stamp = real_counter()
                if threading.get_ident() != main_ident and b.buffered_returned:
                    b.timestamp_captured.set()
                return stamp

            controller = c.Controller(b, log, .04)
            controller.start()
            self.assertTrue(b.initial_read.wait(1))
            def advance_old_read():
                with controller.condition:
                    controller.condition.wait_for(lambda: controller.epoch > 0, timeout=1)
                b.wake_old_read.set()
            advance_thread = threading.Thread(target=advance_old_read)
            advance_thread.start()
            try:
                with patch.object(c.time, 'perf_counter_ns', side_effect=marked_counter):
                    with self.subTest(kind=old_kind), self.assertRaisesRegex(RuntimeError, 'timeout'):
                        if old_kind == 4:
                            controller.snapshot()
                        else:
                            controller.toggle(b.original, 0)
                self.assertEqual(controller.cross_epoch_drops, 0)
                self.assertLessEqual(sum(x[0] == 'set' for x in b.calls), 1)
            finally:
                b.allow_buffered.set()
                controller.close()
                advance_thread.join(1)

    def test_same_perf_counter_tick_is_conservatively_not_fresh(self):
        controller = c.Controller(FakeBackend(), lambda *args, **kw: None, .02)
        controller.events.put((1, time.monotonic(), 6, None, 4, 123456))
        with self.assertRaisesRegex(RuntimeError, 'timeout'):
            controller.receive(6, (0, 4, 123456))

    def test_recovery_snapshot_exception_still_sends_on_once(self):
        b = FakeBackend()
        b.initialize = lambda: (_ for _ in ()).throw(OSError('readback initial unavailable'))
        controller = c.Controller(b, lambda *args, **kw: None, .03)
        controller.start()
        try:
            try:
                controller.toggle(b.original, 1, recovery=True)
            except (RuntimeError, OSError):
                pass  # Verification may fail, but the one ON send must occur.
            self.assertEqual([x[2] for x in b.calls if x[0] == 'set'], [1])
        finally:
            controller.close()

    def test_parser_join_timeout_retains_com_lock(self):
        class Stuck(FakeBackend):
            def __init__(self):
                super().__init__()
                self.blocked = threading.Event()

            def read(self):
                if self.initials >= 2 and self.events.empty():
                    self.blocked.set()
                    return self.events.get()
                return super().read()
        b = Stuck()
        b.close = lambda: b.calls.append(('close_did_not_unblock_parser',))
        original_snapshot = c.Controller.snapshot
        def ensure_reader_blocked(controller):
            state = original_snapshot(controller)
            self.assertTrue(b.blocked.wait(1))
            return state
        try:
            with patch.object(c.Controller, 'snapshot', ensure_reader_blocked), self.assertRaisesRegex(RuntimeError, 'parser did not stop'):
                self.run_backend(b, off=0)
            self.assertTrue((self.root / 'COM7.output-control.lock').exists())
            self.assertTrue(self.audit()[-1]['com_lock_retained'])
        finally:
            b.events.put((255, None))  # Test-only cleanup of fake parser.

    def test_measurement_flood_filtered(self):
        b = FakeBackend()
        for _ in range(2000):
            b.events.put((1, None))
        self.run_backend(b, off=0)
        self.assertFalse(any(x.get('kind') == 1 for x in self.audit()))

    def test_requested_15_minute_window_uses_existing_finally_on(self):
        b = FakeBackend()
        with patch.object(c.Controller, 'hold') as hold:
            c.run(b, 'COM7', [port()], self.output, 900, True, .04, self.root)
        self.assertTrue(hold.called)
        self.assertGreater(hold.call_args.args[0], 890)
        self.assertEqual(b.state, b.original)
        self.assertTrue(self.audit()[-1]['restored_on_verified'])

    def test_validation_identity_confirmation_exclusive_output_and_lock(self):
        for devices in ([port(serial='other')], [port(vid=1)], [port(), port()],
                        [port(), port('COM8', vid=0x303a)],
                        [port(), port('COM8', vid=2, description='USB CDC')]):
            with self.assertRaises(ValueError):
                c.validate_identity('COM7', devices)
        for seconds, confirmed in ((1, False), (-1, True), (1201, True), (True, True), (1.0, True)):
            b = FakeBackend()
            with self.assertRaises(ValueError):
                c.run(b, 'COM7', [port()], self.output, seconds, confirmed, .04, self.root)
            self.assertEqual(b.calls, [])
        self.output.write_text('existing')
        b = FakeBackend()
        with self.assertRaises(FileExistsError):
            self.run_backend(b, off=0)
        self.assertEqual(b.calls, [])
        self.assertEqual(self.output.read_text(), 'existing')
        self.output.unlink()
        lock = self.root / 'COM7.output-control.lock'
        lock.write_text('another process')
        with self.assertRaises(FileExistsError):
            self.run_backend(b, off=0)
        self.assertEqual(lock.read_text(), 'another process')

    def test_real_inventory_jlink_and_bluetooth_allowed_board_usb_rejected(self):
        inventory = [port('COM8', 0x1366, 0x0105, 'jlink', 'JLink CDC UART Port'),
                     port('COM21'), port('COM5', None, None, None, 'Bluetooth serial')]
        self.assertEqual(c.validate_identity('COM21', inventory), 'COM21')
        with self.assertRaises(ValueError):
            c.validate_identity('COM21', inventory + [port('COM9', 0x303a, 0x1001, 'board')])

    def test_audit_io_failure_after_off_does_not_prevent_restore(self):
        real_dumps = json.dumps
        def failing_dumps(value, **kw):
            if value.get('event') == 'verified' and value['raw']['on'] == 0:
                raise OSError('simulated disk full')
            return real_dumps(value, **kw)
        b = FakeBackend()
        with patch.object(c.json, 'dumps', side_effect=failing_dumps), self.assertRaisesRegex(RuntimeError, 'audit logging failed'):
            self.run_backend(b)
        self.assertEqual([x[2] for x in b.calls if x[0] == 'set'], [0, 1])
        self.assertEqual(b.state, b.original)
        self.assertTrue(self.audit()[-1]['restored_on_verified'])

    def test_documented_bindings_and_immediate_same_thread_snapshot(self):
        class Function:
            def __init__(self, result=None): self.result = result
            def __call__(self, *args): return self.result
        lib = SimpleNamespace(**{name: Function(value) for name, value in (
            ('iot_get_current_uint', 1), ('iot_get_set_current', 123),
            ('iot_get_set_voltage', 456), ('iot_uart_send_set', None),
            ('iot_parse', 4), ('iot_get_power_on', 1))})
        def fake_init(instance, *args): instance.lib = lib
        with patch.object(c.NativeBackend, '__init__', fake_init):
            backend = c.OutputBackend('not-loaded', 'not-used')
        self.assertEqual(lib.iot_uart_send_set.argtypes,
                         [ctypes.c_uint32, ctypes.c_uint8, ctypes.c_uint32, ctypes.c_uint8])
        self.assertIsNone(lib.iot_uart_send_set.restype)
        self.assertEqual(lib.iot_get_set_current.restype, ctypes.c_uint16)
        self.assertEqual(backend.read(), (4, {'current': 123, 'unit': 1, 'voltage': 456, 'on': 1}))


if __name__ == '__main__':
    unittest.main()
