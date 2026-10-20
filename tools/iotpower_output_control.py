"""Bounded V1 output-only transaction: snapshot by default, no limit/voltage edits.

Uses documented native APIs, never guessed serial opcodes. See the companion doc
for ABI evidence gaps and the limits of software-only recovery.
"""
import argparse
import ctypes
import json
import math
from pathlib import Path
import queue
import threading
import time

from iotpower_capture import NativeBackend

ROOT = Path(__file__).resolve().parents[1]


class ReplyTimeout(RuntimeError):
    """No eligible reply, distinct from disconnect/parse/protocol failures."""


class OutputBackend(NativeBackend):
    """Documented x64 C ABI; no claim of setter-specific managed ABI proof."""
    def __init__(self, dll, expected_sha256):
        super().__init__(dll, expected_sha256)
        for name, args, result in (
                ('iot_get_current_uint', [], ctypes.c_uint8),
                ('iot_get_set_current', [], ctypes.c_uint16),
                ('iot_get_set_voltage', [], ctypes.c_uint16),
                ('iot_uart_send_set', [ctypes.c_uint32, ctypes.c_uint8,
                                       ctypes.c_uint32, ctypes.c_uint8], None)):
            function = getattr(self.lib, name)
            function.argtypes, function.restype = args, result

    def read(self):
        kind = self.lib.iot_parse()
        if kind == 4:
            # Must run immediately on the sole parser thread, before next parse.
            return kind, {'current': self.lib.iot_get_set_current(),
                          'unit': self.lib.iot_get_current_uint(),
                          'voltage': self.lib.iot_get_set_voltage(),
                          'on': self.lib.iot_get_power_on()}
        return kind, None  # Do not enqueue the 10 kHz measurement stream.

    def set_output(self, original, on):
        self.lib.iot_uart_send_set(original['current'], original['unit'],
                                   original['voltage'], on)


def validate_identity(port, ports):
    """Fail closed on ambiguous adapter identity or a visible board CDC port."""
    target = [p for p in ports if p.device.upper() == port.upper()]
    adapters = [p for p in ports if (p.vid, p.pid, p.serial_number) == (0x10c4, 0xea60, '0001')]
    if (len(target) != 1 or len(adapters) != 1 or
            (target[0].vid, target[0].pid, target[0].serial_number) != (0x10c4, 0xea60, '0001')):
        raise ValueError('requires unique CP210x 10c4:ea60 serial 0001 at the explicit COM port')
    if not port.upper().startswith('COM') or not port[3:].isdigit() or int(port[3:]) < 1:
        raise ValueError('V1 requires a COM port')
    for p in ports:
        description = (p.description or '').lower()
        jlink = (p.vid, p.pid) == (0x1366, 0x0105)
        if p.vid == 0x303a or (p.vid is not None and not jlink and
                              ('cdc' in description or 'usb jtag/serial' in description)):
            raise ValueError('board CDC is present: single-supply topology not established')
    return port.upper()


def checked_state(value):
    if not isinstance(value, dict) or set(value) != {'current', 'unit', 'voltage', 'on'}:
        raise RuntimeError('invalid raw settings snapshot')
    limits = {'current': 65535, 'voltage': 65535, 'unit': 1, 'on': 1}
    if any(type(value[k]) is not int or not 0 <= value[k] <= high for k, high in limits.items()):
        raise RuntimeError('raw settings snapshot outside documented getter types')
    return dict(value)


class Controller:
    def __init__(self, backend, log, timeout):
        self.backend, self.log, self.timeout = backend, log, timeout
        self.events = queue.Queue(maxsize=32)
        self.stop = threading.Event()
        self.lock = threading.Lock()
        self.condition = threading.Condition(self.lock)
        self.sequence = 0
        self.epoch = 0
        self.read_entered_epoch = -1
        self.cross_epoch_drops = 0
        self.reader_error = None
        self.thread = threading.Thread(target=self.reader, daemon=True)

    def reader(self):
        try:
            while not self.stop.is_set():
                with self.condition:
                    read_epoch = self.epoch
                    self.read_entered_epoch = read_epoch
                    self.condition.notify_all()
                kind, payload = self.backend.read()
                received_tick = time.perf_counter_ns()
                received_at = time.monotonic()  # Before validation or publication waits.
                if kind == 255:
                    self.reader_error = 'serial disconnected'
                    with self.condition:
                        self.condition.notify_all()
                    return
                if kind not in (4, 6, 255):
                    continue
                with self.lock:
                    if read_epoch != self.epoch:
                        self.cross_epoch_drops += 1
                        continue
                    if kind == 4:
                        payload = checked_state(payload)
                    self.sequence += 1
                    self.events.put_nowait((self.sequence, received_at, kind, payload, read_epoch,
                                            received_tick))
        except BaseException as exc:
            self.reader_error = f'{type(exc).__name__}: {exc}'
            with self.condition:
                self.condition.notify_all()

    def start(self):
        self.thread.start()

    def bootstrap(self):
        """Send one initial request before reader entry, then consume its 04."""
        with self.condition:
            self.epoch += 1
            epoch = self.epoch
            marker = self.sequence
            self.log('request', operation='bootstrap_initial', after_sequence=marker, epoch=epoch)
            send_start = time.monotonic()
            send_tick = time.perf_counter_ns()
            self.backend.initialize()
            self.log('request_sent', operation='bootstrap_initial',
                     send_start_monotonic=send_start, send_start_perf_counter_ns=send_tick,
                     epoch=epoch)
        self.start()
        return self.receive(4, (marker, epoch, send_tick))

    def begin(self, operation, action, recovery=False):
        # Every blocking read keeps its entry epoch: publication can never turn
        # an already-read old reply into a reply to a subsequent request.
        with self.condition:
            self.epoch += 1
            epoch = self.epoch
            self.condition.notify_all()
            while True:
                try:
                    stale = self.events.get_nowait()
                    if stale[2] == 255 and not recovery:
                        raise RuntimeError('serial disconnected before request')
                    self.log('discard_stale', sequence=stale[0], kind=stale[2])
                except queue.Empty:
                    break
            if self.reader_error and not recovery:
                raise RuntimeError(self.reader_error)
            # The V1 stream continues even with output OFF. Let a measurement
            # advance the parser into this epoch. Never send a wake-up initial:
            # it could still await 04 when the target setter is transmitted.
            if not self.reader_error:
                deadline = time.monotonic() + self.timeout
                while self.read_entered_epoch != epoch and not self.reader_error:
                    remaining = deadline - time.monotonic()
                    if remaining <= 0:
                        if not recovery:
                            raise RuntimeError('timeout synchronizing parser request epoch')
                        self.log('restore_epoch_barrier_timeout')
                        break  # Still attempt ON once; no extra initial interferes.
                    self.condition.wait(remaining)
            if self.reader_error and not recovery:
                raise RuntimeError(self.reader_error)
            while True:
                try:
                    stale = self.events.get_nowait()
                    self.log('discard_stale', sequence=stale[0], kind=stale[2])
                except queue.Empty:
                    break
            marker = self.sequence
            self.log('request', operation=operation, after_sequence=marker, epoch=epoch,
                     cross_epoch_drops=self.cross_epoch_drops)
            send_start = time.monotonic()  # After logging, immediately before native action.
            send_tick = time.perf_counter_ns()
            action()
            self.log('request_sent', operation=operation, send_start_monotonic=send_start,
                     send_start_perf_counter_ns=send_tick, epoch=epoch)
        return marker, epoch, send_tick

    def receive(self, kind, marker):
        after_sequence, epoch, send_tick = marker
        kinds = kind if isinstance(kind, tuple) else (kind,)
        deadline = time.monotonic() + self.timeout
        while True:
            if self.reader_error:
                raise RuntimeError(self.reader_error)
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise ReplyTimeout('timeout waiting for kind ' + '/'.join(f'{k:02x}' for k in kinds))
            try:
                seq, stamp, received, payload, read_epoch, received_tick = self.events.get(timeout=min(remaining, .1))
            except queue.Empty:
                continue
            self.log('reply', sequence=seq, receive_monotonic=stamp, kind=received, raw=payload,
                     read_epoch=read_epoch, receive_perf_counter_ns=received_tick)
            if received == 255:
                raise RuntimeError('serial disconnected')
            if (seq > after_sequence and read_epoch == epoch and
                    received_tick > send_tick and received in kinds):
                return (received, payload) if isinstance(kind, tuple) else payload

    def snapshot(self):
        marker = self.begin('initial', self.backend.initialize)
        return self.receive(4, marker)

    def toggle(self, original, on, recovery=False):
        expected = dict(original, on=on)
        self.log('set_raw', raw=expected)
        marker = self.begin('set_on' if on else 'set_off',
                            lambda: self.backend.set_output(original, on), recovery=recovery)
        ack_seen = False
        confirmation_source = 'query_only'
        try:
            kind, status = self.receive((4, 6), marker)
            if kind == 6:
                ack_seen = True
                confirmation_source = '06+query'
            else:
                if status != expected:
                    raise RuntimeError(f'raw unsolicited status mismatch: expected {expected}, received {status}')
                confirmation_source = 'status04+query'
        except ReplyTimeout:
            # Observed V1 may emit no 06. Query once; never resend the setter.
            self.log('set_reply_timeout_query_once', ack_seen=False)
        self.log('set_reply_evidence', ack_seen=ack_seen, confirmation_source=confirmation_source)
        readback = self.snapshot()
        if readback != expected:
            raise RuntimeError(f'raw readback mismatch: expected {expected}, received {readback}')
        self.log('verified', raw=readback, confirmation_source=confirmation_source,
                 ack_seen=ack_seen)

    def hold(self, seconds, stop_requested=None):
        deadline = time.monotonic() + seconds
        while time.monotonic() < deadline:
            if stop_requested is not None and stop_requested():
                raise RuntimeError('external stop requested during off interval')
            if self.reader_error:
                raise RuntimeError(self.reader_error)
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                return
            try:
                seq, stamp, kind, payload, read_epoch, received_tick = self.events.get(timeout=min(.1, remaining))
            except queue.Empty:
                continue
            self.log('off_interval_reply', sequence=seq, receive_monotonic=stamp, kind=kind,
                     raw=payload, read_epoch=read_epoch, receive_perf_counter_ns=received_tick)
            if kind == 255:
                raise RuntimeError('serial disconnected during off interval')

    def close(self):
        self.stop.set()
        self.backend.close()  # Out of band: never hold the publication lock.
        self.thread.join(timeout=2)
        if self.thread.is_alive():
            raise RuntimeError('parser did not stop; do not unload DLL or reuse connection')


def run(backend, port, ports, output, off_seconds=0, confirm_single_supply=False,
        timeout=5, lock_directory=None, stop_requested=None):
    if (type(off_seconds) is not int or not 0 <= off_seconds <= 600 or
            (off_seconds and not confirm_single_supply)):
        raise ValueError('off requires integer 1..600 seconds and --confirm-single-supply')
    if not math.isfinite(timeout) or not 0 < timeout <= 30:
        raise ValueError('timeout must be finite and in (0,30] seconds')
    if stop_requested is not None and not callable(stop_requested):
        raise ValueError('stop_requested must be callable or None')
    port = validate_identity(port, ports)
    output = Path(output)
    if output.suffix.lower() != '.jsonl' or not output.parent.is_dir():
        raise ValueError('output requires a new .jsonl file under an existing directory')
    locks = Path(lock_directory) if lock_directory is not None else ROOT / '.artifacts/iotpower'
    locks.mkdir(parents=True, exist_ok=True)
    lock_path = locks / (port + '.output-control.lock')
    # Cooperative process exclusion plus vendor open's busy-port failure. Stale
    # locks are intentionally not removed automatically after abnormal death.
    with lock_path.open('x', encoding='utf-8') as lock_file:
        lock_file.write(str(output.resolve()))
        lock_file.flush()
        release_lock = True  # No native session has yet been attempted.
        try:
            with output.open('x', encoding='utf-8') as stream:
                audit_error = None
                recovering = False
                def log(event, **data):
                    nonlocal audit_error
                    try:
                        stream.write(json.dumps(dict(event=event, monotonic=time.monotonic(),
                                                     unix_time=time.time(), **data), allow_nan=False) + '\n')
                        stream.flush()
                    except (OSError, ValueError) as exc:
                        audit_error = f'audit logging failed: {exc}'
                        if not recovering:
                            raise
                control = Controller(backend, log, timeout)
                original = None
                attempted_off = False
                opened = False
                error = None
                restored = False
                try:
                    log('start', port=port, off_seconds=off_seconds,
                        dll_sha256=getattr(backend, 'dll_sha256', None))
                    release_lock = False
                    backend.open(port)
                    opened = True
                    first = control.bootstrap()
                    second = control.snapshot()
                    if first != second:
                        raise RuntimeError('two initial raw snapshots disagree')
                    original = dict(first)
                    log('original', raw=original)
                    if off_seconds:
                        if original['on'] != 1:
                            raise RuntimeError('output must initially be ON; no off request sent')
                        if stop_requested is not None and stop_requested():
                            raise RuntimeError('external stop requested before off')
                        attempted_off = True  # Set BEFORE possibly side-effecting native call.
                        off_started = time.monotonic()
                        control.toggle(original, 0)
                        control.hold(max(0, off_seconds - (time.monotonic() - off_started)),
                                     stop_requested=stop_requested)
                except BaseException as exc:
                    error = f'{type(exc).__name__}: {exc}'
                finally:
                    recovering = True  # Logging failure must never prevent the ON attempt.
                    if attempted_off:
                        try:
                            log('restore_attempt', raw=dict(original, on=1))
                            control.toggle(original, 1, recovery=True)
                            restored = True
                            log('restored_on_verified')
                        except BaseException as exc:
                            error = (error + '; ' if error else '') + f'RESTORE FAILED, STATE UNKNOWN: {type(exc).__name__}: {exc}'
                    if opened:
                        try:
                            control.close()
                            release_lock = True
                        except BaseException as exc:
                            error = (error + '; ' if error else '') + f'close failed: {exc}'
                    if audit_error:
                        error = (error + '; ' if error else '') + audit_error
                    log('finish', complete=error is None, error=error,
                        restored_on_verified=restored, original=original,
                        com_lock_retained=not release_lock, lock_path=str(lock_path))
                    if audit_error and audit_error not in (error or ''):
                        error = (error + '; ' if error else '') + audit_error
                if error:
                    raise RuntimeError(error)
                return {'snapshot': original, 'restored_on_verified': restored,
                        'off_seconds': off_seconds, 'complete': True}
        finally:
            # Close before unlink on Windows; the outer with closes again safely.
            lock_file.close()
            if release_lock:
                lock_path.unlink()


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--port', required=True)
    parser.add_argument('--dll', required=True)
    parser.add_argument('--dll-sha256', required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--off-seconds', type=int, default=0)
    parser.add_argument('--confirm-single-supply', action='store_true')
    parser.add_argument('--timeout', type=float, default=5)
    args = parser.parse_args(argv)
    try:
        from serial.tools import list_ports
        ports = list(list_ports.comports())
        validate_identity(args.port, ports)  # Reject topology before loading native code.
        if not 0 <= args.off_seconds <= 600 or (args.off_seconds and not args.confirm_single_supply):
            raise ValueError('off requires 1..600 seconds and --confirm-single-supply')
        backend = OutputBackend(args.dll, args.dll_sha256)
        result = run(backend, args.port, ports, args.output, args.off_seconds,
                     args.confirm_single_supply, args.timeout)
    except (ValueError, OSError, RuntimeError, KeyboardInterrupt) as exc:
        parser.exit(1, f'output control failed: {exc}\n')
    print(json.dumps(result))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
