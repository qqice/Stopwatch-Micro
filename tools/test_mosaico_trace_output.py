"""Compile-time execution of actual trace sink with bounded transport mocks."""
from pathlib import Path
import os
import shutil
import subprocess
import tempfile
import unittest

ROOT=Path(__file__).resolve().parents[1]
SOURCE=(ROOT/'main/debug/serial_debug.cpp').read_text(encoding='utf-8-sig')
COMPILER=os.environ.get('CXX') or shutil.which('g++') or shutil.which('clang++') or 'C:/Espressif/tools/riscv32-esp-elf/esp-15.2.0_20251204/riscv32-esp-elf/bin/riscv32-esp-elf-g++.exe'


def sink_function():
    start=SOURCE.index('bool SerialDebug::debugTraceLine(')
    opening=SOURCE.index('{',start);end=opening+1;depth=1
    while depth:
        depth+=(SOURCE[end]=='{')-(SOURCE[end]=='}');end+=1
    # Body and calls are actual production code. Only static local storage is
    # automatic in the constexpr fixture so each isolated scenario owns it.
    return SOURCE[start:end].replace('bool SerialDebug::debugTraceLine(', 'constexpr bool debugTraceLine(').replace('static std::mutex mutex;', 'std::mutex mutex;').replace('static char bytes[642];', 'char bytes[642];')


def fixture():
    return r'''
#include <cstddef>
struct Stream {
 char last[642]{};std::size_t calls=0,total=0,lastSize=0;
 bool partial=false,reject=false;
};
namespace std {
 struct mutex {};
 template<class T>struct lock_guard {constexpr explicit lock_guard(T&){} };
 constexpr const char* memchr(const char* p,int c,size_t n){for(size_t i=0;i<n;++i)if(p[i]==c)return p+i;return nullptr;}
 constexpr char* memcpy(char* out,const char* p,size_t n){for(size_t i=0;i<n;++i)out[i]=p[i];return out;}
 constexpr size_t fwrite(const char* p,size_t size,size_t n,Stream* s){
  ++s->calls;s->lastSize=size*n;
  const size_t accepted=s->partial && n?n-1:n;s->total+=size*accepted;
  for(size_t i=0;i<size*accepted;++i)s->last[i]=p[i];return accepted;
 }
}
constexpr std::size_t strnlen(const char* p,std::size_t n){std::size_t i=0;while(i<n && p[i])++i;return i;}
struct Tx {
 Stream stream;
 constexpr bool enqueue(const char* p,std::size_t n){
  if(stream.reject)return false;
  return std::fwrite(p,1,n,&stream)==n;
 }
};
struct Writer {bool _reply_uart=false;unsigned _format_overflow=0;Tx _uart_tx;};
struct Fixture {
 Writer writer;Writer* _writer=&writer;Stream stream;Stream* stdout=&stream;
''' + sink_function() + r'''
};
constexpr bool exact(const Stream& s,const char* text,std::size_t n){
 if(s.lastSize!=n+2 || s.last[n]!='\r' || s.last[n+1]!='\n')return false;
 for(std::size_t i=0;i<n;++i)if(s.last[i]!=text[i])return false;return true;
}
constexpr bool literalAndLimits(){
 Fixture f;
 if(!f.debugTraceLine("literal %s %n %%"))return false;
 if(!exact(f.stream,"literal %s %n %%",16))return false;
 if(!f.debugTraceLine("")||!exact(f.stream,"",0))return false;
 const auto calls=f.stream.calls;
 if(f.debugTraceLine(nullptr)||f.stream.calls!=calls)return false;
 char max[640]{};for(unsigned i=0;i<639;++i)max[i]='x';
 if(!f.debugTraceLine(max)||!exact(f.stream,max,639))return false;
 max[639]='x';if(f.debugTraceLine(max)||f.stream.calls!=calls+1)return false;
 if(f.debugTraceLine("line\rbreak")||f.debugTraceLine("line\nbreak"))return false;
 return f.writer._format_overflow==3 && f.stream.calls==calls+1;
}
constexpr bool outputFailures(){
 Fixture f;f.stream.partial=true;
 if(f.debugTraceLine("payload")||f.stream.total!=8)return false;
 Fixture uart;uart.writer._reply_uart=true;uart.writer._uart_tx.stream.reject=true;
 if(uart.debugTraceLine("payload")||uart.stream.calls||uart.writer._uart_tx.stream.calls)return false;
 uart.writer._uart_tx.stream.reject=false;
 if(!uart.debugTraceLine("%literal")||!exact(uart.writer._uart_tx.stream,"%literal",8)||uart.stream.calls)return false;
 // A callback failure latches false and cannot be overwritten by later successes.
 bool emitted=true;
 const auto callback=[&](const char* line){if(!f.debugTraceLine(line))emitted=false;};
 callback("failed");f.stream.partial=false;callback("succeeded");
 return !emitted;
}
constexpr bool boundedStream(){
 Fixture f;
 if(!f.debugTraceLine("status")||!f.debugTraceLine("schema"))return false;
 char row[640]{};for(unsigned j=0;j<639;++j)row[j]='a';
 for(unsigned i=0;i<64;++i){row[0]=char('A'+i%26);if(!f.debugTraceLine(row)||!exact(f.stream,row,639))return false;}
 return f.stream.calls==66 && f.stream.total==16+64*641;
}
static_assert(literalAndLimits(),"literal percent CRLF empty null 639 640 embedded newline");
static_assert(outputFailures(),"partial CDC failure UART queue rejection latched output failure");
static_assert(boundedStream(),"64 max-length rows plus two headers without truncation");
'''


class TraceOutputTests(unittest.TestCase):
    def test_actual_sink_faults(self):
        with tempfile.TemporaryDirectory() as td:
            source=Path(td)/'trace-output.cpp';source.write_text(fixture())
            result=subprocess.run([COMPILER,'-std=c++20','-fconstexpr-ops-limit=200000000','-fsyntax-only',str(source)],capture_output=True,text=True)
            log=ROOT/'.artifacts/mosaico/gauge-trace-output-tests-compile.log'
            log.parent.mkdir(parents=True,exist_ok=True);log.write_text(result.stdout+result.stderr)
            self.assertEqual(result.returncode,0,str(log))

    def test_handler_routes_and_latches_failure(self):
        handler=SOURCE.split('if (std::strcmp(command,"gauge-trace")==0)',1)[1].split('if (std::strcmp(command,"gauge-thresholds")==0)',1)[0]
        self.assertIn('bool emitted=true;',handler)
        self.assertIn('if(!SerialDebug::debugTraceLine(line))*static_cast<bool*>(context)=false;',handler)
        self.assertIn('GaugeDischargeTrace::status(emit,&emitted)',handler)
        self.assertIn('GaugeDischargeTrace::exportRows(emit,&emitted)',handler)
        self.assertEqual(handler.count('emitted?"PASS":"FAIL"'),2)
        export=handler.split('if(!std::strcmp(action,"export"))',1)[1].split('bool queued=',1)[0]
        self.assertLess(export.index('_reply_uart'),export.index('GaugeDischargeTrace::exportRows'))
        self.assertIn('reason=bulk_export_cdc_only data_retained=1',export)
        self.assertIn('reason=trace_output_failed data_retained=1',export)
        for forbidden in ('debugPrintf(', 'nvs_', 'clearConfirm()'):
            self.assertNotIn(forbidden,export)
        sink=sink_function()
        self.assertNotIn('snprintf',sink)
        self.assertNotIn('debugPrintf',sink)
        self.assertIn('std::fwrite(bytes, 1, length + 2, stdout) == length + 2',sink)

if __name__=='__main__':
    unittest.main()
