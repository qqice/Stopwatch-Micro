import os,shutil,subprocess,tempfile,unittest
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
def harness():
 s=(ROOT/'main/debug/mosaico_rx_observer.cpp').read_text();actual=s[s.index('extern "C" void mosaico_wg_tx_trace'):s.index('#endif // ESP_PLATFORM')]
 return r'''#include "main/debug/mosaico_rx_observer.h"
#include <cassert>
#define portENTER_CRITICAL(p) ((void)(p))
#define portEXIT_CRITICAL(p) ((void)(p))
namespace mosaico_rx_observer { Snapshot state;detail::MetadataRing ring;int mux;uint32_t fakeNow;uint32_t nowMs(){return fakeNow;} }
'''+actual+r'''
int main(){using namespace mosaico_rx_observer;
 fakeNow=999;mosaico_wg_tx_trace(1,1,2,3,4,5,6,0);assert(!ring.count&&!state.txTrace[1]);
 mosaico_derp_trace(2,19,-80,104,3,4);assert(!state.derpEvents&&!ring.count);
 state.active=true;state.deadlineMs=1000;
 mosaico_derp_trace(0,0,0,0,0,0);mosaico_derp_trace(7,0,0,0,0,0);mosaico_derp_trace(1,20,0,0,0,0);assert(!state.derpEvents&&!ring.count);
 mosaico_derp_trace(2,19,-80,104,3,4);Record d;assert(ring.pop(&d,1)==1&&d.tracePhase==9&&d.protocol==2&&d.tcpFlags==19&&d.traceResult==-80&&int32_t(d.ack)==104&&int32_t(d.seq)==4&&d.receiverIndex==3);
 mosaico_wg_tx_trace(0,0,0,0,0,0,0,0);mosaico_wg_tx_trace(9,0,0,0,0,0,0,0);assert(!ring.count);
 mosaico_wg_tx_trace(2,123,0xffffffffU,0xfedcba98U,40000,8765,123456,-11);
 Record r;assert(ring.pop(&r,1)==1&&r.tracePhase==2&&r.receiverIndex==123&&r.counter==0xfedcba98ffffffffULL&&r.srcPort==40000&&r.dstPort==8765&&r.seq==123456&&r.traceResult==-11);
 mosaico_wg_tx_trace(8,0,3,7,0,8765,0x64616b16,0);assert(ring.pop(&r,1)==1&&r.tracePhase==8&&uint32_t(r.counter)==3&&(r.counter>>32)==7&&r.seq==0x64616b16&&r.traceResult==0);
 mosaico_derp_trace(2,5,-1234,-5,7,-1);assert(ring.pop(&d,1)==1&&d.traceResult==-1234&&int32_t(d.ack)==-5&&int32_t(d.seq)==-1);
 fakeNow=1000;mosaico_wg_tx_trace(2,0,0,0,0,0,0,0);assert(!ring.count&&state.txTrace[2]==1);
 fakeNow=0xfffffff0U;state.deadlineMs=0x20;
 for(unsigned i=0;i<129;++i)mosaico_wg_tx_trace(3,0,i,0,0,0,0,0);
 assert(ring.count==128&&state.txTrace[3]==129&&state.txRingDrops==1&&state.ringDrops==0);
 mosaico_derp_trace(4,17,0,0,3,-1);assert(state.derpEvents==3&&state.derpRingDrops==1&&state.txRingDrops==1);
 assert(ring.discardQueued()==128&&ring.count==0);state.controlOnly=true;fakeNow=10;state.deadlineMs=1000;
 mosaico_wg_tx_trace(1,0,0,0,1,2,3,0);assert(!ring.count);
 mosaico_derp_trace(1,2,0,0,7,3);assert(ring.count==1);assert(ring.pop(&d,1)==1&&d.tracePhase==9);
 state.controlOnly=false;fakeNow=0x20;state.deadlineMs=0x20;mosaico_derp_trace(4,17,0,0,3,-1);assert(state.derpEvents==4);mosaico_wg_tx_trace(3,0,0,0,0,0,0,0);assert(state.txTrace[3]==129);
}
'''
class Sink(unittest.TestCase):
 def compile(self,cc,run):
  with tempfile.TemporaryDirectory() as d:
   p=Path(d)/'sink.cpp';exe=Path(d)/'sink';p.write_text(harness())
   cmd=[cc,'-std=c++17','-Wall','-Wextra','-Werror','-I'+str(ROOT),str(p)]
   cmd+=['-o',str(exe)] if run else ['-fsyntax-only']
   subprocess.run(cmd,check=True)
   if run:subprocess.run([str(exe)],check=True)
 def test_actual_sink_target_syntax(self):
  cc=next(iter(Path.home().glob('.espressif/tools/riscv32-esp-elf/*/riscv32-esp-elf/bin/riscv32-esp-elf-g++.exe')),None)
  if not cc:self.skipTest('installed target compiler unavailable')
  self.compile(str(cc),False)
 def test_actual_sink_native(self):
  cc=os.environ.get('TX_SINK_CXX') or shutil.which('clang++') or shutil.which('c++')
  if not cc:self.skipTest('native host compiler unavailable')
  self.compile(cc,True)
if __name__=='__main__':unittest.main()

