"""Host safety tests: no actual UART, DLL, device, network, or keys."""
import contextlib
import hashlib
import io
import json
from pathlib import Path
import tempfile
import threading
import types
import unittest
from unittest.mock import patch

import mosaico_gauge_contrast_experiment as e


class Clock:
    def __init__(self):
        self.now = 100.

    def monotonic(self):
        return self.now

    def time(self):
        return 1800000000 + self.now


class Serial:
    timeout = .1
    write_timeout = 3

    def __init__(self):
        self.writes = []

    def write(self, data):
        self.writes.append(data)
        return len(data)

    def flush(self):
        pass


class Client:
    uart = True
    wake_preamble = True

    def __init__(self, clock):
        self.serial, self.clock, self.preamble_delay, self.fail = Serial(), clock, 0, False

    def _send_preamble(self):
        self.serial.write(b'U' * 32 + b'\n')
        self.clock.now += self.preamble_delay

    def _command_once(self, text, expected, timeout):
        from serial_debug_test import encode_command
        self.serial.write(encode_command(text, True))
        if self.fail:
            raise TimeoutError('unknown ACK')
        print('DBG RESULT command=' + expected + ' status=PASS value=1')
        return types.SimpleNamespace(status='PASS', details='value=1')


def rows(ccs, rms=None, endpoint=False):
    output = []
    for i, cc in enumerate(ccs):
        row = dict(monotonic=10 + i * 30, gauge=dict(raw_cc=str((65534 + cc) & 65535),
                   rm_mah=str(65 if rms is None else rms[i])), engine=dict(CEDVRemCap='65', SmoothRemCap='65'))
        output.append(row)
    if endpoint:
        crossing = next(i for i, cc in enumerate(ccs) if cc >= 3)
        output[-2].update(endpoint_check=1, monotonic=output[crossing]['monotonic'] + 10)
        output[-1].update(endpoint_check=2, monotonic=output[crossing]['monotonic'] + 15)
    return output


class FakeCall:
    uncertain = False
    deadline = None
    check = None

    def __init__(self):
        self.commands = []
        self.settings = {key: '1' for key in e.CONFIG_KEYS}
        self.settings.update(temporary='1', lock_wifi_minutes='60', lock_ble_minutes='60', smart_keepalive='0')

    def __call__(self, command, **kwargs):
        self.commands.append(command)
        if e.mutation(command):
            self.check(command)
        if command == 'settings get':
            return dict(self.settings)
        return {}

    @contextlib.contextmanager
    def group(self):
        yield


class FakePower:
    def __init__(self):
        self.stop = threading.Event()
        self.on_known, self.source, self.thread = True, 'on', None
        self.footer, self.error = {}, None
        self.snapshots = 0

    def snapshot(self):
        self.snapshots += 1


class HostTests(unittest.TestCase):
    def setUp(self):
        self.root = e.ROOT / 'artifacts/gauge-contrast-host-tests'
        self.root.mkdir(parents=True, exist_ok=True)
        self.tmp = tempfile.TemporaryDirectory(dir=self.root)
        self.out = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def experiment(self):
        call, power, clock = FakeCall(), FakePower(), Clock()
        watcher = types.SimpleNamespace(sticky=threading.Event())
        args = types.SimpleNamespace(stage='observe', arm='C', expected_boot=10)
        number = getattr(self, 'run_number', 0) + 1
        self.run_number = number
        out = self.out / f'run-{number}'
        out.mkdir()
        obj = e.Experiment(call, power, watcher, out, args, [], [], 'proof', clock)
        return obj, call, power, clock

    def test_default_dry_never_loads_ports_or_dll(self):
        with patch.object(e, 'load_proof', side_effect=AssertionError('proof touched')), patch.object(e, 'Power', side_effect=AssertionError('DLL touched')):
            capture = io.StringIO()
            with contextlib.redirect_stdout(capture):
                self.assertEqual(e.main([]), 0)
                self.assertEqual(e.main(['--stage', 'observe', '--arm', 'B']), 0)
            self.assertIn('"ports_opened": 0', capture.getvalue())

    def test_execute_missing_explicit_admission_rejected_before_io(self):
        with patch.object(e, 'source_fields', side_effect=AssertionError('input touched')):
            with self.assertRaises(ValueError):
                e.main(['--execute'])

    def test_source_full131_and_only_three_targets(self):
        source = e.source_fields()
        c, b = e.target_fields(source, 'C'), e.target_fields(source, 'B')
        self.assertEqual(len(source), 131)
        self.assertEqual([x['address'] for x, y in zip(source, c) if x != y], ['929b', '929d', '929f'])
        self.assertEqual([x['address'] for x, y in zip(c, b) if x != y], ['929b'])
        self.assertEqual(e.canonical_crc(b), '675dd812')

    def test_modular_wrap_and_half_range(self):
        self.assertEqual(e.signed_mod_delta(1, 65534), 3)
        self.assertEqual(e.signed_mod_delta(65534, 1), -3)
        for args in ((32768, 0), (-1, 0), (65536, 0)):
            with self.assertRaises(ValueError):
                e.signed_mod_delta(*args)

    def test_no_count_no_confirmation_and_no_guessed_0073(self):
        self.assertEqual(e.analyse(rows([0, 1, 2, 2]))['outcome'], 'inconclusive')
        self.assertEqual(e.analyse(rows([0, 1, 2, 3, 3, 3]))['outcome'], 'inconclusive')
        data = rows([0, 1, 2, 3, 3, 3], endpoint=True)
        self.assertEqual(e.analyse(data)['outcome'], 'platform')
        data[-1]['engine'] = {'raw73': '00' * 23}
        self.assertEqual(e.analyse(data)['outcome'], 'inconclusive')

    def test_endpoint_latency_two_reads_and_residual(self):
        data = rows([0, 1, 2, 3, 3, 3], [65, 64, 63, 62, 62, 62], endpoint=True)
        self.assertEqual(e.analyse(data)['outcome'], 'tracking')
        data[-1]['monotonic'] -= 1
        self.assertFalse(e.analyse(data)['confirmed_after_three_counts'])
        data[-1]['monotonic'] += 1
        data[-1]['gauge']['rm_mah'] = '60'
        self.assertEqual(e.analyse(data)['outcome'], 'nontracking')

    def test_budget_minimum450_never_expands_global_or_max480(self):
        self.assertEqual(e.off_budget(0, 660, 600), 480)
        self.assertEqual(e.off_budget(29, 660, 600), 470)
        self.assertEqual(e.off_budget(49, 660, 600), 450)
        for args in ((50, 660, 600), (100, 640, 700), (0, float('inf'), 600)):
            with self.assertRaises(ValueError):
                e.off_budget(*args)

    def test_mutation_fence_checks_at_wire_after_preamble(self):
        clock = Clock()
        client = Client(clock)
        raw = client.serial
        call = e.Console(client, self.out, clock)
        call.deadline = clock.now + 300
        checked = []
        call.check = lambda command: checked.append((command, clock.now))
        client.preamble_delay = 1
        call('gauge-contrast apply CONFIRM_EXTERNAL_POWER')
        self.assertEqual(checked, [('gauge-contrast apply CONFIRM_EXTERNAL_POWER', 101)])
        self.assertEqual(len(raw.writes), 2)
        self.assertEqual(raw.write_timeout, 3)

    def test_late_preamble_blocks_real_frame_and_unknown_ack_never_retried(self):
        clock, client = Clock(), None
        client = Client(clock)
        raw = client.serial
        call = e.Console(client, self.out, clock)
        call.deadline = clock.now + 300
        call.check = lambda command: None
        client.preamble_delay = 7
        with self.assertRaises(TimeoutError):
            call('gauge-contrast apply CONFIRM_EXTERNAL_POWER', timeout=6)
        self.assertEqual(raw.writes, [b'U' * 32 + b'\n'])
        self.assertTrue(call.uncertain)
        with self.assertRaises(ValueError):
            call('gauge-contrast restore CONFIRM')
        self.assertEqual(len(raw.writes), 1)

    def test_unknown_ack_latches_mutations_but_readonly_remains(self):
        clock = Clock()
        client = Client(clock)
        call = e.Console(client, self.out, clock)
        call.deadline = clock.now + 300
        call.check = lambda command: None
        client.fail = True
        with self.assertRaises(TimeoutError):
            call('gauge-contrast apply CONFIRM_EXTERNAL_POWER')
        client.fail = False
        self.assertEqual(call('gauge-contrast status'), {'value': '1'})
        with self.assertRaises(ValueError):
            call('settings restore')

    def test_floor_first_immediately_sets_independent_stop(self):
        obj, call, power, _ = self.experiment()
        def response(command, **kwargs):
            call.commands.append(command)
            return {'voltage_mv': '3900'}
        call.__class__ = type('FloorCall', (FakeCall,), {'__call__': staticmethod(response)})
        with self.assertRaises(ValueError):
            obj.sample()
        self.assertEqual(call.commands, ['gauge-charge'])
        self.assertTrue(power.stop.is_set())

    def test_rebase_once_identical_values_does_not_extend_global(self):
        obj, call, power, clock = self.experiment()
        obj.apply_confirmed = True
        obj.report['target131_verified'] = True
        obj.ram_deadline = 500
        for command in ('settings set lock_wifi_minutes 60 600', 'settings set lock_ble_minutes 60 600', 'settings set smart_keepalive 0 600'):
            obj.sent[command] = 1
        obj.state = lambda phase: {'phase': str(phase), 'images_capability_verified': '1'}
        obj.pages = lambda *args: None
        obj.sample = lambda *args: {'physical': {'voltage_mv': '4200'}}
        old = obj.deadline
        obj.rebase_quiet_once()
        self.assertEqual(obj.deadline, old)
        self.assertEqual(obj.ram_deadline, clock.now + 600)
        self.assertEqual(power.snapshots, 1)
        self.assertTrue(all(v == 2 for v in obj.sent.values()))
        self.assertTrue(obj.report['RAM_alignment']['identical_settings'])
        with self.assertRaises(ValueError):
            obj.rebase_quiet_once()

    def test_rebase_rejects_STOP_unknown_ack_active_OFF_and_missing_apply(self):
        for fault in ('stop', 'uncertain', 'OFF', 'unapplied'):
            obj, call, power, _ = self.experiment()
            obj.apply_confirmed = True
            obj.report['target131_verified'] = True
            if fault == 'stop':
                power.stop.set()
            elif fault == 'uncertain':
                call.uncertain = True
            elif fault == 'OFF':
                power.thread = object()
            else:
                obj.apply_confirmed = False
            with self.assertRaises(ValueError):
                obj.rebase_quiet_once()
            self.assertFalse(obj.rebase_used)
            self.assertEqual(call.commands, [])

    def test_setup300_late_apply_never_arms_or_renews(self):
        obj, _, _, clock = self.experiment()
        obj.ram_deadline = clock.now + 600
        clock.now += 301
        before = obj.deadline
        with self.assertRaises(ValueError):
            obj.before_write('gauge-contrast apply CONFIRM_EXTERNAL_POWER')
        self.assertIsNone(obj.apply_start)
        self.assertEqual(obj.deadline, before)
        self.assertFalse(obj.apply_sent)

    def test_apply_wire_arms660_once_not_ack_relative_and_second_refused(self):
        obj, call, _, clock = self.experiment()
        obj.ram_deadline = clock.now + 600
        clock.now += 100
        obj.before_write('gauge-contrast apply CONFIRM_EXTERNAL_POWER')
        self.assertEqual(obj.deadline, clock.now + 660)
        self.assertEqual(obj.apply_start, clock.now)
        self.assertEqual(call.deadline, min(obj.ram_deadline, obj.deadline))
        self.assertTrue(obj.report['deadline_armed'])
        self.assertEqual(obj.report['setup_elapsed'], 100)
        before = obj.deadline
        clock.now += 20
        with self.assertRaises(ValueError):
            obj.before_write('gauge-contrast apply CONFIRM_EXTERNAL_POWER')
        self.assertEqual(obj.deadline, before)

    def test_setup_expiring_during_preamble_refuses_wire_no_deadline_renewal(self):
        obj, _, _, clock = self.experiment()
        client = Client(clock)
        raw = client.serial
        call = e.Console(client, self.out, clock)
        obj.call = call
        call.check = obj.before_write
        call.deadline = obj.setup_deadline
        obj.ram_deadline = clock.now + 600
        clock.now = obj.setup_deadline - 1
        client.preamble_delay = 2
        before = obj.deadline
        with self.assertRaises(ValueError):
            call('gauge-contrast apply CONFIRM_EXTERNAL_POWER', timeout=90)
        self.assertEqual(raw.writes, [b'U' * 32 + b'\n'])
        self.assertIsNone(obj.apply_start)
        self.assertEqual(obj.deadline, before)

    def test_evidence_hash_and_B_requires_real_C_platform(self):
        d = dict(kind='GCT1_contrast_experiment', stage='observe', arm='C', complete=True,
            original_verified=True, V1_ON_verified=True, settings_restored=True, source_crc=e.SOURCE,
            legacy_crc='430c5ed4', target131_verified=True, unknown_ack=False, errors=[], image_bindings=['r13/r14'],
            analysis={'outcome': 'inconclusive', 'confirmed_after_three_counts': False}, apply_start_monotonic=100)
        path = self.out / 'C.json'
        sha = e.save(path, d)
        with self.assertRaises(ValueError):
            e.load_evidence(path, sha, stage='observe', arm='C', image_bindings=['r13/r14'])
        d['analysis'] = {'outcome': 'platform', 'confirmed_after_three_counts': True}
        path2 = self.out / 'C-platform.json'
        sha2 = e.save(path2, d)
        e.save(Path(str(path2) + '.complete.json'), dict(complete=True, within_deadline=True, report_sha256=sha2))
        e.save(self.out / 'root-acceptance.json', dict(report_sha256=sha2, child_exit_code=0, observed_child_exit_monotonic=200))
        self.assertTrue(e.load_evidence(path2, sha2, stage='observe', arm='C', image_bindings=['r13/r14']))
        with self.assertRaises(ValueError):
            e.load_evidence(path2, '0' * 64, stage='observe', arm='C')
        with self.assertRaises(ValueError):
            e.load_evidence(path2, sha2, stage='observe', arm='C', image_bindings=['other BIN'])

    def test_STOP_watcher_is_sticky_and_predicate_does_not_read_files(self):
        stop = threading.Event()
        watcher = e.StopWatcher(self.out / 'STOP', stop)
        watcher.start()
        try:
            watcher.path.write_text('STOP')
            self.assertTrue(stop.wait(2))
            watcher.path.unlink()
            self.assertTrue(watcher.sticky.is_set())
            with patch.object(Path, 'exists', side_effect=AssertionError('predicate IO')):
                self.assertTrue(stop.is_set())
        finally:
            watcher.close()

    def test_no_old_mutations_restart_or_direct_i2c_in_allowlist(self):
        for command in ('gauge-factory-scale status', 'gauge-factory-scale apply CONFIRM_EXTERNAL_POWER',
                        'runtime-restart CONFIRM_EXTERNAL_POWER 430c5ed4', 'gauge-access open', 'settings set smart_keepalive 1 600'):
            self.assertFalse(e.allowed(command))

    def recovery_harness(self, unknown=False):
        obj, _, power, clock = self.experiment()
        events = []
        phase = [0]
        status = dict(schema='1', namespace='gcontrast', source_crc=e.SOURCE, factor='0', verified='0',
            capacity_calibrated='0', original3000_UNTRUSTED='1', legacy_Failed_preserved='1',
            never_publish_physical_units='1', deadline_seconds='720', host_deadline_seconds='660',
            third_value='0', unexpected_count='0', arm='C', phase='2')
        class Call:
            uncertain = False
            deadline = None
            check = obj.before_write
            def __call__(self, command, **kwargs):
                events.append(command)
                if e.mutation(command):
                    self.check(command)
                if command == 'gauge-contrast apply CONFIRM_EXTERNAL_POWER':
                    phase[0] = 2
                    if unknown:
                        self.uncertain = True
                        raise TimeoutError('apply ACK unknown')
                    return dict(status)
                if command == 'gauge-contrast restore CONFIRM':
                    phase[0] = 0
                    return dict(status, phase='0')
                if command == 'gauge':
                    return {'design_mah': '3000', 'fcc_mah': '3000', 'op_status': '0026'}
                return {'evidence_crc': '430c5ed4'}
        obj.call = Call()
        obj.ram_deadline = clock.now + 600
        obj.legacy = {'evidence_crc': '430c5ed4'}
        def on():
            events.append('INDEPENDENT_V1_ON_DOUBLE_QUERY')
            power.on_known = True
        power.ensure_on = on
        obj.prepare = lambda: None
        def state(expected=None):
            if unknown:
                # Firmware recovery is only observed; host never sends keys.
                phase[0] = 0
            return dict(status, phase=str(phase[0]))
        obj.state = state
        obj.pages = lambda *args: None
        def sample(*args):
            power.stop.set()
            raise ValueError('floor injected')
        obj.sample = sample
        return obj, events

    def test_after_apply_exception_independent_ON_precedes_restore(self):
        obj, events = self.recovery_harness()
        with patch.object(e, 'legacy_check', side_effect=lambda d: d):
            report = obj.run()
        self.assertTrue(report['original_verified'])
        self.assertFalse(report['complete'])
        self.assertEqual(events.count('gauge-contrast apply CONFIRM_EXTERNAL_POWER'), 1)
        self.assertEqual(events.count('gauge-contrast restore CONFIRM'), 1)
        self.assertLess(events.index('INDEPENDENT_V1_ON_DOUBLE_QUERY'), events.index('gauge-contrast restore CONFIRM'))

    def test_unknown_apply_ACK_no_host_restore_or_other_keys(self):
        obj, events = self.recovery_harness(unknown=True)
        with patch.object(e, 'legacy_check', side_effect=lambda d: d):
            report = obj.run()
        self.assertTrue(report['unknown_ack'])
        self.assertTrue(report['original_verified'])
        self.assertFalse(report['complete'])
        self.assertNotIn('gauge-contrast restore CONFIRM', events)
        self.assertEqual(events.count('gauge-contrast apply CONFIRM_EXTERNAL_POWER'), 1)

    def physical_console(self, detail=None, failure=None):
        obj, _, power, clock = self.experiment()
        class Physical(Client):
            def _command_once(self, text, expected, timeout):
                from serial_debug_test import encode_command
                self.serial.write(encode_command(text, True))
                if failure is not None:
                    raise failure
                print('DBG RESULT command=' + expected + ' status=PASS ' + detail)
                return types.SimpleNamespace(status='PASS', details=detail)
        client = Physical(clock)
        call = e.Console(client, self.out, clock)
        call.safety_hook = obj.safety
        call.failure_hook = lambda cmd, exc: power.stop.set()
        return call, power

    def test_real_Console_floor_STOP_before_first_log_open_and_primary_fault_preserved(self):
        call, power = self.physical_console('valid=1 readonly=1 voltage_mv=3900 temperature_dK=3000 op_status=0026')
        def blocked_open(*args, **kwargs):
            self.assertTrue(power.stop.is_set(), 'STOP must precede first audit open')
            raise OSError('disk unavailable')
        with patch.object(Path, 'open', side_effect=blocked_open):
            with self.assertRaisesRegex(ValueError, '3900mV') as caught:
                call('gauge-charge')
        self.assertTrue(any('disk unavailable' in note for note in caught.exception.__notes__))
        self.assertTrue(call.audit_errors)

    def test_real_Console_invalid_voltage_temperature_and_unknown_STOP_before_fsync(self):
        cases = [('valid=1 readonly=1 voltage_mv=0 temperature_dK=3000 op_status=0026', None),
                 ('valid=1 readonly=1 voltage_mv=4200 temperature_dK=3300 op_status=0026', None),
                 (None, TimeoutError('physical reply unknown'))]
        for detail, failure in cases:
            call, power = self.physical_console(detail, failure)
            def check_fsync(fd):
                self.assertTrue(power.stop.is_set(), 'STOP must precede audit fsync')
                raise OSError('injected disk fault')
            with patch.object(e.os, 'fsync', side_effect=check_fsync):
                with self.assertRaises((ValueError, TimeoutError)):
                    call('gauge-charge')
            self.assertTrue(power.stop.is_set())

    def production_power(self, complete=True, footer_error=None, native_error=None,
                         kind=None, message=None, owned=False):
        power = e.Power.__new__(e.Power)
        power.stop = threading.Event()
        power.thread = types.SimpleNamespace(join=lambda **kwargs: None, is_alive=lambda: False)
        power.monitor = types.SimpleNamespace(join=lambda **kwargs: None, is_alive=lambda: False)
        power.monitor_error = None
        power.original = dict(current=500, unit=1, voltage=5000, on=1)
        power.footer = dict(restored_on_verified=True, com_lock_retained=False, original=power.original,
                            complete=complete, error=footer_error)
        power.error, power.error_kind, power.error_message = native_error, kind, message
        power.stop_owned, power.cycle_fault = owned, None
        power.snapshot = lambda: None
        return power

    def test_production_ensure_ON_rejects_all_three_native_footer_counterexamples(self):
        expected = 'RuntimeError: external stop requested during off interval'
        for power in (
            self.production_power(native_error='RuntimeError: parser failed after footer'),
            self.production_power(complete=False),
            self.production_power(complete=False, footer_error=expected, native_error='ValueError: unexpected reader error',
                                  kind='ValueError', message='unexpected reader error', owned=True)):
            power.ensure_on()
            self.assertIsNotNone(power.cycle_fault)

    def test_production_ensure_ON_accepts_only_clean_or_owned_exact_stop(self):
        expected = 'RuntimeError: external stop requested during off interval'
        natural = self.production_power()
        natural.ensure_on()
        self.assertIsNone(natural.cycle_fault)
        controlled = self.production_power(complete=False, footer_error=expected,
            native_error=repr(RuntimeError(expected)), kind='RuntimeError', message=expected, owned=True)
        controlled.ensure_on()
        self.assertIsNone(controlled.cycle_fault)
        controlled.stop_owned = False
        controlled.ensure_on()
        self.assertIsNotNone(controlled.cycle_fault)

    def test_production_ensure_ON_missing_lock_or_error_is_not_success(self):
        for key in ('com_lock_retained', 'error'):
            power = self.production_power()
            del power.footer[key]
            if key == 'com_lock_retained':
                with self.assertRaises(ValueError):
                    power.ensure_on()
            else:
                power.ensure_on()
                self.assertIsNotNone(power.cycle_fault)

    def test_pages_absolute_group_includes_disk_persistence(self):
        obj, _, _, clock = self.experiment()
        def pages(*args):
            clock.now += 6.01
            return {}
        obj._pages = pages
        with self.assertRaisesRegex(ValueError, 'including persistence'):
            obj.pages([], {}, 1, 'unused')

    def test_B_old_boot_platform_rejected_before_ports_DLL_or_run_creation(self):
        pairs = [{'image': dict(version='r13', sha256='a', size=1)}, {'image': dict(version='r14', sha256='b', size=1)}]
        def accepted(path, sha, **kwargs):
            return {'boot_pin': 99}
        with patch.object(e, 'source_fields', return_value=[]), patch.object(e, 'target_fields', return_value=[]), \
             patch.object(e, 'load_proof', return_value=('proof', pairs)), patch.object(e, 'load_evidence', side_effect=accepted), \
             patch.object(e, 'Power', side_effect=AssertionError('DLL touched')), \
             patch.object(e, 'topology', side_effect=AssertionError('ports touched')):
            with self.assertRaisesRegex(ValueError, 'exact observation boot'):
                e.main(['--execute', '--confirm-v1-sole-5v', '--expected-boot', '100', '--arm', 'B', '--stage', 'observe',
                    '--proof', 'unused', '--on-smoke-evidence', 'unused', '--c-platform-evidence', 'unused'])

    def test_slow_final_report_never_becomes_accepted_evidence_even_failure_receipt_IO_fails(self):
        obj, _, _, clock = self.experiment()
        obj.report.update(complete=True, original_verified=True, V1_ON_verified=True, settings_restored=True,
                          target131_verified=True)
        real_save = e.save
        def slow(path, data):
            if str(path).endswith('.failed.json'):
                raise OSError('failure receipt disk unavailable')
            digest = real_save(path, data)
            if Path(path).name == 'report.json':
                clock.now = obj.deadline + 1
            return digest
        with patch.object(e, 'save', side_effect=slow):
            obj.finish_report()
        self.assertFalse(obj.report['complete'])
        self.assertTrue(obj.claim.exists())
        path = obj.out / 'report.json'
        disk = json.loads(path.read_bytes())
        self.assertTrue(disk['complete'])  # immutable raw report never rewritten
        sha = hashlib.sha256(path.read_bytes()).hexdigest()
        with self.assertRaisesRegex(ValueError, 'unfinished/failed'):
            e.load_evidence(path, sha, stage='observe', arm='C')

    def test_slow_completion_receipt_retains_UNFINISHED_claim(self):
        obj, _, _, clock = self.experiment()
        obj.report['complete'] = True
        real_save = e.save
        def slow(path, data):
            digest = real_save(path, data)
            if str(path).endswith('.complete.json'):
                clock.now = obj.deadline + 1
            return digest
        with patch.object(e, 'save', side_effect=slow):
            obj.finish_report()
        self.assertFalse(obj.report['complete'])
        self.assertTrue(obj.claim.exists())

    def known_floor_wire(self, duplicate=False, timeout=False, prior_uncertain=False):
        obj, _, power, clock = self.experiment()
        events = []
        class WireSerial(Serial):
            def write(self, data):
                events.append(data)
                return super().write(data)
        class Physical(Client):
            def _command_once(self, text, expected, seconds):
                from serial_debug_test import encode_command
                self.serial.write(encode_command(text, True))
                if timeout:
                    raise TimeoutError('actual reply timeout')
                details = ('valid=1 readonly=1 voltage_mv=3900 temperature_dK=3000 op_status=0026'
                           if expected == 'gauge-charge' else 'value=1')
                line = 'DBG RESULT command=' + expected + ' status=PASS ' + details
                print(line)
                if duplicate:
                    print(line)
                return types.SimpleNamespace(status='PASS', details=details)
        client = Physical(clock)
        client.serial = WireSerial()
        call = e.Console(client, obj.out, clock)
        obj.call = call
        obj.apply_sent = obj.apply_confirmed = True
        obj.apply_start = clock.now
        obj.deadline, obj.ram_deadline = clock.now + 660, clock.now + 600
        call.deadline, call.check = obj.deadline, obj.before_write
        call.safety_hook = obj.safety
        call.failure_hook = lambda command, exc: power.stop.set()
        call.uncertain = prior_uncertain
        def on():
            events.append('INDEPENDENT_ON_DOUBLE_QUERY')
            power.on_known = True
        power.ensure_on = on
        return obj, call, power, events

    def test_real_Console_known_floor_ON_then_restore_actual_wire(self):
        from serial_debug_test import encode_command
        obj, call, power, events = self.known_floor_wire()
        with self.assertRaises(e.TypedKnownSafetyStop):
            call('gauge-charge')
        self.assertTrue(power.stop.is_set())
        self.assertFalse(call.uncertain)
        power.ensure_on()
        call('gauge-contrast restore CONFIRM')
        frame = encode_command('debug gauge-contrast restore CONFIRM', True)
        self.assertEqual(events.count(frame), 1)
        self.assertLess(events.index('INDEPENDENT_ON_DOUBLE_QUERY'), events.index(frame))
        self.assertEqual(obj.sent['gauge-contrast restore CONFIRM'], 1)

    def test_real_Console_timeout_duplicate_or_prior_unknown_still_forbids_restore_wire(self):
        from serial_debug_test import encode_command
        for kwargs in ({'timeout': True}, {'duplicate': True}, {'prior_uncertain': True}):
            _, call, power, events = self.known_floor_wire(**kwargs)
            with self.assertRaises((ValueError, TimeoutError)):
                call('gauge-charge')
            self.assertTrue(call.uncertain)
            power.ensure_on()
            with self.assertRaises(ValueError):
                call('gauge-contrast restore CONFIRM')
            self.assertNotIn(encode_command('debug gauge-contrast restore CONFIRM', True), events)

    def test_real_Console_known_temperature_vs_zero_invalid_fields(self):
        for voltage, temperature, known in ((4200, 3300, True), (0, 3000, False)):
            call, power = self.physical_console(f'valid=1 readonly=1 voltage_mv={voltage} temperature_dK={temperature} op_status=0026')
            with self.assertRaises(ValueError):
                call('gauge-charge')
            self.assertTrue(power.stop.is_set())
            self.assertEqual(call.uncertain, not known)

    def test_claim_unlink_late_makes_return_incomplete_without_rewriting_report(self):
        obj, _, _, clock = self.experiment()
        obj.report['complete'] = True
        unlink = Path.unlink
        def slow(path, *args, **kwargs):
            result = unlink(path, *args, **kwargs)
            if path == obj.claim:
                clock.now = obj.deadline + 1
            return result
        with patch.object(Path, 'unlink', new=slow):
            obj.finish_report()
        self.assertFalse(obj.report['complete'])
        self.assertTrue(json.loads((obj.out / 'report.json').read_bytes())['complete'])
        self.assertTrue((obj.out / 'report.json.failed.json').exists())
        self.assertTrue(any('claim unlink' in error for error in obj.report['errors']))

    def parent_evidence(self):
        path = self.out / 'accepted-report.json'
        d = dict(kind='GCT1_contrast_experiment', stage='on-smoke', arm='C', complete=True,
            original_verified=True, V1_ON_verified=True, settings_restored=True, source_crc=e.SOURCE,
            legacy_crc='430c5ed4', target131_verified=True, unknown_ack=False, errors=[], apply_start_monotonic=100.)
        sha = e.save(path, d)
        e.save(Path(str(path) + '.complete.json'), dict(complete=True, within_deadline=True, report_sha256=sha))
        return path, sha

    def test_missing_independent_parent_receipt_rejected_even_child_complete(self):
        path, sha = self.parent_evidence()
        with self.assertRaisesRegex(ValueError, 'root acceptance'):
            e.load_evidence(path, sha, stage='on-smoke', arm='C')

    def test_late_parent_receipt_rejected_even_child_complete(self):
        path, sha = self.parent_evidence()
        e.save(self.out / 'root-acceptance.json', dict(report_sha256=sha, child_exit_code=0,
                                                     observed_child_exit_monotonic=760.))
        with self.assertRaisesRegex(ValueError, 'parent exit/deadline'):
            e.load_evidence(path, sha, stage='on-smoke', arm='C')

    def test_parent_receipt_requires_typed_zero_finite_exit_and_exact_hash(self):
        path, sha = self.parent_evidence()
        receipt = self.out / 'root-acceptance.json'
        variants = [dict(report_sha256=sha, child_exit_code=True, observed_child_exit_monotonic=200.),
                    dict(report_sha256=sha, child_exit_code=1, observed_child_exit_monotonic=200.),
                    dict(report_sha256=sha, child_exit_code=0, observed_child_exit_monotonic=float('nan')),
                    dict(report_sha256=sha, child_exit_code=0, observed_child_exit_monotonic=99.),
                    dict(report_sha256='0' * 64, child_exit_code=0, observed_child_exit_monotonic=200.)]
        for data in variants:
            receipt.write_text(json.dumps(data))
            with self.assertRaisesRegex(ValueError, 'parent exit/deadline'):
                e.load_evidence(path, sha, stage='on-smoke', arm='C')
        receipt.write_text(json.dumps(dict(report_sha256=sha, child_exit_code=0, observed_child_exit_monotonic=200.)))
        self.assertTrue(e.load_evidence(path, sha, stage='on-smoke', arm='C'))

    def test_average_transient_pure_exact_boundaries(self):
        args = [0, -25, 5, 20, 100., 102., 112., 112.1]
        self.assertTrue(e.average_transient(*args))
        for average in (0, 20):
            changed = list(args)
            changed[2] = average
            self.assertTrue(e.average_transient(*changed))
        for index, value in ((0, 1), (1, 0), (1, 25), (2, 21), (3, 0),
                             (4, 97.), (4, 103.), (4, None), (5, float('nan')),
                             (6, 111.999), (6, 113.), (7, 118.), (7, 111.9)):
            changed = list(args)
            changed[index] = value
            with self.assertRaises(ValueError):
                e.average_transient(*changed)
        self.assertFalse(e.average_transient(3, -25, -1, None, None, None, None, None))
        with self.assertRaises(ValueError):
            e.average_transient(3, 0, -1, None, None, None, None, None)

    def transient_sample_harness(self):
        obj, _, power, clock = self.experiment()
        state = dict(schema='1', namespace='gcontrast', source_crc=e.SOURCE, factor='0', verified='0',
            capacity_calibrated='0', original3000_UNTRUSTED='1', legacy_Failed_preserved='1',
            never_publish_physical_units='1', deadline_seconds='720', host_deadline_seconds='660',
            third_value='0', unexpected_count='0', arm='C', phase='2', generation='3', epoch='3', images_capability_verified='1')
        values = {'voltage': 4200, 'instant': 20, 'average': 20}
        class Scripted(Client):
            def _command_once(self, text, expected, timeout):
                from serial_debug_test import encode_command
                self.serial.write(encode_command(text, True))
                clock.now += .01
                detail = {
                    'gauge-charge': dict(valid='1', readonly='1', voltage_mv=str(values['voltage']),
                        temperature_dK='3000', op_status='0026', current_ma=str(values['instant']), average_current_ma=str(values['average'])),
                    'gauge': dict(valid='1', capacity_valid='1', raw_cc_valid='1', design_mah='65', fcc_mah='65',
                        unit_factor='0', unit_verified='0', op_status='0026', raw_cc='100', rm_mah='65'),
                    'gauge-mode': dict(readonly='1', mode_valid='1'),
                    'power': dict(locked='1', phase='2', wifi_running='0', bt_connected='0', bt_advertising='0', cpu_mhz='160'),
                    'boot': dict(count='10'), 'ota-status': dict(state='2', busy='0', pending='0'),
                    'gauge-contrast': state,
                    'gauge-engine': dict(definition_match='1', device_be='0220', fw_be='0003', build_be='0004',
                        raw73='00' * 23, raw76='00' * 17, CEDVRemCap='65', SmoothRemCap='65')
                }[expected]
                details = ' '.join(key + '=' + value for key, value in detail.items())
                print('DBG RESULT command=' + expected + ' status=PASS ' + details)
                return types.SimpleNamespace(status='PASS', details=details)
        client = Scripted(clock)
        call = e.Console(client, obj.out, clock)
        call.safety_hook, call.failure_hook = obj.safety, lambda command, exc: power.stop.set()
        obj.call = call
        power.original = dict(current=500, unit=1, voltage=5000, on=1)
        power.off_intent = power.off_verified = None
        return obj, call, power, clock, values, state

    def test_real_Console_sample_first_mean_transient_retains_raw_RM_RawCC(self):
        obj, call, power, clock, values, _ = self.transient_sample_harness()
        on = obj.sample('on')
        self.assertEqual(on['charge_received_monotonic'], call.rows[0]['terminal_received_monotonic'])
        power.off_intent, power.off_verified, power.source, power.on_known = 102., 102.1, 'off', False
        values.update(instant=-25, average=5)
        clock.now = 112.1
        off = obj.sample('off')
        self.assertTrue(off['mean_filter_transient'])
        self.assertTrue(off['exclude_steady_average'])
        self.assertEqual(off['last_ON_witness']['charge_received_monotonic'], on['charge_received_monotonic'])
        self.assertEqual(off['gauge']['raw_cc'], '100')
        self.assertEqual(off['gauge']['rm_mah'], '65')
        clock.now = 142.1
        with self.assertRaisesRegex(ValueError, 'not first'):
            obj.sample('off')
        values['average'] = -1
        steady = obj.sample('off')
        self.assertFalse(steady['mean_filter_transient'])

    def test_sample_transient_rejects_stale_missing_source_epoch_and_instant_proof(self):
        for fault in ('missing_ON', 'stale_ON', 'epoch', 'source', 'instant'):
            obj, _, power, clock, values, state = self.transient_sample_harness()
            obj.sample('on')
            power.off_intent, power.off_verified, power.source, power.on_known = 102., 102.1, 'off', False
            values.update(instant=-25, average=5)
            clock.now = 112.1
            if fault == 'missing_ON':
                obj.last_on_sample = None
            elif fault == 'stale_ON':
                obj.last_on_sample['charge_received_monotonic'] = 97.
            elif fault == 'epoch':
                state['epoch'] = '4'
            elif fault == 'source':
                obj.last_on_sample['source_verified'] = False
            else:
                values['instant'] = 0
            with self.assertRaises(ValueError):
                obj.sample('off')

    def test_real_Console_floor_precedes_transient_exemption_and_any_audit_IO(self):
        obj, call, power, clock, values, _ = self.transient_sample_harness()
        obj.sample('on')
        power.off_intent, power.off_verified, power.source, power.on_known = 102., 102.1, 'off', False
        values.update(instant=-25, average=5, voltage=3900)
        clock.now = 112.1
        actual_open = Path.open
        def check_open(path, *args, **kwargs):
            self.assertTrue(power.stop.is_set())
            return actual_open(path, *args, **kwargs)
        with patch.object(Path, 'open', new=check_open):
            with self.assertRaises(e.TypedKnownSafetyStop):
                obj.sample('off')
        self.assertEqual(len(obj.report['samples']), 1)
        self.assertFalse(call.uncertain)

    def images_harness(self, outcomes, guard_overrides=None, readonly_outcomes=None):
        obj, _, power, clock = self.experiment()
        events, image_count = [], [0]
        readonly_count = {}
        status = dict(schema='1', namespace='gcontrast', source_crc=e.SOURCE, factor='0', verified='0',
            capacity_calibrated='0', original3000_UNTRUSTED='1', legacy_Failed_preserved='1',
            never_publish_physical_units='1', deadline_seconds='720', host_deadline_seconds='660',
            third_value='0', unexpected_count='0', arm='C', phase='255', generation='0', epoch='0',
            runtime_ready='1', protection='0', images_capability_verified='0', prepared_fresh='0', success='0')
        original_settings = {key: '1' for key in e.CONFIG_KEYS}
        original_settings['temporary'] = '0'
        settings = dict(original_settings)
        class Scripted(Client):
            def _command_once(self, text, expected, timeout):
                from serial_debug_test import encode_command
                self.serial.write(encode_command(text, True))
                command = text.removeprefix('debug ')
                events.append(command)
                clock.now += .01
                terminal = 'PASS'
                duplicate = False
                if command.startswith('gauge-contrast images '):
                    index = image_count[0]
                    image_count[0] += 1
                    outcome = outcomes[min(index, len(outcomes) - 1)]
                    if outcome == 'timeout':
                        raise TimeoutError('images ACK unknown')
                    detail = dict(status)
                    if outcome == 'pass':
                        detail.update(images_capability_verified='1', success='1')
                    else:
                        terminal = 'FAIL'
                        detail['reason'] = 'different_proof_failure' if outcome == 'unknown' else 'images_lease_runtime_length'
                        duplicate = outcome == 'duplicate'
                elif command == 'gauge-contrast status':
                    detail = dict(status, success='1')
                elif command == 'gauge-contrast prepare C CONFIRM':
                    status.update(phase='0', generation='1', epoch='1', prepared_fresh='1',
                                  images_capability_verified='1', success='1',
                                  prepare_source='currentboot_authenticated_FA_full131', prepare_fresh_dm131='1')
                    detail = dict(status)
                elif command == 'gauge-contrast apply CONFIRM_EXTERNAL_POWER':
                    status.update(phase='2', generation='3', epoch='3')
                    detail = dict(status)
                elif command == 'gauge-contrast restore CONFIRM':
                    status.update(phase='0', generation='5', epoch='5')
                    detail = dict(status)
                elif command == 'boot':
                    detail = {'count': '10'}
                elif command == 'ota-status':
                    detail = dict(state='2', busy='0', pending='0')
                elif command == 'power':
                    detail = dict(locked='1', phase='2', wifi_running='0', bt_connected='0', bt_advertising='0', cpu_mhz='160')
                elif command == 'gauge':
                    detail = dict(valid='1', capacity_valid='1', design_mah='3000', fcc_mah='3000',
                        unit_factor='0', unit_verified='0', op_status='0026')
                elif command == 'gauge-native-recovery evidence':
                    detail = {'evidence_crc': '430c5ed4'}
                elif command == 'gauge-charge':
                    detail = dict(valid='1', readonly='1', voltage_mv='4200', temperature_dK='3000',
                                  op_status='0026', current_ma='20', average_current_ma='20')
                elif command == 'settings get':
                    detail = dict(settings)
                elif command == 'settings restore':
                    settings.clear()
                    settings.update(original_settings)
                    detail = {'restored': '1'}
                elif command.startswith('settings set '):
                    _, _, key, value, _ = command.split()
                    settings.update(temporary='1')
                    settings[key] = value
                    detail = {'value': value}
                else:
                    detail = {'value': '1'}
                if command in (readonly_outcomes or {}):
                    index = readonly_count.get(command, 0)
                    readonly_count[command] = index + 1
                    choices = readonly_outcomes[command]
                    selected = choices[min(index, len(choices) - 1)]
                    if selected == 'timeout':
                        raise TimeoutError('readonly timeout')
                    if selected != 'pass':
                        terminal = 'FAIL'
                        detail = (dict(reason='ota_not_idle', readonly='1') if command == 'gauge-engine' else
                            dict(reason='blocked_storage_lease', ram_only='1', resolved_this_boot='0', source_reason='none',
                                 evidence_crc='00000000', proof_generation='0', failed_records_preserved='1', keys='0',
                                 parameter_writes='0', nvs_writes='0'))
                        duplicate = selected == 'duplicate'
                        if selected == 'unknown':
                            detail['reason'] = 'different_readonly_failure'
                detail.update((guard_overrides or {}).get(command, {}))
                details = ' '.join(key + '=' + value for key, value in detail.items())
                line = f'DBG RESULT command={expected} status={terminal} ' + details
                print(line)
                if duplicate:
                    print(line)
                return types.SimpleNamespace(status=terminal, details=details)
        client = Scripted(clock)
        call = e.Console(client, obj.out, clock)
        obj.call = call
        call.check, call.deadline = obj.before_write, obj.deadline
        call.prekey_failure_check = obj.known_images_failure
        call.failure_hook = lambda command, exc: power.stop.set()
        call.expected_boot = obj.args.expected_boot
        def wait(seconds):
            clock.now += seconds
            return power.stop.is_set()
        call.retry_wait = wait
        def ensure_on():
            events.append('INDEPENDENT_ON_DOUBLE_QUERY')
            power.on_known = True
        power.ensure_on = ensure_on
        return obj, call, power, events, image_count, original_settings

    def test_real_Console_images_known_FAIL_fresh_guards_PASS_only_gate_repeated(self):
        obj, call, power, events, count, _ = self.images_harness(['lease', 'pass'])
        with patch.object(power.stop, 'wait', return_value=False):
            result = obj.images_capability()
        self.assertEqual(result['images_capability_verified'], '1')
        self.assertEqual(count[0], 2)
        self.assertFalse(call.uncertain)
        self.assertFalse(power.stop.is_set())
        self.assertEqual(events, ['gauge-contrast images proof', 'boot', 'ota-status', 'power', 'gauge-contrast images proof'])
        self.assertEqual(obj.sent['gauge-contrast images proof'], 2)
        self.assertTrue(obj.no_key_request())

    def test_images_unknown_FAIL_timeout_duplicate_prior_uncertain_never_retry(self):
        for outcome in ('unknown', 'timeout', 'duplicate', 'prior_uncertain'):
            obj, call, power, events, count, _ = self.images_harness([outcome])
            if outcome == 'prior_uncertain':
                call.uncertain = True
            with patch.object(power.stop, 'wait', return_value=False):
                with self.assertRaises((ValueError, TimeoutError)):
                    obj.images_capability()
            self.assertEqual(count[0], 0 if outcome == 'prior_uncertain' else 1)
            self.assertTrue(call.uncertain)
            self.assertTrue(obj.no_key_request())

    def test_images_gate_capped_three_attempts_and_no_prepare_retry_ticket(self):
        obj, call, power, _, count, _ = self.images_harness(['lease'])
        with patch.object(power.stop, 'wait', return_value=False):
            with self.assertRaises(e.TypedKnownPrekeyFailure):
                obj.images_capability()
        self.assertEqual(count[0], 3)
        self.assertFalse(call.uncertain)
        self.assertIsNone(obj.images_retry_ticket)
        obj.before_write('gauge-contrast prepare C CONFIRM')
        with self.assertRaises(ValueError):
            obj.before_write('gauge-contrast prepare C CONFIRM')
        self.assertFalse(obj.known_images_failure('gauge-contrast images proof', {}))

    def test_images_retry_guard_wrong_boot_busy_or_nonquiet_never_resends(self):
        for overrides in ({'boot': {'count': '11'}}, {'ota-status': {'busy': '1'}}, {'power': {'phase': '1'}}):
            obj, _, power, _, count, _ = self.images_harness(['lease', 'pass'], overrides)
            with patch.object(power.stop, 'wait', return_value=False):
                with self.assertRaisesRegex(ValueError, 'retry guard failed'):
                    obj.images_capability()
            self.assertEqual(count[0], 1)
            self.assertIsNone(obj.images_retry_ticket)
            self.assertTrue(obj.no_key_request())

    def test_actual_Console_missing_cleanup_ON_standard3000_and_RAM_no_keys(self):
        obj, call, power, events, _, original = self.images_harness(['lease'])
        obj.legacy = {'evidence_crc': '430c5ed4'}
        def prepare():
            obj.settings = call('settings get')
            obj.settings_touched = True
            for command in ('settings set lock_wifi_minutes 60 600', 'settings set lock_ble_minutes 60 600',
                            'settings set smart_keepalive 0 600'):
                call(command)
            obj.images_capability()
        obj.prepare = prepare
        with patch.object(power.stop, 'wait', return_value=False), patch.object(e, 'legacy_check', side_effect=lambda d: d):
            report = obj.run()
        self.assertFalse(report['complete'])
        self.assertFalse(report['original_verified'])
        self.assertFalse(report['unknown_ack'])
        self.assertTrue(report['prekey_missing_standard_verified'])
        self.assertTrue(report['V1_ON_verified'])
        self.assertTrue(report['settings_restored'])
        self.assertLess(events.index('INDEPENDENT_ON_DOUBLE_QUERY'), events.index('settings restore'))
        self.assertFalse(any(command.startswith(('gauge-contrast prepare ', 'gauge-contrast apply ', 'gauge-contrast restore ')) for command in events))
        self.assertEqual(json.loads((obj.out / 'settings-after.json').read_bytes()), original)
        self.assertTrue((obj.out / 'prekey-Missing-cleanup.json').is_file())

    def test_real_Console_readonly_busy_to_PASS_exact_family_no_STOP_unknown(self):
        for command in ('gauge-native-recovery evidence', 'gauge-engine'):
            _, call, power, events, _, _ = self.images_harness(['pass'], readonly_outcomes={command: ['busy', 'pass']})
            result = call(command)
            self.assertTrue(result)
            self.assertEqual(events.count(command), 2)
            self.assertFalse(call.uncertain)
            self.assertFalse(power.stop.is_set())
            index = events.index(command)
            self.assertEqual(events[index + 1:index + 4], ['ota-status', 'boot', 'gauge-contrast status'])
            self.assertTrue(all(row['deadline'] == call.rows[0]['deadline'] for row in call.rows))

    def test_readonly_busy_whitelist_strict_no_parameter_command_or_extra_fields(self):
        native = dict(reason='blocked_storage_lease', ram_only='1', resolved_this_boot='0', source_reason='none',
            evidence_crc='00000000', proof_generation='0', failed_records_preserved='1', keys='0', parameter_writes='0', nvs_writes='0')
        self.assertTrue(e.readonly_busy('gauge-native-recovery evidence', native))
        self.assertTrue(e.readonly_busy('gauge-engine', dict(reason='ota_not_idle', readonly='1')))
        for command in ('gauge-contrast prepare C CONFIRM', 'gauge-contrast apply CONFIRM_EXTERNAL_POWER',
                        'gauge-contrast restore CONFIRM', 'gauge-contrast status', 'gauge-charge'):
            self.assertFalse(e.readonly_busy(command, native))
        for key in ('keys', 'parameter_writes', 'nvs_writes', 'failed_records_preserved'):
            changed = dict(native)
            changed[key] = '1' if native[key] == '0' else '0'
            self.assertFalse(e.readonly_busy('gauge-native-recovery evidence', changed))
        self.assertFalse(e.readonly_busy('gauge-native-recovery evidence', native | {'extra': '1'}))
        self.assertFalse(e.readonly_busy('gauge-engine', dict(reason='ota_not_idle', readonly='1', extra='1')))

    def test_readonly_timeout_duplicate_unknown_and_prior_uncertain_never_retry(self):
        command = 'gauge-native-recovery evidence'
        for failure in ('timeout', 'duplicate', 'unknown', 'prior'):
            _, call, _, events, _, _ = self.images_harness(['pass'], readonly_outcomes={command: ['busy' if failure == 'prior' else failure]})
            call.uncertain = failure == 'prior'
            with self.assertRaises((ValueError, TimeoutError)):
                call(command)
            self.assertEqual(events.count(command), 1)
            self.assertTrue(call.uncertain)

    def test_readonly_retry_caps_three_attempts_four_seconds_group_and_parent(self):
        command = 'gauge-engine'
        _, call, power, events, _, _ = self.images_harness(['pass'], readonly_outcomes={command: ['busy']})
        start = call.clock.now
        with self.assertRaises(e.TypedReadonlyBusy):
            call(command, timeout=90)
        self.assertEqual(events.count(command), 3)
        self.assertLess(call.clock.now - start, 4)
        self.assertFalse(call.uncertain)
        self.assertFalse(power.stop.is_set())
        for cap in ('group_deadline', 'deadline'):
            _, call, _, events, _, _ = self.images_harness(['pass'], readonly_outcomes={command: ['busy']})
            setattr(call, cap, call.clock.now + .9)
            with self.assertRaisesRegex(ValueError, 'contention budget'):
                call(command, timeout=90)
            self.assertEqual(events.count(command), 1)
            self.assertFalse(call.uncertain)
            self.assertTrue(all(row['deadline'] <= 100.9 for row in call.rows))

    def test_readonly_changed_SDK_boot_or_epoch_forbids_retry(self):
        command = 'gauge-engine'
        for change in ('SDK', 'boot', 'epoch'):
            _, call, _, events, _, _ = self.images_harness(['pass'], readonly_outcomes={command: ['busy', 'pass']})
            original = call.client._command_once
            def changed(text, expected, timeout):
                result = original(text, expected, timeout)
                # Modify subsequent GUARD context, not the failed command.
                if command in events:
                    if change == 'SDK' and text == 'debug ota-status':
                        result.details = 'state=2 busy=1 pending=0'
                    elif change == 'boot' and text == 'debug boot':
                        result.details = 'count=11'
                    elif change == 'epoch' and text == 'debug gauge-contrast status':
                        result.details = result.details.replace('epoch=0', 'epoch=1')
                return result
            # The terminal text must match the returned detail, so rewrite the
            # synthetic stdout in the shim as if the actual device emitted it.
            def shim(text, expected, timeout):
                capture = io.StringIO()
                with contextlib.redirect_stdout(capture):
                    result = changed(text, expected, timeout)
                print(f'DBG RESULT command={expected} status={result.status} ' + result.details)
                return result
            call.client._command_once = shim
            with self.assertRaisesRegex(ValueError, 'phase epoch changed'):
                call(command)
            self.assertEqual(events.count(command), 1)

    def test_real_prepare_receipt_precedes_busy_legacy_and_Original_RAM_cleanup(self):
        command = 'gauge-native-recovery evidence'
        obj, call, power, events, _, original_settings = self.images_harness(['pass'],
            readonly_outcomes={command: ['pass', 'busy', 'busy', 'busy', 'pass']})
        actual = call.client._command_once
        def checked(text, expected, timeout):
            if text == 'debug ' + command and call.gct_context and call.gct_context['phase'] == '0':
                self.assertTrue(obj.report.get('prepared'))
                self.assertTrue((obj.out / 'prepare-receipt.json').is_file())
            return actual(text, expected, timeout)
        call.client._command_once = checked
        with patch.object(e, 'legacy_check', side_effect=lambda d: d):
            report = obj.run()
        self.assertFalse(report['complete'])
        self.assertFalse(report['unknown_ack'])
        self.assertTrue(report['prepared']['prepared_fresh'] == '1')
        self.assertFalse(report['original_verified'])
        self.assertTrue(report['preapply_standard_safe'])
        self.assertTrue(report['settings_restored'])
        self.assertTrue((obj.out / 'preapply-Original-cleanup.json').is_file())
        self.assertEqual(events.count('gauge-contrast prepare C CONFIRM'), 1)
        self.assertFalse(any(command.startswith(('gauge-contrast apply ', 'gauge-contrast restore ')) for command in events))
        self.assertEqual(json.loads((obj.out / 'settings-after.json').read_bytes()), original_settings)

    def test_actual_Console_parameter_commands_never_retry_readonly_busy_lookalike(self):
        from serial_debug_test import encode_command
        for command in ('gauge-contrast prepare C CONFIRM', 'gauge-contrast apply CONFIRM_EXTERNAL_POWER',
                        'gauge-contrast restore CONFIRM'):
            obj, call, power, events, _, _ = self.images_harness(['pass'])
            obj.ram_deadline = call.clock.now + 600
            def failed(text, expected, timeout):
                call.client.serial.write(encode_command(text, True))
                events.append(text.removeprefix('debug '))
                detail = ('reason=blocked_storage_lease ram_only=1 resolved_this_boot=0 source_reason=none '
                    'evidence_crc=00000000 proof_generation=0 failed_records_preserved=1 keys=0 parameter_writes=0 nvs_writes=0')
                print(f'DBG RESULT command={expected} status=FAIL ' + detail)
                return types.SimpleNamespace(status='FAIL', details=detail)
            call.client._command_once = failed
            with self.assertRaises(ValueError):
                call(command)
            self.assertEqual(events, [command])
            self.assertTrue(call.uncertain)
            self.assertTrue(power.stop.is_set())

    def test_preapply_cleanup_negative_proof_never_restores_RAM_or_claims_Original(self):
        command = 'gauge-native-recovery evidence'
        for fault in ('epoch', 'boot', 'DC130', 'FCC130', 'security', 'readfail'):
            with self.subTest(fault=fault):
                obj, call, power, events, _, _ = self.images_harness(['pass'],
                    readonly_outcomes={command: ['pass', 'busy', 'busy', 'busy', 'pass']})
                cleanup = [False]
                actual_on = power.ensure_on
                def on():
                    actual_on()
                    cleanup[0] = True
                power.ensure_on = on
                actual_command = call.client._command_once
                def changed(text, expected, timeout):
                    with contextlib.redirect_stdout(io.StringIO()):
                        result = actual_command(text, expected, timeout)
                    if cleanup[0]:
                        if fault == 'epoch' and text == 'debug gauge-contrast status':
                            result.details = result.details.replace('epoch=1', 'epoch=2')
                        elif fault == 'boot' and text == 'debug boot':
                            result.details = 'count=11'
                        elif text == 'debug gauge':
                            if fault == 'DC130':
                                result.details = result.details.replace('design_mah=3000', 'design_mah=130')
                            elif fault == 'FCC130':
                                result.details = result.details.replace('fcc_mah=3000', 'fcc_mah=130')
                            elif fault == 'security':
                                result.details = result.details.replace('op_status=0026', 'op_status=0000')
                            elif fault == 'readfail':
                                raise TimeoutError('cleanup standard reply unknown')
                    print(f'DBG RESULT command={expected} status={result.status} ' + result.details)
                    return result
                call.client._command_once = changed
                with patch.object(e, 'legacy_check', side_effect=lambda d: d):
                    report = obj.run()
                self.assertTrue(report['prepared'])
                self.assertFalse(report['original_verified'])
                self.assertFalse(report['preapply_standard_safe'])
                self.assertFalse(report['settings_restored'])
                self.assertFalse(report['complete'])
                self.assertNotIn('settings restore', events)
                self.assertFalse((obj.out / 'preapply-Original-cleanup.json').exists())

    def test_prepare_provenance_both_modes_and_inconsistent_or_missing_claims(self):
        base = dict(prepared_fresh='1', phase='0', source_crc=e.SOURCE, protection='0', images_capability_verified='1')
        for mode, fresh in (('durable_original131', '0'), ('currentboot_authenticated_FA_full131', '1')):
            result = e.prepare_provenance(base | dict(prepare_source=mode, prepare_fresh_dm131=fresh))
            self.assertEqual(result['prepare_fresh_dm131'], fresh)
            self.assertEqual(result['durable_source_plus_fresh_standard_NOT_freshDM'], mode == 'durable_original131')
            self.assertTrue(result['apply_preCFG_full131_required'])
        good = base | dict(prepare_source='durable_original131', prepare_fresh_dm131='0')
        for override in (dict(prepare_source='unknown'), dict(prepare_fresh_dm131='1'), dict(prepare_fresh_dm131=0),
                         dict(prepared_fresh='0'), dict(protection='1'), dict(phase='2'), dict(images_capability_verified='0')):
            with self.assertRaises(ValueError):
                e.prepare_provenance(good | override)
        with self.assertRaises(ValueError):
            e.prepare_provenance(base)
        with self.assertRaises(ValueError):
            e.prepare_provenance(base | dict(prepare_source='currentboot_authenticated_FA_full131', prepare_fresh_dm131='0'))

    def test_real_Console_both_prepare_modes_record_distinct_cleanup_provenance(self):
        command = 'gauge-native-recovery evidence'
        for mode, fresh in (('durable_original131', '0'), ('currentboot_authenticated_FA_full131', '1')):
            with self.subTest(mode=mode):
                obj, _, _, events, _, _ = self.images_harness(['pass'], guard_overrides={
                    'gauge-contrast prepare C CONFIRM': dict(prepare_source=mode, prepare_fresh_dm131=fresh)},
                    readonly_outcomes={command: ['pass', 'busy', 'busy', 'busy', 'pass']})
                with patch.object(e, 'legacy_check', side_effect=lambda d: d):
                    report = obj.run()
                self.assertFalse(report['complete'])
                self.assertFalse(report['original_verified'])
                self.assertTrue(report['preapply_standard_safe'])
                self.assertTrue(report['settings_restored'])
                self.assertEqual(report['prepare_source'], mode)
                self.assertEqual(report['prepare_fresh_dm131'], fresh)
                receipt = json.loads((obj.out / 'prepare-receipt.json').read_bytes())
                cleanup = json.loads((obj.out / 'preapply-Original-cleanup.json').read_bytes())
                self.assertEqual(receipt['provenance']['prepare_source'], mode)
                self.assertEqual(cleanup['provenance']['prepare_fresh_dm131'], fresh)
                self.assertTrue(cleanup['full131_current_read_not_claimed'])
                self.assertEqual(events.count('gauge-contrast prepare C CONFIRM'), 1)
                self.assertFalse(any(event.startswith(('gauge-contrast apply ', 'gauge-contrast restore ')) for event in events))

    def bind_production_experiment(self, original, call, power):
        out = original.out / 'actual-constructor-binding'
        out.mkdir()
        return e.Experiment(call, power, original.watcher, out, original.args, [], [], 'proof', original.clock)

    def test_real_Console_Experiment_normal_supply_stop_cleanup_readonly_retries_PASS(self):
        command = 'gauge-native-recovery evidence'
        old, call, power, events, _, _ = self.images_harness(['pass'],
            readonly_outcomes={command: ['pass', 'pass', 'busy', 'pass']})
        old.args.stage = 'on-smoke'
        obj = self.bind_production_experiment(old, call, power)
        # Full source/target codec has its own tests; this is the actual UART
        # transaction/recovery wiring, with synthetic apply/restore terminals.
        obj.pages = lambda *args: None
        obj.sample = lambda *args: None
        on = power.ensure_on
        def expected_on():
            power.stop.set()  # Production ensure_on does this on normal early ON.
            on()
        power.ensure_on = expected_on
        with patch.object(power.stop, 'wait', side_effect=AssertionError('supply-stop must not cancel readonly cleanup')), \
             patch.object(obj.watcher.sticky, 'wait', wraps=obj.watcher.sticky.wait) as operator_wait, \
             patch.object(e, 'legacy_check', side_effect=lambda d: d):
            report = obj.run()
        self.assertTrue(power.stop.is_set())
        self.assertFalse(obj.watcher.sticky.is_set())
        self.assertTrue(report['complete'])
        self.assertFalse(report['unknown_ack'])
        self.assertTrue(report['V1_ON_verified'])
        self.assertTrue(report['settings_restored'])
        self.assertEqual(events.count(command), 4)
        operator_wait.assert_called_once_with(1)
        self.assertEqual(events.count('gauge-contrast apply CONFIRM_EXTERNAL_POWER'), 1)
        self.assertEqual(events.count('gauge-contrast restore CONFIRM'), 1)
        busy = next(row for row in call.rows if row.get('typed_readonly_busy'))
        cleanup_rows = [row for row in call.rows if row['monotonic'] >= busy['monotonic']][:5]
        self.assertTrue(all(row['deadline'] == busy['deadline'] for row in cleanup_rows))

    def test_real_Console_Experiment_operator_latch_aborts_readonly_backoff_without_resend(self):
        command = 'gauge-native-recovery evidence'
        old, call, power, events, _, _ = self.images_harness(['pass'], readonly_outcomes={command: ['busy', 'pass']})
        obj = self.bind_production_experiment(old, call, power)
        power.stop.set()
        obj.watcher.sticky.set()
        with self.assertRaisesRegex(ValueError, 'STOP during readonly'):
            call(command)
        self.assertEqual(events.count(command), 1)
        self.assertFalse(call.uncertain)
        self.assertFalse(any(event.startswith(('gauge-contrast apply ', 'gauge-contrast restore ')) for event in events))

    def test_real_Console_Experiment_supply_stopped_unknown_timeout_dupe_never_retry(self):
        command = 'gauge-native-recovery evidence'
        for failure in ('timeout', 'duplicate', 'unknown'):
            old, call, power, events, _, _ = self.images_harness(['pass'], readonly_outcomes={command: [failure, 'pass']})
            obj = self.bind_production_experiment(old, call, power)
            power.stop.set()
            with patch.object(obj.watcher.sticky, 'wait', side_effect=AssertionError('unknown must never retry')):
                with self.assertRaises((ValueError, TimeoutError)):
                    call(command)
            self.assertTrue(call.uncertain)
            self.assertEqual(events.count(command), 1)


if __name__ == '__main__':
    unittest.main()
