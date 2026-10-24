"""Offline RV32 execution of REAL SerialDebug::handleCold and strict numeric parser.

HAL/Cold/NAND and libc tokenization are bounded mocks. Existing UART whitelist
and CRC decoder are compiled unchanged (authorization denial is fault-injected
through a wrapper). This is not UART/device/SDK or physical-trial validation.
"""
import hashlib
import json
import os
from pathlib import Path
import re
import struct
import subprocess
import sys
import tempfile
import unittest
import zlib

from test_mosaico_gauge_scale_backend import CLANG, HEADERS

ROOT = Path(__file__).resolve().parents[1]
RUN = ROOT / 'artifacts/gauge-cold-console-tests'
SOURCE = ROOT / 'main/debug/serial_debug.cpp'
TRANSPORT = ROOT / 'main/debug/serial_debug_transport.h'


def extract(source, signature):
    start = source.index(signature)
    opening = source.index('{', start)
    depth = 1
    tokens = re.compile(r'''"(?:\\.|[^"\\])*"|'(?:\\.|[^'\\])*'|//[^\n]*|/\*.*?\*/|[{}]''', re.S)
    for token in tokens.finditer(source, opening + 1):
        if token.group() == '{':
            depth += 1
        elif token.group() == '}':
            depth -= 1
        if not depth:
            return source[start:token.end()]
    raise AssertionError('unterminated ' + signature)


def vectors():
    cases = []

    def add(name, line, action=-1, handled=True, **kwargs):
        cases.append(dict(name=name, line=line, action=action, handled=handled, **kwargs))

    for i, line in enumerate((None, '', 'debug', 'dbg', 'gauge-cold status', 'debugx gauge-cold status',
        'dbgx gauge-cold status', 'debug gauge-cold-extra status', 'debug gauge-coldx',
        'debug gauge-contrast status', 'debug gauge-col', 'DEBUG gauge-cold status',
        'debug GAUGE-COLD status', 'debug\ngauge-cold status', '  other gauge-cold status')):
        add('nonmatching_' + str(i), line, handled=False, unchanged=True)
    for i, line in enumerate(('debug gauge-cold', 'dbg gauge-cold', 'debug gauge-cold status',
        ' \tdebug\t gauge-cold\tstatus \t', 'dbg gauge-cold\t', 'debug gauge-cold status  ')):
        add('status_' + str(i), line, 0)
    valid = [('verify x', 7), ('bind x CONFIRM', 1), ('apply CONFIRM_EXTERNAL_POWER', 2),
        ('handoff CONFIRM_POWER_OFF_30S', 3), ('recover CONFIRM', 4), ('cancel CONFIRM', 5)]
    for command, action in valid:
        add('valid_' + command.split()[0], 'debug gauge-cold ' + command, action)
        add('dbg_' + command.split()[0], '\tdbg\tgauge-cold\t' + command.replace(' ', '\t'), action)
    for length in (1, 355, 356):
        add('bind_length_' + str(length), 'debug gauge-cold bind ' + 'A' * length + ' CONFIRM', 1, bind_length=length)
    for page in ('0', '1', '2', '3', '4', '00', '0004'):
        add('page_' + page, 'debug gauge-cold evidence-page ' + page, 6, page=int(page))
    invalid = ['status unexpected', 'status x y', 'status CONFIRM', 'status x y z',
        'verify', 'verify x CONFIRM', 'verify x extra', 'verify '+ 'A'*357, 'bind', 'bind x', 'bind CONFIRM', 'bind x confirm', 'bind x CONFIRM extra',
        'bind x CONFIRM extra extra', 'bind ' + 'A' * 357 + ' CONFIRM',
        'apply', 'apply CONFIRM', 'apply confirm_external_power', 'apply CONFIRM_EXTERNAL_POWER CONFIRM',
        'apply CONFIRM_EXTERNAL_POWER x y', 'handoff', 'handoff CONFIRM', 'handoff CONFIRM_POWER_OFF_60S',
        'handoff CONFIRM_POWER_OFF_30S x', 'recover', 'recover confirm', 'recover CONFIRM x',
        'cancel', 'cancel CONFIRM_EXTERNAL_POWER', 'cancel CONFIRM x', 'evidence-page',
        'evidence-page -1', 'evidence-page +1', 'evidence-page 0x1', 'evidence-page 1abc',
        'evidence-page 5', 'evidence-page 4294967296', 'evidence-page 999999999999999999999999999',
        'evidence-page 1.0', 'evidence-page 0 CONFIRM', 'evidence-page 0 x y', 'unknown',
        'STATUS', 'BIND x CONFIRM', 'apply CONFIRM_EXTERNAL_POWERextra']
    for i, command in enumerate(invalid):
        add('invalid_' + str(i), 'debug gauge-cold ' + command)
    all_actions = [('status', 0), *valid, ('evidence-page 4', 6)]
    for command, action in all_actions:
        name = command.split()[0]
        add('nand_busy_' + name, 'debug gauge-cold ' + command, busy=True, unchanged=True, detail='reason=nand_format_maintenance no_changes=1')
        add('uart_denied_' + name, 'debug gauge-cold ' + command, uart=True, deny=True, unchanged=True, detail='reason=uart_authorization no_changes=1')
        add('uart_allowed_' + name, 'debug gauge-cold ' + command, action, uart=True)
        add('backend_fail_' + name, 'debug gauge-cold ' + command, action, backend=False)
    add('usb_not_uart_gate', 'debug gauge-cold status', 0, deny=True)
    for i, payload in enumerate(('debug gauge-cold status', 'dbg gauge-cold evidence-page 4',
        'debug gauge-cold apply CONFIRM_EXTERNAL_POWER')):
        frame = 'uart ' + f'{zlib.crc32(payload.encode()) & 0xffffffff:08x}' + ' ' + payload
        add('crc_valid_' + str(i), frame, (0, 6, 2)[i], uart=True, framed=True, page=4 if i == 1 else -1)
        add('crc_bad_' + str(i), frame[:5] + ('0' if frame[5] != '0' else '1') + frame[6:], handled=False, uart=True, framed=True, unchanged=True)
    add('crc_unframed_rejected', 'debug gauge-cold status', handled=False, uart=True, framed=True, unchanged=True)
    add('crc_control_rejected', 'uart 00000000 debug\tgauge-cold status', handled=False, uart=True, framed=True, unchanged=True)
    return cases


SUPPORT = r'''
#include <cstdint>
#include <cstddef>
#include <cstring>
extern "C" void* memcpy(void* a,const void* b,size_t n){for(size_t i=0;i<n;++i)static_cast<char*>(a)[i]=static_cast<const char*>(b)[i];return a;}
extern "C" void* memset(void* a,int c,size_t n){for(size_t i=0;i<n;++i)static_cast<char*>(a)[i]=char(c);return a;}
extern "C" int memcmp(const void* a,const void* b,size_t n){for(size_t i=0;i<n;++i)if(static_cast<const unsigned char*>(a)[i]!=static_cast<const unsigned char*>(b)[i])return 1;return 0;}
int errno=0;
namespace std {
size_t strlen(const char* p){size_t n=0;while(p[n])++n;return n;}
int strcmp(const char* a,const char* b){while(*a&&*a==*b){++a;++b;}return uint8_t(*a)-uint8_t(*b);}
int strncmp(const char* a,const char* b,size_t n){for(size_t i=0;i<n;++i){const int d=uint8_t(a[i])-uint8_t(b[i]);if(d||!a[i])return d;}return 0;}
unsigned long strtoul(const char* value,char** end,int base){uint32_t n=0;const char* p=value;bool overflow=false;
 if(base!=10){errno=22;*end=const_cast<char*>(value);return 0;}
 while(*p>='0'&&*p<='9'){const uint32_t digit=uint32_t(*p-'0');if(n>(0xffffffffU-digit)/10)overflow=true;else if(!overflow)n=n*10+digit;++p;}
 *end=const_cast<char*>(p);if(overflow){errno=34;return 0xffffffffU;}return n;
}
}
static bool delimiter(char c,const char* list){for(;*list;++list)if(c==*list)return true;return false;}
extern "C" char* strtok_r(char* text,const char* delimiters,char** saved){char* p=text?text:*saved;if(!p)return nullptr;
 while(*p&&delimiter(*p,delimiters))++p;if(!*p){*saved=p;return nullptr;}
 char* token=p;while(*p&&!delimiter(*p,delimiters))++p;if(*p)*p++=0;*saved=p;return token;
}
static bool forceUartDeny=false,nandBusy=false,backendOk=true;
static unsigned calls[8]{},resultCount=0,lastPage=99,lastBindLength=0;
static char resultStatus[8]{},resultDetails[1024]{};
static void copyString(char* out,const char* text,size_t cap){size_t i=0;for(;i+1<cap&&text&&text[i];++i)out[i]=text[i];out[i]=0;}
namespace GaugeDischargeTrace {namespace NandBackend {bool busy(){return nandBusy;}}}
namespace MosaicoGaugeCold {
static bool invoked(unsigned action,char* out,size_t n){++calls[action];copyString(out,"mock_cold_only",n);return backendOk;}
bool status(char* out,size_t n){return invoked(0,out,n);}
bool verifyFragment(const char* fragment,char* out,size_t n){lastBindLength=std::strlen(fragment);return invoked(7,out,n);}
bool bindFragment(const char* fragment,char* out,size_t n){lastBindLength=std::strlen(fragment);return invoked(1,out,n);}
bool apply(char* out,size_t n){return invoked(2,out,n);}
bool handoff(char* out,size_t n){return invoked(3,out,n);}
bool recover(char* out,size_t n){return invoked(4,out,n);}
bool cancel(char* out,size_t n){return invoked(5,out,n);}
bool evidencePage(uint32_t page,char* out,size_t n){lastPage=page;return invoked(6,out,n);}
}
struct SerialDebug {bool _reply_uart=false;bool handleCold(char*);
 static constexpr size_t DiagnosticCapacity=1024;char diagnostic[DiagnosticCapacity]{};char* diagnosticScratch(){return diagnostic;}
 void result(const char* command,const char* status,const char* details){++resultCount;if(std::strcmp(command,"gauge-cold"))resultCount+=100;copyString(resultStatus,status,sizeof(resultStatus));copyString(resultDetails,details,sizeof(resultDetails));}
};
'''

CHECK = r'''
static int check(const char* input,bool handled,int action,bool uart,bool deny,bool busy,bool backend,bool unchanged,bool framed,int page,int bindLength,const char* detail){
 forceUartDeny=deny;nandBusy=busy;backendOk=backend;resultCount=0;lastPage=99;lastBindLength=0;for(unsigned i=0;i<8;++i)calls[i]=0;resultStatus[0]=resultDetails[0]=0;
 char line[1024]{},original[1024]{};if(input){copyString(line,input,sizeof(line));copyString(original,input,sizeof(original));}
 SerialDebug console;console._reply_uart=uart;char* command=input?line:nullptr;
 if(framed&&command)command=serial_debug_transport::decodeUartLine(command,std::strlen(command));
 const bool actual=command?console.handleCold(command):console.handleCold(nullptr);
 if(actual!=handled)return 1;
 if(unchanged&&std::strcmp(line,original))return 2;
 if(resultCount!=(handled?1U:0U))return 3;
 unsigned total=0;for(unsigned i=0;i<8;++i){total+=calls[i];if(calls[i]!=unsigned(action==int(i)))return 4;}
 if(total!=unsigned(action>=0))return 5;
 if(handled&&std::strcmp(resultStatus,action>=0&&backend?"PASS":"FAIL"))return 6;
 if(detail&&std::strcmp(resultDetails,detail))return 7;
 if(action==6&&page>=0&&lastPage!=unsigned(page))return 8;
 if((action==1||action==7)&&bindLength>=0&&lastBindLength!=unsigned(bindLength))return 9;
 return 0;
}
'''


def elf_symbols(elf):
    offset = struct.unpack_from('<I', elf, 32)[0]
    size, count = struct.unpack_from('<HH', elf, 46)
    symbols = {}
    for index in range(count):
        section = struct.unpack_from('<10I', elf, offset + index * size)
        if section[1] != 2:
            continue
        strings = struct.unpack_from('<10I', elf, offset + section[6] * size)
        names = elf[strings[4]:strings[4] + strings[5]]
        for pos in range(section[4], section[4] + section[5], section[9]):
            name, value, _, _, _, _ = struct.unpack_from('<IIIBBH', elf, pos)
            symbols[names[name:].split(b'\0', 1)[0].decode()] = value
    return symbols


class ColdConsoleTests(unittest.TestCase):
    def test_real_handler_and_parser_on_RV32(self):
        RUN.mkdir(parents=True, exist_ok=True)
        source = SOURCE.read_text()
        handler = extract(source, 'bool __attribute__((noinline)) SerialDebug::handleCold(')
        parser = extract(source, 'bool parseUnsignedStrict(')
        transport = TRANSPORT.read_text().replace('inline bool uartAllowed(', 'inline bool productionUartAllowed(')
        case_list = vectors()
        branches = []
        for index, case in enumerate(case_list):
            values = [json.dumps(case['line']) if case['line'] is not None else 'nullptr',
                str(case['handled']).lower(), str(case['action']),
                *[str(case.get(k, default)).lower() for k, default in [('uart', False), ('deny', False),
                    ('busy', False), ('backend', True), ('unchanged', False), ('framed', False)]],
                str(case.get('page', -1)), str(case.get('bind_length', -1)),
                json.dumps(case['detail']) if 'detail' in case else 'nullptr']
            branches.append(f'if(id=={index})return check(' + ','.join(values) + ');')
        driver = '\nextern "C" int run_case(int id){' + ''.join(branches) + 'return 999;}\n'
        driver += f'extern "C" {{volatile int test_results[{len(case_list)}];void __attribute__((noinline,used)) test_done(){{for(;;){{asm volatile("");}}}}void _start(){{for(int i=0;i<{len(case_list)};++i)test_results[i]=run_case(i);test_done();}}}}'
        prefix = '#define MOSAICO_BOARD 1\n#define MOSAICO_NAND_STORE 1\n#define CONFIG_IDF_TARGET_ESP32S31 1\n'
        code = prefix + SUPPORT + transport + '\nnamespace serial_debug_transport {inline bool uartAllowed(const char* c){return !forceUartDeny&&productionUartAllowed(c);}}\n' + parser + '\n' + handler + CHECK + driver
        with tempfile.TemporaryDirectory(prefix='cold-console-', dir=RUN) as temporary:
            temp = Path(temporary)
            for name in ('cstdint', 'cstddef', 'array', 'cstring'):
                (temp / name).write_text(HEADERS[name])
            translation = temp / 'console.cpp'
            translation.write_text(code)
            executable = RUN / 'console.elf'
            command = [str(CLANG), '--target=riscv32-unknown-elf', '-march=rv32im', '-mabi=ilp32',
                '-std=c++20', '-O1', '-fstack-usage', '-ffunction-sections', '-fdata-sections', '-fno-builtin',
                '-fno-exceptions', '-fno-rtti', '-nostdinc++', '-nostdlib', '-I' + str(temp), str(translation),
                '--ld-path=' + str(CLANG.parent / 'ld.lld.exe'), '-Wl,--gc-sections', '-Wl,-e,_start', '-Wl,-Ttext=0x10000', '-o', str(executable)]
            result = subprocess.run(command, capture_output=True, text=True, env={**os.environ, 'TMP': str(temp), 'TEMP': str(temp)})
            (RUN / 'compile.log').write_text(result.stdout + result.stderr)
            self.assertEqual(result.returncode, 0, str(RUN / 'compile.log'))
        package = ROOT / '.artifacts/tools/unicorn-2.1.4-private-001/package'
        sys.path.insert(0, str(package))
        from unicorn import Uc, UC_ARCH_RISCV, UC_MODE_RISCV32
        from unicorn.riscv_const import UC_RISCV_REG_SP, UC_RISCV_REG_PC
        elf = executable.read_bytes()
        symbols = elf_symbols(elf)
        emulator = Uc(UC_ARCH_RISCV, UC_MODE_RISCV32)
        emulator.mem_map(0, 32 * 1024 * 1024)
        offset = struct.unpack_from('<I', elf, 28)[0]
        size, count = struct.unpack_from('<HH', elf, 42)
        for index in range(count):
            kind, position, address, _, file_size, _, _, _ = struct.unpack_from('<8I', elf, offset + index * size)
            if kind == 1:
                emulator.mem_write(address, elf[position:position + file_size])
        emulator.reg_write(UC_RISCV_REG_SP, 0x1fffff0)
        emulator.emu_start(struct.unpack_from('<I', elf, 24)[0], symbols['test_done'], timeout=20000000, count=100000000)
        self.assertEqual(emulator.reg_read(UC_RISCV_REG_PC), symbols['test_done'])
        values = struct.unpack('<' + str(len(case_list)) + 'i', bytes(emulator.mem_read(symbols['test_results'], 4 * len(case_list))))
        report = dict(source_sha256=hashlib.sha256(source.encode()).hexdigest(), handler_sha256=hashlib.sha256(handler.encode()).hexdigest(),
            transport_sha256=hashlib.sha256(TRANSPORT.read_bytes()).hexdigest(), elf_sha256=hashlib.sha256(elf).hexdigest(),
            case_count=len(case_list), cases=[dict(index=i, name=case['name'], result=values[i]) for i, case in enumerate(case_list)],
            limitations='RV32 actual function body; bounded Cold/NAND/libc mocks; not hardware/SDK/physical-trial validation')
        (RUN / 'execution.json').write_text(json.dumps(report, indent=2))
        self.assertEqual(values, (0,) * len(case_list), str(RUN / 'execution.json'))

    def test_source_scope_and_existing_transport_unchanged_contract(self):
        source = SOURCE.read_text()
        body = extract(source, 'bool __attribute__((noinline)) SerialDebug::handleCold(')
        self.assertIn('serial_debug_transport::uartAllowed("gauge-cold")', body)
        self.assertIn('parseUnsignedStrict(argument,0,4,index)', body)
        self.assertIn('std::strlen(argument)<=356', body)
        self.assertLess(body.index('NandBackend::busy()'), body.index('::strtok_r(line'))
        self.assertLess(body.index('if(!valid||'), body.index('MosaicoGaugeCold::status'))


if __name__ == '__main__':
    unittest.main()
