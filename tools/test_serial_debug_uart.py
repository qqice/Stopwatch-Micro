"""Offline, extracted-source UART transport checks. No device access."""
import json
import re
import subprocess
import tempfile
import unittest
import zlib
from unittest.mock import patch
from types import SimpleNamespace
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SOURCE = (ROOT / 'main/debug/serial_debug.cpp').read_text()
HEADER = ROOT / 'main/debug/serial_debug_transport.h'
COMPILERS = sorted(Path('C:/Espressif/tools/riscv32-esp-elf').glob('*/riscv32-esp-elf/bin/riscv32-esp-elf-g++.exe'))

def method(name):
    start = SOURCE.index('void SerialDebug::' + name + '(')
    opening = SOURCE.index('{', start)
    depth = 1
    end = opening + 1
    while depth:
        if SOURCE[end] == '{': depth += 1
        if SOURCE[end] == '}': depth -= 1
        end += 1
    return 'constexpr ' + SOURCE[start:end]

class UartTests(unittest.TestCase):
    def test_source_line_routing_and_ring(self):
        if not COMPILERS: self.skipTest('cross compiler unavailable')
        harness = r"""
#include <array>
#include <algorithm>
#include <cstdint>
#include <cstddef>
#include "serial_debug_transport.h"
#define MOSAICO_BOARD 1
#define CONFIG_IDF_TARGET_ESP32S31 1
using QueueHandle_t = void*;
constexpr int UART_NUM_0 = 0;
constexpr void uart_flush_input(int) {}
constexpr void xQueueReset(QueueHandle_t) {}
struct SerialDebug {
    enum class AsyncTest { None, Inputs };
    static constexpr std::size_t LineCapacity = 1536;
    std::array<char, LineCapacity> _line{}, _uart_line{};
    std::size_t _line_length=0, _uart_line_length=0;
    bool _line_overflow=false, _uart_line_overflow=false, _reply_uart=false, _async_uart=false;
    // Test-local equivalent of the scoped module writer: production syntax is checked separately.
    SerialDebug* _writer=nullptr;
    AsyncTest _async_test=AsyncTest::None;
    unsigned usbReplies=0, uartReplies=0, usbLines=0, uartLines=0;
    uint32_t _uart_read_errors=0;
    void* _uart_events=nullptr;
    constexpr void invalidateUartInput();
    constexpr void consume(char);
    constexpr void consumeFrom(char,bool);
    constexpr void result(const char*,const char*,const char*) {
        if(_reply_uart)++uartReplies; else ++usbReplies;
    }
    constexpr void handleLine(char* line) {
        if(_reply_uart)++uartLines;else ++usbLines;
        if(line[6]=='a' && _async_test==AsyncTest::None) _async_test=AsyncTest::Inputs;
        if(line[6]=='c') _async_test=AsyncTest::None;
        result("stub","PASS","");
    }
};
"""
        for text in ("debug ping", "debug display-test-frequency 160", "debug display-lock", "dbg help"):
            crc = zlib.crc32(text.encode("ascii"))
            wire = f"uart {crc:08x} {text}"
            literal = json.dumps(wire)
            harness += f"constexpr bool vector{crc}() {{ char s[] = {literal}; return serial_debug_transport::decodeUartLine(s, sizeof(s)-1) == s+14; }}\nstatic_assert(vector{crc}());\n"
        harness += method('consumeFrom') + '\n' + method('consume') + '\n' + method('invalidateUartInput')
        harness += r"""
constexpr void feed(SerialDebug& d, const char* text, bool uart) {
    for(std::size_t i=0;text[i];++i)d.consumeFrom(text[i],uart);
}
constexpr std::array<char,100> frame(const char* command) {
    std::array<char,100> out{};
    const char prefix[]="uart ";
    for(unsigned i=0;i<5;++i)out[i]=prefix[i];
    std::size_t length=0;while(command[length])++length;
    const auto crc=serial_debug_transport::crc32(command,length);
    for(unsigned i=0;i<8;++i)out[5+i]="0123456789abcdef"[(crc >> (28-4*i)) & 15];
    out[13]=' ';
    for(std::size_t i=0;i<length;++i)out[14+i]=command[i];
    out[14+length]='\n';return out;
}
constexpr bool splitAndSticky() {
    SerialDebug d;auto command=frame("debug async");
    for(unsigned i=0;command[i]!='\n';++i)d.consumeFrom(command[i],true);
    d.consumeFrom('p',false);
    if(d.uartLines || d.usbLines)return false;
    d.consumeFrom('\n',true);
    if(!d._async_uart || d.uartLines!=1 || d.usbLines!=0)return false;
    d.consumeFrom('\n',false);
    if(!d._async_uart || d.usbLines!=1 || d.usbReplies!=1 || d.uartReplies!=1)return false;
    feed(d,"debug cancel\n",false);
    if(d._async_uart || d._async_test!=SerialDebug::AsyncTest::None)return false;
    for(char c:d._uart_line)if(c)return false;
    for(char c:d._line)if(c)return false;
    return d._writer==nullptr && !d._reply_uart;
}
constexpr bool overflowIndependent() {
    SerialDebug d;
    for(unsigned i=0;i<1600;++i)d.consumeFrom('x',true);
    feed(d,"debug ping\r\n",false);
    d.consumeFrom('\b',true);d.consumeFrom('a',true);d.consumeFrom('\n',true);
    if(d.uartLines || d.usbLines!=1 || d.uartReplies!=1 || d._uart_line_overflow)return false;
    for(char c:d._uart_line)if(c)return false;
    auto command=frame("debug ping");feed(d,command.data(),true);
    return d.uartLines==1 && d.uartReplies==2 && !d._uart_line_length;
}
constexpr bool corruptAndRecovery() {
    SerialDebug d;
    feed(d,"debug ping\n",true); // Bare UART must never execute.
    auto command=frame("debug ping");command[20]='b'; // CRC mismatch, lost ASCII/error event.
    feed(d,command.data(),true);
    feed(d,"uart 12 debug ping\n",true); // Truncated checksum.
    auto control=frame("debug ping");
    d.consumeFrom(control[0],true);d.consumeFrom('\1',true);
    feed(d,control.data()+1,true); // Do not hide control corruption by filtering it.
    if(d.uartLines || d.uartReplies!=4)return false;
    auto good=frame("debug ping");feed(d,good.data(),true);
    if(d.uartLines!=1)return false;
    // Full event queue with a lost error clears pending bytes and drops a tail.
    feed(d,"uart dead",true);
    if(!serial_debug_transport::uartQueueSaturated(8) || serial_debug_transport::uartQueueSaturated(7))return false;
    d.invalidateUartInput();
    if(d._uart_line_length || !d._uart_line_overflow || d._uart_read_errors!=1)return false;
    for(char c:d._uart_line)if(c)return false;
    feed(d,good.data(),true); // This frame is discarded until its fresh delimiter.
    if(d.uartLines!=1)return false;
    feed(d,good.data(),true); // Clean next frame recovers.
    feed(d,"debug ping\n",false); // Bare USB compatibility after desync.
    return d.uartLines==2 && d.usbLines==1;
}
static_assert(serial_debug_transport::crc32("123456789",9)==0xcbf43926U);
static_assert(corruptAndRecovery());
constexpr bool partialAndBoundedTx() {
    serial_debug_transport::TxRing<8> ring;
    if(!ring.enqueue("abcdef",6))return false;
    char out[10]{}; unsigned n=0, calls=0;
    auto writer=[&](const char* p,std::size_t size) constexpr {
        ++calls; const auto take=size>2?2:size;
        for(std::size_t i=0;i<take;++i)out[n++]=p[i];
        return int(take);
    };
    ring.drain(writer,3);
    if(calls!=1 || n!=2 || ring.pending()!=4)return false;
    if(ring.enqueue("12345",5) || ring.overflow!=1)return false;
    if(!ring.enqueue("gh",2))return false;
    ring.drain([](const char*,std::size_t) constexpr {return 0;},128);
    if(ring.stalls!=1 || ring.pending()!=6)return false;
    for(unsigned i=0;i<4;++i)ring.drain(writer,128);
    for(unsigned i=0;i<8;++i)if(out[i]!="abcdefgh"[i])return false;
    ring.clear(); return ring.pending()==0;
}
static_assert(splitAndSticky());
static_assert(overflowIndependent());
static_assert(partialAndBoundedTx());
"""
        with tempfile.TemporaryDirectory() as directory:
            source=Path(directory)/'uart-harness.cpp';source.write_text(harness)
            result=subprocess.run([str(COMPILERS[-1]),'-std=c++20','-fsyntax-only','-I'+str(HEADER.parent),str(source)],capture_output=True,text=True)
            log=ROOT/'.artifacts/mosaico/uart-debug-source-harness.log';log.parent.mkdir(parents=True,exist_ok=True);log.write_text(result.stdout+result.stderr)
            self.assertEqual(result.returncode,0,str(log))

    def test_sensitive_policy(self):
        # Evaluate actual allowlist, not a mirrored policy.
        text=HEADER.read_text(); allowed=set(re.findall(r'"([a-z0-9-]+)"',text.split('allowed[] =',1)[1]))
        for command in ('ota-check','ota-download','ota-install','ota-reboot','ota-update','ota-rollback-test','runtime-restart','gauge-access','gauge-reconcile','gauge-nominal','network-config','tailscale-config','pairing-reset','host-usage'):
            self.assertNotIn(command,allowed)
        for command in ('status','power','gauge','display-lock','display-wake','display-test-frequency','display-idle-frequency','boot','mic','inputs','cancel'):
            self.assertIn(command,allowed)
        self.assertIn('ota-bypass',allowed)
        bypass=SOURCE.split('if (command && (!std::strcmp(command, "ota-update")',1)[1].split('if (command && std::strcmp(command, "ota-rollback-test")',1)[0]
        self.assertIn('"CONFIRM_EXTERNAL_POWER"',bypass)
        self.assertIn('MosaicoOta::request()',bypass)
        self.assertIn('_async_test != AsyncTest::None',bypass)
        self.assertNotIn('esp_ota_set_boot_partition',bypass)
        gate=SOURCE.split('char* command =',1)[1].split('#ifdef MOSAICO_BOARD',1)[0]
        self.assertIn('uartAllowed(command)',gate)
        self.assertIn('std::strcmp(value, "160") && std::strcmp(value, "320")',gate)
        self.assertIn('cancel_first=1',gate)

    def test_budgets_flow_clock_and_error_discard(self):
        poll=method('poll')
        self.assertNotIn('while (',poll)
        self.assertIn('std::array<char, 128>',poll)
        self.assertIn('uart_read_bytes(UART_NUM_0, bytes.data(), bytes.size(), 0)',poll)
        self.assertIn('_uart_line_overflow = true',method('invalidateUartInput'))
        self.assertIn('i < serial_debug_transport::UartEventCapacity',poll)
        self.assertIn('uxQueueMessagesWaiting',poll)
        self.assertGreaterEqual(poll.count('saturated()'),4)
        self.assertIn('UART_SCLK_XTAL',SOURCE)
        self.assertIn('UART_HW_FLOWCTRL_DISABLE',SOURCE)
        self.assertIn('uart_set_pin(UART_NUM_0, 58, 59,',SOURCE)
        self.assertIn('uart_driver_install(UART_NUM_0, 2048, 0, serial_debug_transport::UartEventCapacity, &events, 0)',SOURCE)
        for forbidden in ('uart_write_bytes(', 'uart_wait_tx_done(', 'esp_pm_lock_acquire(', 'xTaskCreate(', 'uart_vfs_dev_use_driver('):
            self.assertNotIn(forbidden,SOURCE)
        self.assertIn('#if defined(MOSAICO_BOARD) && CONFIG_IDF_TARGET_ESP32S31',method('drainUart'))
        self.assertIn('BootTracePrint(debugPrintf)',SOURCE)
        self.assertNotIn('std::printf(',SOURCE)

    def test_host_encoder_and_default_usb(self):
        import serial_debug_test as host
        self.assertEqual(host.encode_command('debug ping'), b'debug ping\n')
        self.assertEqual(host.encode_command('debug ping', True), b'uart ' + f"{zlib.crc32(b'debug ping'):08x}".encode() + b' debug ping\n')
        for invalid in ('debug ping\n', 'debug ping\r', 'debug \tping'):
            with self.assertRaises(ValueError):host.encode_command(invalid,True)
        with self.assertRaises(ValueError):host.encode_command('debug '+('x'*1530),True)
        for uart in (False, True):
            writes=[]
            fake=SimpleNamespace(open=lambda:None, close=lambda:None, write=writes.append,
                readline=lambda:b'DBG RESULT command=ping status=PASS reply=pong\r\n')
            with patch.object(host.serial,'Serial',return_value=fake):
                client=host.DebugClient('TEST-NO-DEVICE',uart=uart)
                self.assertFalse(fake.dtr);self.assertFalse(fake.rts)
                client.handshake();client.command('debug ping','ping')
                self.assertEqual(writes,[host.encode_command('debug ping',uart)]*2)
        with patch('sys.argv',['serial_debug_test.py','--uart']), patch.object(host,'discover_port') as discover:
            with self.assertRaises(SystemExit) as caught:host.main()
            self.assertEqual(caught.exception.code,2);discover.assert_not_called()

    def test_one_command_cli_does_not_run_suite(self):
        import serial_debug_test as host
        fake=SimpleNamespace(handshake=lambda:None, close=lambda:None,
            command=lambda text, expected, timeout=5:host.Result(expected,'PASS','ok=1'))
        with patch('sys.argv',['serial_debug_test.py','--uart','--port','TEST-NO-DEVICE','--command','debug power']), patch.object(host,'DebugClient',return_value=fake) as create, patch.object(host,'run_automated') as suite:
            self.assertEqual(host.main(),0)
            create.assert_called_once_with('TEST-NO-DEVICE',uart=True);suite.assert_not_called()
        for invalid in ('power','notdebug power','debug','debug power\n','debug  power'):
            with patch('sys.argv',['serial_debug_test.py','--uart','--port','TEST-NO-DEVICE','--command',invalid]), patch.object(host,'DebugClient') as create:
                with self.assertRaises(SystemExit):host.main()
                create.assert_not_called()

    def test_single_async_result_and_timeout(self):
        import serial_debug_test as host
        for text in ('debug ui', 'debug ui cycle'):
            calls=[]
            def command(text, expected, timeout):
                calls.append((expected,timeout))
                return host.Result('ui-cycle','PASS','final=quota')
            fake=SimpleNamespace(handshake=lambda:None,close=lambda:None,command=command)
            with patch('sys.argv',['serial_debug_test.py','--uart','--port','TEST-NO-DEVICE','--command',text,'--command-timeout','65']),patch.object(host,'DebugClient',return_value=fake):
                self.assertEqual(host.main(),0)
                self.assertEqual(calls,[('ui-cycle',65.0)])
        for timeout in ('nan','inf','0','601'):
            with patch('sys.argv',['serial_debug_test.py','--port','TEST-NO-DEVICE','--command','debug power','--command-timeout',timeout]),patch.object(host,'DebugClient') as create:
                with self.assertRaises(SystemExit):host.main()
                create.assert_not_called()

    def test_actual_target_syntax(self):
        logs=[]
        for db in (ROOT/'.artifacts/mosaico/ota-staged-heapdiag-build/compile_commands.json',ROOT/'build/compile_commands.json'):
            if not db.exists():self.skipTest('configured build unavailable')
            entries=json.loads(db.read_text())
            for name in ('serial_debug.cpp','boot_trace.cpp'):
                entry=next(e for e in entries if e['file'].replace('\\','/').endswith('/'+name))
                command=re.sub(r' -o \S+ -c ', ' -fsyntax-only ',entry['command'])
                result=subprocess.run(command,cwd=entry['directory'],capture_output=True,text=True)
                logs.append(str(db)+' '+name+'\n'+result.stdout+result.stderr)
                log=ROOT/'.artifacts/mosaico/uart-debug-syntax.log';log.parent.mkdir(parents=True,exist_ok=True);log.write_text('\n'.join(logs))
                self.assertEqual(result.returncode,0,str(log))

if __name__=='__main__':unittest.main()
