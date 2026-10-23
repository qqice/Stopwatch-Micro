"""Bounded GCT1 C/B experiment, NOT an SOC repair or physical calibration.

Default dry opens no UART, V1 DLL, or keys. Execute only after independent review.
Unknown UART ACK permanently closes mutations; firmware rollback is observed RO.
No board reset, OTA, direct I2C, old GSJ mutations, or NVS erasure is available.
"""
import argparse
import base64
import contextlib
import hashlib
import io
import json
import math
import os
from pathlib import Path
import re
import sys
import threading
import time
import zlib

ROOT = Path(__file__).resolve().parents[1]
SOURCE = '70a767be'
TARGET = {'C': '91336fc0', 'B': '675dd812'}
SOURCE_PATH = ROOT / '.artifacts/private/mosaico/gauge95r12-current-gain-probe-actual-002/actual-public131-backup.json'
SOURCE_SHA = '18ddb3caa5171a550b377e76bfee1eb5b21c6190719c733d36d6776c68f94a3c'
DLL_SHA = '100bc284bc998252d0be924629f1c16e0264122549df10697357444296d244e7'
CONFIG_KEYS = ('charge_timeout', 'battery_timeout', 'charge_brightness', 'battery_brightness',
               'lock_brightness', 'burn_in', 'lock_wifi_minutes', 'lock_ble_minutes', 'smart_keepalive')
HOST_SECONDS, SETUP_SECONDS, RAM_SECONDS, RECOVERY_RESERVE, GROUP_SECONDS = 660, 300, 600, 100, 6


def require(condition, reason):
    if not condition:
        raise ValueError(reason)


class TypedKnownSafetyStop(ValueError):
    """A unique complete PASS contained plausible, confirmed unsafe telemetry.

    This requests independent ON but is NOT a lost/ambiguous UART ACK. Only
    Console's post-terminal safety callback may preserve known wire state.
    """


class TypedKnownPrekeyFailure(ValueError):
    """Unique explicit images FAIL, independently classified before any keys."""
    def __init__(self, detail):
        self.detail = dict(detail)
        super().__init__('known prekey images rejection: ' + detail['reason'])


class TypedReadonlyBusy(ValueError):
    """Known no-write contention terminal; not a transport/ACK uncertainty."""
    def __init__(self, command, detail):
        self.command, self.detail = command, dict(detail)
        super().__init__('known readonly contention: ' + command)


def readonly_busy(command, detail):
    # Firmware sources: mosaico_native_recovery.inc native_report/evidence
    # lease gate; serial_debug.cpp gauge-engine's pre-read OTA/storage lease.
    if command == 'gauge-engine':
        return detail == dict(reason='ota_not_idle', readonly='1')
    if command != 'gauge-native-recovery evidence':
        return False
    fixed = dict(reason='blocked_storage_lease', ram_only='1', failed_records_preserved='1',
                 keys='0', parameter_writes='0', nvs_writes='0')
    return (set(detail) == set(fixed) | {'resolved_this_boot', 'source_reason', 'evidence_crc', 'proof_generation'} and
        all(detail.get(k) == v for k, v in fixed.items()) and detail.get('resolved_this_boot') in ('0', '1') and
        bool(re.fullmatch('[A-Za-z0-9_-]{1,100}', detail.get('source_reason', ''))) and
        bool(re.fullmatch('[0-9a-f]{8}', detail.get('evidence_crc', ''))) and
        bool(re.fullmatch('0|[1-9][0-9]{0,9}', detail.get('proof_generation', ''))) and
        int(detail['proof_generation']) <= 0xffffffff)


def parse(text):
    pairs = [token.split('=', 1) for token in text.split()]
    require(all(len(p) == 2 for p in pairs) and len({p[0] for p in pairs}) == len(pairs), 'malformed/duplicate fields')
    return dict(pairs)


def save(path, value):
    raw = (json.dumps(value, indent=2, allow_nan=False) + '\n').encode()
    with Path(path).open('xb') as stream:
        stream.write(raw)
        stream.flush()
        os.fsync(stream.fileno())
    require(Path(path).read_bytes() == raw, 'evidence readback mismatch')
    sha = hashlib.sha256(raw).hexdigest()
    with Path(str(path) + '.sha256').open('x') as stream:
        stream.write(sha + '\n')
        stream.flush()
        os.fsync(stream.fileno())
    return sha


def canonical_crc(fields):
    return f'{zlib.crc32(b"".join(int(f["address"],16).to_bytes(2,"little") + bytes([f["width"]]) + bytes.fromhex(f["raw"]) for f in fields)) & 0xffffffff:08x}'


def source_fields():
    raw = SOURCE_PATH.read_bytes()
    require(hashlib.sha256(raw).hexdigest() == SOURCE_SHA, 'immutable original131 SHA drift')
    document = json.loads(raw)
    require(document['complete'] is True, 'incomplete source')
    fields = [dict(index=i, address=f['address'].lower(), width=f['width'], raw=f['raw'].lower())
              for i, f in enumerate(document['fields'])]
    require(len(fields) == 131 and canonical_crc(fields) == SOURCE, 'source131 CRC')
    require(all(f['width'] in (1, 2, 4) and len(bytes.fromhex(f['raw'])) == 4 and
                not any(bytes.fromhex(f['raw'])[f['width']:]) for f in fields), 'source shape/padding')
    return fields


def target_fields(source, arm):
    require(arm in TARGET, 'arm')
    fields = [dict(f) for f in source]
    for field in fields:
        address = int(field['address'], 16)
        if address in (0x929b, 0x929d, 0x929f):
            require(field['width'] == 2, 'owned width')
            value = (0x112a if arm == 'C' else 0x102a) if address == 0x929b else 65
            field['raw'] = value.to_bytes(2, 'big').hex() + '0000'
    require(canonical_crc(fields) == TARGET[arm], 'closed-owned3 target CRC')
    return fields


def load_proof(path):
    import mosaico_gauge_contrast_capability as cap
    proof = Path(path).read_text(encoding='ascii').strip()
    require(0 < len(proof) <= 1400, 'proof wire length')
    pairs = json.loads(base64.b64decode(proof, validate=True))
    require(isinstance(pairs, list) and len(pairs) == 2 and
            all(isinstance(p, dict) and set(p) == {'image', 'signature'} for p in pairs), 'new GCT1 wire pair required')
    key = cap.public_key(ROOT / 'main/ota/ota_public_key.h')
    require({p['image']['version'] for p in pairs} == cap.VERSIONS, 'exact distinct reviewed GCT1 firmware pair required')
    for pair in pairs:
        m = pair['image']
        endorsement = {k: m[k] for k in ('version', 'size', 'sha256')}
        endorsement.update(contract=cap.DIGEST, signature=pair['signature'])
        cap.verify_endorsement(m, endorsement, key)
    return proof, pairs


def load_evidence(path, sha, *, stage, arm, image_bindings=None):
    require(re.fullmatch('[0-9a-f]{64}', sha or ''), 'explicit accepted evidence SHA256 required')
    raw = Path(path).read_bytes()
    require(hashlib.sha256(raw).hexdigest() == sha, 'accepted evidence hash mismatch')
    require(not (Path(path).parent / 'UNFINISHED').exists() and not Path(str(path) + '.failed.json').exists(),
            'unfinished/failed final persistence forbids evidence reuse')
    receipt_path = Path(str(path) + '.complete.json')
    require(receipt_path.is_file(), 'completed persistence receipt required')
    receipt = json.loads(receipt_path.read_bytes())
    require(receipt.get('report_sha256') == sha and receipt.get('within_deadline') is True and
            receipt.get('complete') is True, 'completed persistence receipt required')
    d = json.loads(raw)
    parent_path = Path(path).parent / 'root-acceptance.json'
    require(parent_path.is_file(), 'independent root acceptance required')
    parent = json.loads(parent_path.read_bytes())
    start, observed = d.get('apply_start_monotonic'), parent.get('observed_child_exit_monotonic')
    require(parent.get('report_sha256') == sha and type(parent.get('child_exit_code')) is int and
            parent['child_exit_code'] == 0 and type(start) in (int, float) and math.isfinite(start) and
            type(observed) in (int, float) and math.isfinite(observed) and
            start <= observed < start + HOST_SECONDS, 'independent parent exit/deadline acceptance failed')
    require(d.get('kind') == 'GCT1_contrast_experiment' and d.get('stage') == stage and d.get('arm') == arm and
            d.get('complete') is True and d.get('original_verified') is True and d.get('V1_ON_verified') is True and
            d.get('settings_restored') is True and d.get('source_crc') == SOURCE and d.get('legacy_crc') == '430c5ed4' and
            d.get('target131_verified') is True and d.get('unknown_ack') is False and not d.get('errors'), 'evidence admission failed')
    if image_bindings is not None:
        require(d.get('image_bindings') == image_bindings, 'evidence signed-BIN bindings differ')
    if stage == 'observe':
        require(d.get('analysis', {}).get('outcome') == 'platform' and d['analysis'].get('confirmed_after_three_counts') is True,
                'B forbidden until valid C platform')
    return d


def topology(ports):
    require(not any(p.vid == 0x303a for p in ports), 'board USB visible: sole V1 not established')
    j = [p for p in ports if (p.vid, p.pid, p.serial_number) == (0x1366, 0x0105, '000024060501')]
    v = [p for p in ports if (p.vid, p.pid, p.serial_number) == (0x10c4, 0xea60, '0001')]
    require(len(j) == len(v) == 1 and v[0].device.upper() == 'COM21' and
            sum((p.vid, p.pid) == (0x1366, 0x0105) for p in ports) == 1 and
            sum((p.vid, p.pid) == (0x10c4, 0xea60) for p in ports) == 1 and
            sum(p.device.upper() == j[0].device.upper() for p in ports) == 1, 'ambiguous/wrong UART or V1')
    return j[0].device


def signed_mod_delta(after, before):
    require(type(after) is int and type(before) is int and 0 <= after <= 65535 and 0 <= before <= 65535, 'RawCC uint16')
    delta = (after - before) & 65535
    require(delta != 32768, 'ambiguous half-range RawCC')
    return delta - 65536 if delta > 32768 else delta


def average_transient(index, instant, average, pre_average, pre_received, off_intent, start, received):
    """First-only bounded mean-filter carryover; never a load/count exemption."""
    require(type(instant) is int and type(average) is int and instant < 0, 'instantaneous OFF current must be negative')
    if average < 0:
        return False
    require(index == 0 and type(pre_average) is int and pre_average > 0 and 0 <= average <= pre_average,
            'nonnegative average not first bounded filter transient')
    require(all(type(x) in (int, float) and math.isfinite(x) for x in (pre_received, off_intent, start, received)) and
            0 <= off_intent - pre_received < 5 and off_intent + 10 <= start < off_intent + 11 and
            start <= received < off_intent + 16, 'first filter transient lacks fresh preOFF/timer evidence')
    return True


def off_budget(now, total_deadline, ram_deadline):
    require(all(math.isfinite(x) for x in (now, total_deadline, ram_deadline)), 'nonfinite deadline')
    # Includes OFF setter/bootstrap overhead and independent ON verification in
    # the same reserve; actual OFF transmission rechecks this exact envelope.
    hold = min(480, math.floor(min(total_deadline, ram_deadline) - now - RECOVERY_RESERVE - 1))
    require(hold >= 450, 'MINIMUM_OBSERVATION_UNAVAILABLE: cannot cover OFF450 plus recovery100; do not OFF')
    return hold


def analyse(samples):
    if not samples:
        return dict(outcome='inconclusive', reason='no_OFF_samples', confirmed_after_three_counts=False)
    first = samples[0]
    relative, crossed = [], None
    for i, row in enumerate(samples):
        cc = signed_mod_delta(int(row['gauge']['raw_cc']), int(first['gauge']['raw_cc']))
        rm = int(row['gauge']['rm_mah']) - int(first['gauge']['rm_mah'])
        relative.append(dict(index=i, signed_mod_RawCC=cc, delta_RM=rm, residual_RM_plus_RawCC=rm + cc))
        if crossed is None and cc >= 3:
            crossed = i
    confirmations = [] if crossed is None else [i for i in range(crossed + 1, len(samples))
        if samples[i].get('endpoint_check') in (1, 2)]
    confirmed = (len(confirmations) == 2 and samples[confirmations[0]].get('endpoint_check') == 1 and
        samples[confirmations[1]].get('endpoint_check') == 2 and
        samples[confirmations[0]]['monotonic'] - samples[crossed]['monotonic'] >= 10 and
        samples[confirmations[1]]['monotonic'] - samples[confirmations[0]]['monotonic'] >= 5 and
        all(relative[i]['signed_mod_RawCC'] >= 3 for i in confirmations))
    supported = ('CEDVRemCap', 'SmoothRemCap')
    supports = {key: [row.get('engine', {}).get(key) for row in samples] for key in supported}
    no_guess = all(all(v is not None for v in values) for values in supports.values())
    plateau = confirmed and no_guess and all(int(row['gauge']['rm_mah']) == int(first['gauge']['rm_mah']) for row in samples) and all(
        len(set(values)) == 1 for values in supports.values())
    tracking = confirmed and all(abs(v['residual_RM_plus_RawCC']) <= 1 for v in relative)
    outcome = 'platform' if plateau else 'tracking' if tracking else 'nontracking' if confirmed and no_guess else 'inconclusive'
    return dict(outcome=outcome, confirmed_after_three_counts=confirmed, crossed_index=crossed,
                relative=relative, supported= supports, no_0073_offset_guess=True,
                nominal_units_only=True, physical_calibration=False,
                protocol='three_counts_then_10s_and_two_5s_endpoint_checks')


READ_COMMANDS = {'ping', 'status', 'gauge-charge', 'gauge', 'gauge-mode', 'gauge-engine', 'power', 'boot',
                 'ota-status', 'gauge-native-recovery evidence', 'gauge-contrast status', 'settings get',
                 'touch-sleep status', 'standby-sleep status'}
SETTINGS_COMMANDS = {'settings set lock_wifi_minutes 60 600', 'settings set lock_ble_minutes 60 600',
                     'settings set smart_keepalive 0 600', 'settings restore', 'ui quota', 'display-lock'}


def mutation(command):
    return command in SETTINGS_COMMANDS or command.startswith(('gauge-contrast images ', 'gauge-contrast prepare ')) or command in {
        'gauge-contrast apply CONFIRM_EXTERNAL_POWER', 'gauge-contrast restore CONFIRM'}


def allowed(command):
    return command in READ_COMMANDS or command in SETTINGS_COMMANDS or bool(re.fullmatch(
        r'gauge-contrast (?:images [A-Za-z0-9+/=]{1,1400}|prepare [CB] CONFIRM|apply CONFIRM_EXTERNAL_POWER|restore CONFIRM|observed-page [0-4])', command))


class MutationWriteFence:
    """Check after wake preamble, immediately at actual framed UART write."""
    def __init__(self, source):
        object.__setattr__(self, 'source', source)
        object.__setattr__(self, 'expected', None)
        object.__setattr__(self, 'check', None)
        object.__setattr__(self, 'attempted', False)

    def __setattr__(self, name, value):
        if name in ('source', 'expected', 'check', 'attempted'):
            object.__setattr__(self, name, value)
        else:
            setattr(self.source, name, value)

    def __getattr__(self, name):
        return getattr(self.source, name)

    def write(self, data):
        if self.expected is not None and data != b'U' * 32 + b'\n':
            require(data == self.expected and self.check is not None, 'unexpected/unbound mutation frame')
            self.check()
            self.attempted = True  # BEFORE potentially side-effecting serial write.
        return self.source.write(data)


class ReadDeadlineSerial:
    def __init__(self, source, deadline, clock=time):
        self.source, self.deadline, self.clock = source, deadline, clock

    def __getattr__(self, name):
        return getattr(self.source, name)

    def remaining(self):
        value = self.deadline - self.clock.monotonic()
        if value <= 0:
            raise TimeoutError('absolute UART envelope exhausted')
        return value

    def _io(self, name, timeout_name, *args):
        left = self.remaining()
        old = getattr(self.source, timeout_name)
        setattr(self.source, timeout_name, left if old is None else min(old, left))
        try:
            self.remaining()
            value = getattr(self.source, name)(*args)
            self.remaining()
            return value
        finally:
            setattr(self.source, timeout_name, old)

    def write(self, *args):
        return self._io('write', 'write_timeout', *args)

    def readline(self, *args):
        return self._io('readline', 'timeout', *args)

    def flush(self):
        self.remaining()
        value = self.source.flush()
        self.remaining()
        return value


class Console:
    def __init__(self, client, out, clock=time):
        self.client, self.out, self.clock = client, out, clock
        self.fence = MutationWriteFence(client.serial)
        client.serial = self.fence
        self.rows, self.uncertain, self.check, self.group_deadline, self.deadline = [], False, None, None, None
        self.safety_hook = self.failure_hook = None
        self.prekey_failure_check = None
        self.audit_errors = []
        self.gct_context, self.boot_context, self.expected_boot = None, None, None
        self.retry_wait = time.sleep

    @contextlib.contextmanager
    def group(self):
        require(self.group_deadline is None, 'nested UART group')
        self.group_deadline = self.clock.monotonic() + GROUP_SECONDS
        try:
            yield
        finally:
            self.group_deadline = None

    def __call__(self, command, timeout=4, statuses=('PASS',)):
        if command not in ('gauge-native-recovery evidence', 'gauge-engine') or statuses != ('PASS',) or self.uncertain:
            return self._once(command, timeout, statuses)
        require(math.isfinite(timeout) and timeout > 0, 'invalid readonly retry timeout')
        end = self.clock.monotonic() + min(timeout, 4)
        for cap in (self.group_deadline, self.deadline):
            if cap is not None:
                end = min(end, cap)
        # Reuse a prior complete terminal context, never manufacture one from a
        # failed read. Only the initial pre-prepare evidence needs this RO probe.
        if self.gct_context is None:
            self._once('gauge-contrast status', absolute_end=end)
        if self.boot_context is None:
            self._once('boot', absolute_end=end)
        require(self.gct_context is not None and self.boot_context is not None,
                'readonly retry requires complete known boot/GCT context')
        baseline, boot = dict(self.gct_context), self.expected_boot or self.boot_context
        for attempt in range(3):
            try:
                return self._once(command, timeout, statuses, absolute_end=end)
            except TypedReadonlyBusy:
                require(not self.uncertain, 'readonly audit/ACK uncertainty: never retry')
                if attempt == 2:
                    raise
                require(self.clock.monotonic() + 1 < end, 'readonly total contention budget exhausted')
                require(not self.retry_wait(1), 'STOP during readonly contention backoff')
                ota = self._once('ota-status', absolute_end=end)
                current_boot = self._once('boot', absolute_end=end)
                self._once('gauge-contrast status', absolute_end=end)
                require(all(ota.get(k) == v for k, v in dict(state='2', busy='0', pending='0').items()) and
                    current_boot.get('count') == str(boot) and self.gct_context == baseline and
                    self.clock.monotonic() < end, 'readonly retry SDK/boot/GCT phase epoch changed')
        raise AssertionError('unreachable')

    def _once(self, command, timeout=4, statuses=('PASS',), absolute_end=None):
        from serial_debug_test import encode_command, RESULT_RE
        require(allowed(command) and math.isfinite(timeout) and timeout > 0, 'command/timeout not allowed')
        end = self.clock.monotonic() + timeout
        # Apply's global660 is armed at its actual wire fence, not at ACK. Its
        # wake preamble must still reach the fence BEFORE setup300 expires.
        # RAM-only setup cleanup remains bounded per call after setup timeout.
        new_apply = command == 'gauge-contrast apply CONFIRM_EXTERNAL_POWER'
        setup_cleanup = command == 'settings restore' and getattr(self, 'setup_cleanup', False)
        if new_apply:
            require(self.deadline is not None and self.clock.monotonic() < self.deadline, 'setup deadline before apply call')
        for limit in (self.group_deadline, None if new_apply or setup_cleanup else self.deadline, absolute_end):
            if limit is not None:
                end = min(end, limit)
        original = self.client.serial
        bounded = ReadDeadlineSerial(original, end, self.clock)
        row = dict(command=command, monotonic=self.clock.monotonic(), deadline=end)
        capture, result, primary, confirmed_terminal = io.StringIO(), None, None, False
        try:
            if mutation(command):
                require(not self.uncertain and self.check is not None, 'unknown ACK permanently forbids mutations')
                self.fence.expected = encode_command('debug ' + command, self.client.uart)
                self.fence.check = lambda: self.check(command)
                self.fence.attempted = False
            self.client.serial = bounded
            with contextlib.redirect_stdout(capture):
                bounded.remaining()
                if self.client.wake_preamble:
                    self.client._send_preamble()
                result = self.client._command_once('debug ' + command, command.split()[0], bounded.remaining())
                row['terminal_received_monotonic'] = self.clock.monotonic()  # BEFORE audit IO, not call-return time.
                bounded.remaining()
            terminals = []
            for line in capture.getvalue().splitlines():
                match = RESULT_RE.search(line)
                if match:
                    terminal = (match[1], match[2], match[3] or '')
                    if terminal == ('parse', 'FAIL', 'reason=uart_crc_frame_required no_changes=1'):
                        continue
                    terminals.append(terminal)
            require(terminals == [(command.split()[0], result.status, result.details)], 'residual/duplicate/unexpected terminal')
            detail = parse(result.details)
            confirmed_terminal = result.status in ('PASS', 'FAIL')
            row.update(status=result.status, details=result.details, parsed=detail)
            if result.status == 'PASS':
                if command == 'boot' and re.fullmatch('[1-9][0-9]*', detail.get('count', '')):
                    self.boot_context = detail['count']
                if command.startswith('gauge-contrast ') and detail.get('namespace') == 'gcontrast' and all(
                        key in detail for key in ('phase', 'generation', 'epoch', 'arm', 'source_crc')):
                    self.gct_context = {key: detail[key] for key in ('phase', 'generation', 'epoch', 'arm', 'source_crc')}
            if result.status == 'FAIL' and readonly_busy(command, detail):
                raise TypedReadonlyBusy(command, detail)
            if (result.status == 'FAIL' and self.prekey_failure_check is not None and
                    self.prekey_failure_check(command, detail)):
                raise TypedKnownPrekeyFailure(detail)
            require(result.status in statuses, 'explicit terminal ' + result.status + ': ' + result.details)
            # Synchronous physical safety callback occurs BEFORE any log open,
            # write, flush or fsync, including this call's finally audit path.
            if self.safety_hook is not None:
                self.safety_hook(command, row['parsed'])
            return row['parsed']
        except BaseException as exc:
            primary = exc
            known_prekey = isinstance(exc, TypedKnownPrekeyFailure) and confirmed_terminal
            known_readonly = isinstance(exc, TypedReadonlyBusy) and confirmed_terminal
            if self.failure_hook is not None and not (known_prekey or known_readonly):
                self.failure_hook(command, exc)  # Event.set only; no file/native IO.
            # A rejected preamble/wire fence is not evidence that a mutation was
            # sent. A sent mutation (including known FAIL) is never retried.
            known_safety = (isinstance(exc, TypedKnownSafetyStop) and confirmed_terminal and command == 'gauge-charge')
            self.uncertain |= not (known_safety or known_prekey or known_readonly) and (not mutation(command) or self.fence.attempted)
            row['typed_known_safety_stop'] = known_safety
            row['typed_known_prekey_failure'] = known_prekey
            row['typed_readonly_busy'] = known_readonly
            row['error'] = repr(exc)
            raise
        finally:
            self.client.serial = original
            self.fence.expected = self.fence.check = None
            row.update(raw_response=capture.getvalue(), completed_monotonic=self.clock.monotonic())
            self.rows.append(row)
            try:
                with (self.out / 'console.jsonl').open('a', encoding='utf8') as stream:
                    stream.write(json.dumps(row, allow_nan=False) + '\n')
                    stream.flush()
                    os.fsync(stream.fileno())
            except BaseException as audit:
                self.audit_errors.append(repr(audit))
                self.uncertain = True
                if self.failure_hook is not None:
                    self.failure_hook(command, audit)
                if primary is None:
                    raise
                primary.add_note('audit also failed: ' + repr(audit))
            if self.clock.monotonic() >= end:
                self.uncertain = True
                fault = TimeoutError('UART deadline includes audit IO')
                if self.failure_hook is not None:
                    self.failure_hook(command, fault)
                if primary is None:
                    raise fault
                primary.add_note(str(fault))


class StopWatcher:
    """Only watcher performs STOP-file I/O. Controller predicate is Event.is_set."""
    def __init__(self, path, independent_stop):
        self.path, self.independent_stop = path, independent_stop
        self.sticky, self.done = threading.Event(), threading.Event()
        self.error = None
        self.thread = threading.Thread(target=self.watch, name='GCT1-sticky-STOP', daemon=False)

    def watch(self):
        try:
            while not self.done.is_set():
                if self.path.exists():
                    self.sticky.set()
                    self.independent_stop.set()
                self.done.wait(.1)
        except BaseException as exc:
            self.error = repr(exc)
            self.sticky.set()
            self.independent_stop.set()

    def start(self):
        require(not self.path.exists(), 'STOP already exists')
        self.thread.start()

    def close(self):
        self.done.set()
        self.thread.join()


class Power:
    def __init__(self, out, ports, stop, clock=time):
        import iotpower_output_control as api
        self.api, self.out, self.ports, self.stop, self.clock = api, out, ports, stop, clock
        self.original = {'current': 500, 'unit': 1, 'voltage': 5000, 'on': 1}
        self.on_known, self.source, self.off_intent, self.off_verified = False, 'unknown', None, None
        self.thread, self.monitor, self.done = None, None, threading.Event()
        self.footer, self.error, self.monitor_error = {}, None, None
        self.error_kind = self.error_message = None
        self.stop_owned = False
        self.cycle_fault = None
        self.hold, self.limit, self.counter = 0, None, 0

    def backend(self):
        owner, api = self, self.api
        class Guarded(api.OutputBackend):
            def set_output(self, original, on):
                require(original == owner.original, 'V1 limits drift')
                if not on:
                    require(not owner.stop.is_set() and owner.limit is not None and
                            owner.clock.monotonic() + owner.hold + RECOVERY_RESERVE < owner.limit,
                            'late OFF cannot preserve recovery reserve')
                return super().set_output(original, on)

            def read(self):
                kind, value = super().read()
                if kind == 4:
                    raw = api.checked_state(value)
                    require(all(raw[k] == owner.original[k] for k in ('current', 'unit', 'voltage')), 'V1500/unit1/5000 required')
                return kind, value
        return Guarded(ROOT / '.artifacts/iotpower/native/iot_parser.dll', DLL_SHA)

    def snapshot(self):
        self.counter += 1
        result = self.api.run(self.backend(), 'COM21', self.ports, self.out / f'v1-ON-{self.counter}.jsonl', off_seconds=0, timeout=3)
        require(result['snapshot'] == self.original, 'V1 initial double-query ON/limits mismatch')
        self.source, self.on_known = 'on', True
        return result

    def monitor_log(self, path):
        try:
            while not path.exists() and not self.done.wait(.05):
                pass
            if not path.exists():
                return
            with path.open(encoding='utf8') as stream:
                pending = ''
                while True:
                    chunk = stream.readline()
                    if not chunk:
                        if self.done.is_set():
                            require(not pending, 'partial V1 footer')
                            break
                        self.done.wait(.05)
                        continue
                    pending += chunk
                    if not pending.endswith('\n'):
                        continue
                    event = json.loads(pending)
                    pending = ''
                    if event.get('event') == 'set_raw':
                        self.source, self.on_known = 'unknown', False
                        if event.get('raw', {}).get('on') == 0:
                            self.off_intent = event['monotonic']
                    elif event.get('event') == 'verified':
                        raw = event['raw']
                        require(all(raw[k] == self.original[k] for k in ('current', 'unit', 'voltage')), 'V1 event limits drift')
                        self.source = 'on' if raw['on'] else 'off'
                        self.on_known = bool(raw['on'])
                        if not raw['on']:
                            self.off_verified = event['monotonic']
                    elif event.get('event') == 'finish':
                        self.footer = event
        except BaseException as exc:
            self.monitor_error = repr(exc)
            self.stop.set()

    def start_off(self, hold, limit):
        require(self.on_known and self.thread is None, 'OFF once only with independently known ON')
        self.hold, self.limit = hold, limit
        require(0 < hold <= 480 and self.clock.monotonic() + hold + RECOVERY_RESERVE + 1 < limit, 'OFF entry budget')
        path = self.out / 'v1-OFF.jsonl'
        def requested():
            # No file/native IO. Ownership is recorded only when THIS run's
            # controller actually consumes its own sticky Event predicate.
            stopped = self.stop.is_set()
            if stopped:
                self.stop_owned = True
            return stopped
        def worker():
            try:
                self.api.run(self.backend(), 'COM21', self.ports, path, off_seconds=hold,
                             confirm_single_supply=True, timeout=3, stop_requested=requested)
            except BaseException as exc:
                self.error = repr(exc)
                self.error_kind, self.error_message = type(exc).__name__, str(exc)
            finally:
                self.done.set()  # api.run independently attempts ON in its own finally.
        self.thread = threading.Thread(target=worker, name='GCT1-V1-independent-finally-ON', daemon=False)
        self.monitor = threading.Thread(target=self.monitor_log, args=(path,), name='GCT1-V1-evidence-monitor', daemon=False)
        self.monitor.start()
        self.thread.start()

    def ensure_on(self):
        self.stop.set()
        if self.thread is not None:
            # Never terminate/unload an OFF thread. No gauge keys while it lives.
            self.thread.join(timeout=35)
            require(not self.thread.is_alive(), 'V1 thread alive: do NOT kill; no gauge keys, manual ON recovery required')
            self.monitor.join(timeout=2)
            require(not self.monitor.is_alive() and self.monitor_error is None, 'V1 monitor/footer unavailable')
            require(self.footer.get('restored_on_verified') is True and self.footer.get('com_lock_retained') is False and
                    self.footer.get('original') == self.original, 'V1 ON unknown; no gauge keys')
            error = self.footer.get('error')
            expected_stop = 'RuntimeError: external stop requested during off interval'
            natural = 'error' in self.footer and self.footer.get('complete') is True and error is None and self.error is None
            controlled = (getattr(self, 'stop_owned', False) and self.footer.get('complete') is False and
                self.error is not None and getattr(self, 'error_kind', None) == 'RuntimeError' and
                getattr(self, 'error_message', None) == error == expected_stop)
            if not (natural or controlled):
                self.cycle_fault = self.error or repr(dict(footer_complete=self.footer.get('complete'), footer_error=error))
        self.snapshot()  # api.run performs two independent raw snapshots.


def sealed(d):
    op = int(d['op_status'], 16)
    return ((op >> 1) & 3) == 3 and not (op & 0x401) and bool(op & 0x20)


def status_check(d, arm=None, phase=None):
    expected = dict(schema='1', namespace='gcontrast', source_crc=SOURCE, factor='0', verified='0',
                    capacity_calibrated='0', original3000_UNTRUSTED='1', legacy_Failed_preserved='1',
                    never_publish_physical_units='1', deadline_seconds='720', host_deadline_seconds='660',
                    third_value='0', unexpected_count='0')
    if arm is not None:
        expected['arm'] = arm
    if phase is not None:
        expected['phase'] = str(phase)
    require(all(d.get(k) == v for k, v in expected.items()), 'GCT1 contract/phase/ownership mismatch')
    return d


def prepare_provenance(detail):
    # prepared_fresh is ONLY the120s admission lease, not DM read freshness.
    modes = {'durable_original131': '0', 'currentboot_authenticated_FA_full131': '1'}
    source, fresh = detail.get('prepare_source'), detail.get('prepare_fresh_dm131')
    require(source in modes and fresh == modes[source] and detail.get('prepared_fresh') == '1' and
            detail.get('source_crc') == SOURCE and detail.get('phase') == '0' and detail.get('protection') == '0' and
            detail.get('images_capability_verified') == '1', 'unknown/inconsistent prepare source/fresh-DM provenance')
    return dict(prepare_source=source, prepare_fresh_dm131=fresh,
        durable_source_plus_fresh_standard_NOT_freshDM=source == 'durable_original131',
        apply_preCFG_full131_required=True)


def legacy_check(d):
    raw = bytearray()
    for key, size in (('reload_v1', 28), ('fcc_boot_v1', 48), ('fcc_fix_v1', 48)):
        require(d.get(key + '_byteLen') == str(size) and re.fullmatch('[0-9a-fA-F]{' + str(size * 2) + '}', d.get(key + '_raw', '')), 'Failed record shape')
        raw += size.to_bytes(4, 'little') + bytes.fromhex(d[key + '_raw'])
    require(f'{zlib.crc32(raw) & 0xffffffff:08x}' == d.get('evidence_crc') == '430c5ed4', 'historical Failed changed')
    return d


class Experiment:
    def __init__(self, call, power, watcher, out, args, source, target, proof, clock=time):
        self.call, self.power, self.watcher, self.out, self.args = call, power, watcher, out, args
        self.source, self.target, self.proof, self.clock = source, target, proof, clock
        self.started = clock.monotonic()
        # Exclusive unfinished claim survives every failed report/receipt write.
        # No potentially successful report can be consumed while this exists.
        self.claim = out / 'UNFINISHED'
        with self.claim.open('x') as stream:
            stream.write('GCT1 run has not completed final persistence\n')
            stream.flush()
            os.fsync(stream.fileno())
        self.setup_deadline = self.started + SETUP_SECONDS
        self.deadline, self.ram_deadline = self.setup_deadline, None
        self.sent, self.settings, self.legacy = {}, None, None
        self.rebase_used = self.rebase_in_progress = False
        self.apply_start = None
        self.last_on_sample = None
        self.images_retry_ticket = None
        self.apply_sent = self.apply_confirmed = self.settings_touched = False
        self.report = dict(kind='GCT1_contrast_experiment', stage=args.stage, arm=args.arm, source_crc=SOURCE,
                           complete=False, original_verified=False, target131_verified=False, V1_ON_verified=False,
                           preapply_standard_safe=False,
                           settings_restored=False, unknown_ack=False, legacy_crc='430c5ed4', errors=[], samples=[],
                           started_unix=clock.time(), SOC_repair=False, physical_calibration=False,
                           host_deadline_seconds=660, firmware_deadline_seconds=720, no_renewal=True)
        call.check, call.deadline = self.before_write, self.deadline
        call.safety_hook = self.safety
        call.failure_hook = lambda command, exc: self.power.stop.set()
        call.prekey_failure_check = self.known_images_failure
        call.expected_boot = args.expected_boot
        # Supply termination is expected at early ON/floor/normal cleanup. Only
        # the independent operator/error latch cancels a bounded RO backoff.
        call.retry_wait = lambda seconds: self.watcher.sticky.wait(seconds)

    def no_key_request(self):
        return not self.apply_sent and not any(command.startswith(('gauge-contrast prepare ',
            'gauge-contrast apply ', 'gauge-contrast restore ')) for command in self.sent)

    def known_images_failure(self, command, detail):
        # This combined firmware reason does NOT prove lease contention. It is
        # retryable only after fresh independent guards exclude length/runtime
        # and observable busy/protection faults, before prepare/keys whatsoever.
        if (command != 'gauge-contrast images ' + self.proof or not self.no_key_request() or
                not 0 < len(self.proof) <= 1400 or detail.get('reason') != 'images_lease_runtime_length'):
            return False
        try:
            status_check(detail)
            require(detail.get('success') == '0' and detail.get('runtime_ready') == '1' and
                    detail.get('protection') == '0' and detail.get('images_capability_verified') == '0', 'known prekey shape')
            require((detail.get('phase') == '255' and detail.get('generation') == detail.get('epoch') == '0') or
                    (detail.get('phase') == '0' and int(detail['generation']) > 0 and int(detail['epoch']) > 0), 'prekey phase')
        except (ValueError, KeyError, TypeError):
            return False
        return True

    def images_capability(self):
        command = 'gauge-contrast images ' + self.proof
        for attempt in range(3):
            require(self.no_key_request() and not self.call.uncertain and not self.watcher.sticky.is_set() and
                    not self.power.stop.is_set() and self.clock.monotonic() < self.setup_deadline, 'images before-keys retry admission')
            try:
                result = self.call(command, timeout=90)
                require(result.get('images_capability_verified') == '1', 'two actual SDK_VALID slot BINs not verified')
                return result
            except TypedKnownPrekeyFailure as exc:
                self.report.setdefault('images_known_prekey_failures', []).append(dict(attempt=attempt + 1, detail=exc.detail))
                require(not self.call.uncertain, 'images audit/ACK uncertainty: never retry')
                if attempt == 2:
                    raise
                # One/two second backoff; only this exact gate can be resent.
                require(not self.power.stop.wait(attempt + 1), 'STOP during images prekey backoff')
                started = self.clock.monotonic()
                with self.call.group():
                    boot, ota, power = self.call('boot'), self.call('ota-status'), self.call('power')
                    require(boot.get('count') == str(self.args.expected_boot) and
                        all(ota.get(k) == v for k, v in dict(state='2', busy='0', pending='0').items()) and
                        all(power.get(k) == v for k, v in dict(locked='1', phase='2', wifi_running='0', bt_connected='0', bt_advertising='0', cpu_mhz='160').items()) and
                        self.clock.monotonic() < started + GROUP_SECONDS and 0 < len(self.proof) <= 1400,
                        'fresh same-boot idle/quiet images retry guard failed')
                self.images_retry_ticket = dict(command=command, expires=min(self.setup_deadline, self.clock.monotonic() + 2))

    def safety(self, command, detail):
        if command != 'gauge-charge':
            return
        try:
            voltage = int(detail['voltage_mv'])
            temperature = int(detail['temperature_dK'])
            require(detail.get('valid') == detail.get('readonly') == '1' and sealed(detail) and
                    2000 <= voltage <= 5000 and 2000 <= temperature <= 4000,
                    'invalid/implausible physical safety fields')
            if voltage <= 3900:
                raise TypedKnownSafetyStop('3900mV floor: known complete PASS; independent ON then restore')
            if not 2832 <= temperature <= 3131:
                raise TypedKnownSafetyStop('confirmed physical temperature safety stop')
            require(voltage <= 4350, 'physical overvoltage envelope')
        except BaseException:
            self.power.stop.set()  # FIRST action, before Console audit IO.
            raise

    def remaining(self):
        return min(self.deadline, self.ram_deadline or self.deadline) - self.clock.monotonic()

    def before_write(self, command):
        repeat = (self.rebase_in_progress and command.startswith('settings set ') and self.sent.get(command) == 1)
        image_retry = (self.images_retry_ticket is not None and self.images_retry_ticket['command'] == command and
            self.sent.get(command) in (1, 2) and self.no_key_request() and
            self.clock.monotonic() < self.images_retry_ticket['expires'])
        setup_cleanup = command == 'settings restore' and not self.apply_sent
        require((command not in self.sent or repeat or image_retry) and (self.clock.monotonic() < self.deadline or setup_cleanup), 'duplicate/late mutation')
        restoring = command in ('gauge-contrast restore CONFIRM', 'settings restore')
        if not restoring:
            require(not self.watcher.sticky.is_set() and not self.power.stop.is_set(), 'sticky STOP forbids forward mutation')
        else:
            require(self.power.on_known and (self.power.thread is None or not self.power.thread.is_alive()), 'independent ON before restore')
        if command.startswith('settings set ') and self.ram_deadline is None:
            self.ram_deadline = self.clock.monotonic() + RAM_SECONDS
            self.call.deadline = min(self.deadline, self.ram_deadline)
        if command == 'gauge-contrast apply CONFIRM_EXTERNAL_POWER':
            require(self.apply_start is None and self.clock.monotonic() < self.setup_deadline and
                    (self.ram_deadline or 0) - self.clock.monotonic() > 190, 'setup expired/duplicate apply/no RAM restore reserve')
            # Total experiment lease starts at the conservative actual SEND,
            # never at its ACK. Only RAM alignment below may change separately.
            self.apply_start = self.clock.monotonic()
            self.deadline = self.apply_start + HOST_SECONDS
            self.call.deadline = min(self.deadline, self.ram_deadline)
            self.apply_sent = True
            self.report.update(deadline_armed=True, setup_elapsed=self.apply_start - self.started)
        if command == 'gauge-contrast restore CONFIRM':
            require(self.remaining() > 75, 'restore+full131 closure reserve unavailable')
        self.sent[command] = self.sent.get(command, 0) + 1
        if image_retry:
            self.images_retry_ticket = None  # Actual-wire one-use guard; not an apply/prepare retry waiver.
        self.report.setdefault('mutation_wire', []).append(dict(command=command, monotonic=self.clock.monotonic()))

    def state(self, phase=None):
        return status_check(self.call('gauge-contrast status'), self.args.arm if phase is not None else None, phase)

    def pages(self, expected, status, cache_phase, name):
        started = self.clock.monotonic()
        with self.call.group():
            result = self._pages(expected, status, cache_phase, name)
            require(self.clock.monotonic() < started + GROUP_SECONDS, 'whole pages group including persistence exceeded6s')
            return result

    def _pages(self, expected, status, cache_phase, name):
        import mosaico_factory_scale_observed_pages as codec
        before = self.state(int(status['phase']))
        pages = [self.call(f'gauge-contrast observed-page {i}', timeout=2) for i in range(5)]
        result = codec.decode(pages, expected)
        after = self.state(int(status['phase']))
        require(all(before[k] == after[k] == status[k] for k in ('phase', 'generation', 'epoch', 'arm')), 'cache bracket changed')
        context = result['context']
        require(int(context['generation']) == int(status['generation']) - 1 and int(context['epoch']) == int(status['epoch']) - 1 and
                int(context['cache_phase']) == cache_phase and result['observed_crc'] == canonical_crc(expected), 'stale cache not tied to terminal phase')
        save(self.out / name, dict(pages=pages, decoded=result, before=before, after=after, cached_not_fresh_sealed_DM=True))
        return result

    def sample(self, source='on', endpoint_check=None):
        started = self.clock.monotonic()
        with self.call.group():
            require(self.power.source == source and not self.watcher.sticky.is_set(), 'sample source/STOP')
            # Voltage/floor is the very FIRST I2C-producing command, never last.
            p = self.call('gauge-charge')
            voltage = int(p['voltage_mv'])
            if voltage <= 3900:
                self.power.stop.set()
                raise ValueError('3900mV floor: independentStop then V1 ON before gauge restore')
            require(p.get('valid') == p.get('readonly') == '1' and voltage <= 4350 and
                    2832 <= int(p['temperature_dK']) <= 3131 and sealed(p), 'physical envelope/SEALED/CFG0')
            charge_row = self.call.rows[-1] if getattr(self.call, 'rows', None) else {}
            charge_received = charge_row.get('terminal_received_monotonic')
            require(charge_row.get('command') == 'gauge-charge' and charge_row.get('status') == 'PASS' and
                    charge_row.get('parsed') == p and type(charge_received) in (int, float) and
                    math.isfinite(charge_received) and started <= charge_received <= self.clock.monotonic(),
                    'actual gauge-charge terminal receipt witness missing')
            g = self.call('gauge')
            require(g.get('valid') == g.get('capacity_valid') == g.get('raw_cc_valid') == '1' and
                    g.get('design_mah') == g.get('fcc_mah') == '65' and g.get('unit_factor') == g.get('unit_verified') == '0' and sealed(g), 'nominal65 gauge/units/security')
            mode = self.call('gauge-mode')
            require(mode.get('readonly') == mode.get('mode_valid') == '1', 'gauge-mode invalid')
            power = self.call('power')
            require(all(power.get(k) == v for k, v in dict(locked='1', phase='2', wifi_running='0', bt_connected='0', bt_advertising='0', cpu_mhz='160').items()), 'quiet160 load changed')
            boot, ota, status = self.call('boot'), self.call('ota-status'), self.state(2)
            require(boot.get('count') == str(self.args.expected_boot) and all(ota.get(k) == v for k, v in dict(state='2', busy='0', pending='0').items()) and
                    status.get('images_capability_verified') == '1', 'boot/SDK_VALID/images changed')
            engine = self.call('gauge-engine')
            require(engine.get('definition_match') == '1' and engine.get('device_be') == '0220' and
                    engine.get('fw_be') == '0003' and engine.get('build_be') == '0004' and
                    re.fullmatch('[0-9a-f]{46}', engine.get('raw73', '')) and re.fullmatch('[0-9a-f]{34}', engine.get('raw76', '')), 'raw engine evidence unavailable')
            require(self.power.source == source and self.clock.monotonic() < started + GROUP_SECONDS, 'mixed/late sample group')
            row = dict(monotonic=self.clock.monotonic(), unix=self.clock.time(), started_monotonic=started,
                       deadline_monotonic=started + GROUP_SECONDS, physical=p, gauge=g, mode=mode, power=power,
                       boot=boot, ota=ota, contrast=status, engine=engine, power_source=source,
                       power_source_begin=source, power_source_end=self.power.source,
                       charge_received_monotonic=charge_received,
                       v1_original=dict(getattr(self.power, 'original', {})),
                       source_verified=(self.power.on_known if source == 'on' else getattr(self.power, 'off_verified', None) is not None))
            if endpoint_check is not None:
                row['endpoint_check'] = endpoint_check
            if source == 'off':
                pre = self.last_on_sample
                index = sum(s['power_source'] == 'off' for s in self.report['samples'])
                instant, average = int(p['current_ma']), int(p['average_current_ma'])
                transient = average_transient(index, instant, average,
                    int(pre['physical']['average_current_ma']) if pre is not None else None,
                    pre.get('charge_received_monotonic') if pre is not None else None,
                    getattr(self.power, 'off_intent', None), started, charge_received)
                if transient:
                    intent, verified = self.power.off_intent, self.power.off_verified
                    require(pre is not None and pre.get('power_source_begin') == pre.get('power_source_end') == 'on' and
                        pre.get('source_verified') is True and row['source_verified'] is True and
                        row['power_source_begin'] == row['power_source_end'] == 'off' and
                        pre['boot']['count'] == boot['count'] == str(self.args.expected_boot) and
                        all(pre['contrast'][k] == status[k] for k in ('phase', 'generation', 'epoch', 'arm')) and
                        pre['v1_original'] == row['v1_original'] == dict(current=500, unit=1, voltage=5000, on=1) and
                        type(verified) in (int, float) and math.isfinite(verified) and intent <= verified <= started and
                        self.clock.monotonic() < intent + 16,
                        'first average transient lacks known raw sources/same boot epoch/timely completion')
                    row['last_ON_witness'] = dict(charge_received_monotonic=pre['charge_received_monotonic'],
                        physical=pre['physical'], boot=pre['boot'], contrast=pre['contrast'],
                        power_source_begin=pre['power_source_begin'], power_source_end=pre['power_source_end'],
                        v1_original=pre['v1_original'], source_verified=pre['source_verified'])
                    row['actual_OFF_intent_monotonic'], row['actual_OFF_verified_monotonic'] = intent, verified
                row['mean_filter_transient'] = row['exclude_steady_average'] = transient
            require(self.clock.monotonic() < started + GROUP_SECONDS, 'sample acceptance deadline including transient proof')
            if source == 'on':
                self.last_on_sample = row
            self.report['samples'].append(row)
        return row

    def prepare(self):
        self.power.snapshot()
        require(self.call('boot')['count'] == str(self.args.expected_boot), 'boot pin')
        ota = self.call('ota-status')
        require(all(ota.get(k) == v for k, v in dict(state='2', busy='0', pending='0').items()), 'SDK not VALID idle')
        self.legacy = legacy_check(self.call('gauge-native-recovery evidence'))
        save(self.out / 'historical-Failed-before.json', self.legacy)
        self.settings = self.call('settings get')
        require(self.settings.get('temporary') == '0' and all(k in self.settings for k in CONFIG_KEYS), 'original RAM settings')
        save(self.out / 'settings-before.json', self.settings)
        for command in ('settings set lock_wifi_minutes 60 600', 'settings set lock_ble_minutes 60 600', 'settings set smart_keepalive 0 600'):
            self.settings_touched = True
            self.call(command)
        self.call('ui quota')
        self.call('display-lock')
        until = min(self.clock.monotonic() + 150, self.setup_deadline - 5)
        while True:
            d = self.call('power')
            if all(d.get(k) == v for k, v in dict(locked='1', phase='2', wifi_running='0', bt_connected='0', bt_advertising='0', cpu_mhz='160').items()):
                break
            require(self.clock.monotonic() < until and not self.watcher.sticky.is_set(), 'quiet160 admission timeout/STOP')
            self.power.stop.wait(min(5, max(0, until - self.clock.monotonic())))
        p = self.call('gauge-charge')
        require(p.get('valid') == p.get('readonly') == '1' and 4150 <= int(p['voltage_mv']) <= 4350 and sealed(p), 'entry requires >=4150mV sealed')
        self.images_capability()
        prepared = self.call(f'gauge-contrast prepare {self.args.arm} CONFIRM', timeout=180)
        status_check(prepared, self.args.arm, 0)
        provenance = prepare_provenance(prepared)
        self.report['prepared'] = prepared
        self.report.update(provenance)
        save(self.out / 'prepare-receipt.json', dict(prepared=prepared, expected_boot=self.args.expected_boot,
            source_crc=SOURCE, immutable_source_sha256=SOURCE_SHA,
            terminal=self.call.rows[-1] if getattr(self.call, 'rows', None) else None,
            full131_prepared_ACK_not_fresh_sealed_DM=True, apply_not_sent=True, provenance=provenance))
        require(legacy_check(self.call('gauge-native-recovery evidence')) == self.legacy, 'Failed changed during prepare')

    def rebase_quiet_once(self):
        require(not self.rebase_used and not self.rebase_in_progress and self.apply_confirmed and
                self.report['target131_verified'] and not self.call.uncertain and not self.watcher.sticky.is_set() and
                not self.power.stop.is_set() and self.power.thread is None and self.power.on_known and self.remaining() > 100,
                'RAM alignment is once-only after verified apply and before any OFF')
        before = self.call('settings get')
        require(before.get('temporary') == '1' and all(before.get(k) == v for k, v in
            dict(lock_wifi_minutes='60', lock_ble_minutes='60', smart_keepalive='0').items()), 'quiet signature before alignment')
        old_deadline = self.deadline
        self.rebase_used = self.rebase_in_progress = True
        self.ram_deadline = self.clock.monotonic() + RAM_SECONDS
        self.call.deadline = min(self.deadline, self.ram_deadline)
        try:
            for command in ('settings set lock_wifi_minutes 60 600', 'settings set lock_ble_minutes 60 600', 'settings set smart_keepalive 0 600'):
                self.call(command)
        finally:
            self.rebase_in_progress = False
        after = self.call('settings get')
        require(all(before[k] == after[k] for k in CONFIG_KEYS) and after.get('temporary') == '1' and
                self.deadline == old_deadline, 'alignment changed settings/global660')
        self.power.snapshot()
        status = self.state(2)
        require(status.get('images_capability_verified') == '1', 'actual two SDK_VALID images after alignment')
        self.pages(self.target, status, 1, 'target131-after-RAM-alignment.json')
        row = self.sample('on')
        require(int(row['physical']['voltage_mv']) >= 4150, 'post-alignment OFF entry >=4150mV')
        self.report['RAM_alignment'] = dict(count=1, identical_settings=True, host_deadline_unchanged=True,
            firmware720_unchanged=True, before=before, after=after, ram_deadline=self.ram_deadline)

    def observe(self):
        self.rebase_quiet_once()
        hold = off_budget(self.clock.monotonic(), self.deadline, self.ram_deadline)
        self.report['planned_OFF_seconds'] = hold
        self.report['shortened_from_480_for_budget'] = hold < 480
        limit = min(self.deadline, self.ram_deadline)
        self.power.start_off(hold, limit)
        next_sample = None
        while not self.power.done.wait(.1):
            require(not self.watcher.sticky.is_set() and self.power.monitor_error is None, 'STOP or V1 log error')
            require(self.clock.monotonic() + RECOVERY_RESERVE < limit, 'early ON recovery reserve')
            if self.power.off_verified is None or self.power.off_intent is None:
                continue
            if next_sample is None:
                next_sample = self.power.off_intent + 10
            if self.clock.monotonic() < next_sample:
                continue
            require(self.clock.monotonic() < self.power.off_intent + hold - 10, 'sample too near ON transition')
            self.sample('off')
            off = [row for row in self.report['samples'] if row['power_source'] == 'off']
            result = analyse(off)
            if result['crossed_index'] is not None:
                # No added load. Once >=3 RawCC counts are observed, allow10s
                # latency then two endpoint reads separated by >=5s. Never
                # extend OFF or either global lease to obtain these reads.
                for endpoint, delay in ((1, 10), (2, 5)):
                    if self.clock.monotonic() + delay + GROUP_SECONDS >= self.power.off_intent + hold - 3:
                        self.report['endpoint_confirmation_unavailable'] = True
                        break  # Inconclusive, never extend OFF or lower count threshold.
                    require(not self.power.stop.wait(delay), 'STOP during endpoint allowance')
                    self.sample('off', endpoint_check=endpoint)
                self.report['early_stop_after_count_confirmation'] = True
                break
            next_sample += 30
        self.power.ensure_on()
        require(self.power.cycle_fault is None, 'V1 cycle fault (ON verified): ' + str(self.power.cycle_fault))
        self.report['analysis'] = analyse([row for row in self.report['samples'] if row['power_source'] == 'off'])

    def run(self):
        try:
            self.prepare()
            ack = self.call('gauge-contrast apply CONFIRM_EXTERNAL_POWER', timeout=90)
            status_check(ack, self.args.arm, 2)
            self.apply_confirmed = True
            self.pages(self.target, ack, 1, 'target131-pages.json')
            self.report['target131_verified'] = True
            self.sample('on')
            if self.args.stage == 'observe':
                self.observe()
        except BaseException as exc:
            self.report['errors'].append(repr(exc))
            self.power.stop.set()  # independent power finally before any recovery keys
        finally:
            try:
                self.power.ensure_on()
                self.report['V1_ON_verified'] = True
                if self.apply_sent:
                    if self.call.uncertain:
                        # No retry of ANY mutating command after unknown ACK.
                        # Firmware owns deadline rollback. Host is bounded to660s.
                        while self.clock.monotonic() + 8 < self.deadline:
                            current = self.state()
                            if current.get('phase') == '0':
                                break
                            threading.Event().wait(min(10, max(0, self.deadline - self.clock.monotonic() - 8)))
                    else:
                        current = self.state()
                        if current.get('phase') == '2' and self.apply_confirmed:
                            self.call('gauge-contrast restore CONFIRM', timeout=60)
                    current = self.state(0)
                    self.pages(self.source, current, 3, 'restored-source131-pages.json')
                    g = self.call('gauge')
                    require(g.get('design_mah') == g.get('fcc_mah') == '3000' and sealed(g), 'Original3000 final standard')
                    self.report['original_verified'] = True
                else:
                    # Setup changed no IC parameters. Do not turn a durable
                    # prepare source into a current full-DM/restored131 claim.
                    # RAM cleanup has a separate, fully validated standard-safe
                    # eligibility below; successful trial full131 gates differ.
                    self.report['original_verified'] = False
                    self.report['preapply_standard_safe'] = False
                    if self.clock.monotonic() < self.setup_deadline:
                        current = self.state()
                        if current.get('phase') == '255' and self.no_key_request() and not self.call.uncertain:
                            require(current.get('generation') == current.get('epoch') == '0' and
                                current.get('protection') == '0', 'Missing GCT must be unprotected/no intent')
                            gauge = self.call('gauge')
                            require(gauge.get('valid') == gauge.get('capacity_valid') == '1' and
                                gauge.get('design_mah') == gauge.get('fcc_mah') == '3000' and
                                gauge.get('unit_factor') == gauge.get('unit_verified') == '0' and sealed(gauge),
                                'prekey Missing requires standard Original3000/SEALED')
                            self.report['original_verified'] = False  # Standard registers are NOT a full131 proof.
                            self.report['prekey_missing_standard_verified'] = True
                            save(self.out / 'prekey-Missing-cleanup.json', dict(gct=current, gauge=gauge,
                                V1_ON_verified=True, no_key_request=True, no_full131_claim=True))
                        else:
                            status_check(current, self.args.arm, 0)
                            prepared = self.report.get('prepared')
                            if prepared and not self.call.uncertain:
                                provenance = prepare_provenance(prepared)
                                require(all(current.get(k) == prepared.get(k) for k in
                                    ('phase', 'generation', 'epoch', 'arm', 'source_crc')) and current.get('protection') == '0' and
                                    self.call('boot').get('count') == str(self.args.expected_boot), 'prepared Original cleanup proof changed')
                                gauge = self.call('gauge')
                                require(gauge.get('valid') == gauge.get('capacity_valid') == '1' and
                                    gauge.get('design_mah') == gauge.get('fcc_mah') == '3000' and
                                    gauge.get('unit_factor') == gauge.get('unit_verified') == '0' and sealed(gauge),
                                    'prepared Original cleanup standard/security mismatch')
                                self.report['preapply_standard_safe'] = True
                                save(self.out / 'preapply-Original-cleanup.json', dict(prepared=prepared, current=current,
                                    gauge=gauge, V1_ON_verified=True, apply_not_sent=True, no_restore_keys=True,
                                    fresh_standard_only=True, full131_current_read_not_claimed=True, provenance=provenance))
                require(legacy_check(self.call('gauge-native-recovery evidence')) == self.legacy, 'historical Failed changed')
                save(self.out / 'historical-Failed-after.json', self.legacy)
            except BaseException as exc:
                self.report['errors'].append('recovery: ' + repr(exc))
            # RAM settings only restore after proven Original and no UART uncertainty.
            if self.settings_touched and (self.report['original_verified'] or self.report.get('prekey_missing_standard_verified') or
                    self.report['preapply_standard_safe']) and not self.call.uncertain:
                try:
                    self.call.setup_cleanup = not self.apply_sent
                    self.call('settings restore')
                    if not self.apply_sent:
                        self.call.deadline = self.clock.monotonic() + 10
                    after = self.call('settings get')
                    require(after.get('temporary') == '0' and all(after[k] == self.settings[k] for k in CONFIG_KEYS), 'RAM settings restoration mismatch')
                    save(self.out / 'settings-after.json', after)
                    self.report['settings_restored'] = True
                except BaseException as exc:
                    self.report['errors'].append('settings: ' + repr(exc))
            self.report.update(unknown_ack=self.call.uncertain, elapsed_seconds=self.clock.monotonic() - (self.apply_start or self.started),
                               preflight_started_monotonic=self.started, apply_send_monotonic=self.apply_start,
                               apply_start_monotonic=self.apply_start,
                               setup_elapsed=self.report.get('setup_elapsed', self.clock.monotonic() - self.started),
                               deadline_armed=self.apply_start is not None,
                               apply_to_closure_elapsed=None if self.apply_start is None else self.clock.monotonic() - self.apply_start,
                               completed_unix=self.clock.time(), V1_footer=self.power.footer,
                               V1_controller_error=self.power.error, STOP_sticky=self.watcher.sticky.is_set())
            if getattr(self.call, 'audit_errors', []):
                self.report['errors'].append('UART audit IO failed: ' + repr(self.call.audit_errors))
            if getattr(self.power, 'cycle_fault', None):
                self.report['errors'].append('V1 cycle: ' + self.power.cycle_fault)
            if self.clock.monotonic() >= self.deadline:
                self.report['errors'].append('host660 deadline exceeded')
            self.report['complete'] = bool(self.apply_confirmed and self.report['target131_verified'] and
                self.report['original_verified'] and self.report['V1_ON_verified'] and self.report['settings_restored'] and
                not self.report['unknown_ack'] and not self.report['errors'] and not self.watcher.sticky.is_set())
            self.finish_report()
        return self.report

    def finish_report(self):
        path = self.out / 'report.json'
        try:
            digest = save(path, self.report)
            require(self.clock.monotonic() < self.deadline, 'final report persistence exceeded hard deadline')
            if self.report['complete']:
                save(Path(str(path) + '.complete.json'), dict(complete=True, report_sha256=digest,
                    within_deadline=True, persisted_elapsed=self.clock.monotonic() - (self.apply_start or self.started)))
                require(self.clock.monotonic() < self.deadline, 'completion receipt persistence exceeded hard deadline')
                # Completion is published only after both durable/readback files
                # have passed their post-IO checks. Late report IO leaves claim.
                self.claim.unlink()
                require(self.clock.monotonic() < self.deadline, 'claim unlink completed after hard deadline')
        except BaseException as exc:
            self.report['complete'] = False
            self.report['errors'].append('final persistence: ' + repr(exc))
            self.report['final_persistence_failed'] = True
            try:
                save(Path(str(path) + '.failed.json'), dict(complete=False, reason=repr(exc),
                    immutable_report_not_overwritten=True, unfinished_claim_retained=self.claim.exists()))
            except BaseException as receipt_error:
                self.report['errors'].append('failure receipt: ' + repr(receipt_error))
                # UNFINISHED remains; missing failure receipt is NOT success.


def parser():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--execute', action='store_true')
    p.add_argument('--confirm-v1-sole-5v', action='store_true')
    p.add_argument('--expected-boot', type=int)
    p.add_argument('--arm', choices=('C', 'B'), default='C')
    p.add_argument('--stage', choices=('on-smoke', 'observe'), default='on-smoke')
    p.add_argument('--proof', type=Path)
    p.add_argument('--on-smoke-evidence', type=Path, help='observe: same-arm smoke; B on-smoke: preceding C smoke (both hash-bound)')
    p.add_argument('--on-smoke-sha256')
    p.add_argument('--c-platform-evidence', type=Path, help='B observe only: successful C platform from the exact expected observation boot')
    p.add_argument('--c-platform-sha256')
    p.add_argument('--run', default='gct1-experiment-' + time.strftime('%Y%m%d-%H%M%S'))
    return p


def main(argv=None):
    args = parser().parse_args(argv)
    require(re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_.-]{0,100}', args.run) and args.run not in ('.', '..'), 'unsafe run')
    if not args.execute:
        print(json.dumps(dict(dry=True, ports_opened=0, DLL_loads=0, keys=0, no_experiment_performed=True,
            stage=args.stage, arm=args.arm, SOC_repair=False,
            requires=['--execute', '--confirm-v1-sole-5v', '--expected-boot', '--proof'],
            maximum_OFF_seconds=480, dynamic_shortening=True)))
        return 0
    require(args.confirm_v1_sole_5v and args.expected_boot is not None and args.expected_boot > 0 and args.proof is not None,
            'explicit sole5V/boot/proof admission required')
    require(Path(sys.executable).resolve() == Path('C:/Espressif/tools/python/v6.1/venv/Scripts/python.exe').resolve(), 'explicit IDF venv Python required')
    source = source_fields()
    target = target_fields(source, args.arm)
    proof, pairs = load_proof(args.proof)
    bindings = [dict(version=p['image']['version'], sha256=p['image']['sha256'], size=p['image']['size']) for p in pairs]
    accepted = {}
    if args.stage == 'observe':
        require(args.on_smoke_evidence is not None, 'successful on-smoke evidence required')
        accepted['on_smoke'] = load_evidence(args.on_smoke_evidence, args.on_smoke_sha256, stage='on-smoke', arm=args.arm, image_bindings=bindings)
    elif args.arm == 'B':
        require(args.on_smoke_evidence is not None, 'B on-smoke requires successful C on-smoke path/hash (no discharge)')
        accepted['C_on_smoke'] = load_evidence(args.on_smoke_evidence, args.on_smoke_sha256, stage='on-smoke', arm='C', image_bindings=bindings)
    if args.arm == 'B' and args.stage == 'observe':
        require(args.c_platform_evidence is not None, 'C-first platform evidence required; do not run B otherwise')
        accepted['C_platform'] = load_evidence(args.c_platform_evidence, args.c_platform_sha256, stage='observe', arm='C', image_bindings=bindings)
        require(accepted['C_platform'].get('boot_pin') == args.expected_boot, 'B observe requires C platform from this exact observation boot')
    out = ROOT / '.artifacts/private/mosaico' / args.run
    out.mkdir(exist_ok=False)
    save(out / 'inputs.json', dict(source=source, target=target, pairs=pairs, accepted=accepted, argv=vars(args) | {'proof': str(args.proof),
        'on_smoke_evidence': str(args.on_smoke_evidence), 'c_platform_evidence': str(args.c_platform_evidence)}))
    independent_stop = threading.Event()
    watcher = StopWatcher(out / 'STOP', independent_stop)
    client = None
    lock = None
    watcher.start()
    try:
        from serial.tools import list_ports
        from serial_debug_test import DebugClient
        ports = list(list_ports.comports())
        uart = topology(ports)
        save(out / 'inventory.json', [dict(port=p.device, vid=p.vid, pid=p.pid, serial=p.serial_number) for p in ports])
        lockpath = ROOT / '.artifacts/private/mosaico' / ('GCT1-UART-' + uart.upper() + '.lock')
        lock = lockpath.open('x')
        lock.write(str(out))
        lock.flush()
        client = DebugClient(uart, uart=True, wake_preamble=True)
        client.serial.write_timeout = 3
        call = Console(client, out)
        call('ping')
        power = Power(out, ports, independent_stop)
        experiment = Experiment(call, power, watcher, out, args, source, target, proof)
        experiment.report.update(image_bindings=bindings, boot_pin=args.expected_boot,
            accepted_evidence_hashes=dict(on_smoke=args.on_smoke_sha256, C_platform=args.c_platform_sha256))
        result = experiment.run()
        print(json.dumps(result, allow_nan=False))
        return 0 if result['complete'] else 1
    finally:
        watcher.close()
        if client is not None:
            client.close()
        if lock is not None:
            lock.close()
            lockpath.unlink()  # Never touches stale locks from other attempts.


if __name__ == '__main__':
    raise SystemExit(main())
