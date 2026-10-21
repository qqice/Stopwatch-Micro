"""Execute the extracted production console parser; no device or private gauge data."""
import tempfile,subprocess,struct,sys,unittest,os
from pathlib import Path
import test_mosaico_gauge_scale_backend as base
ROOT=base.ROOT
class ParserTests(unittest.TestCase):
 def test_production_parser(self):
  source=(ROOT/'main/debug/serial_debug.cpp').read_text()
  parser=source.split('    if (std::strcmp(command, "gauge-native-recovery") == 0) {',1)[1].split('    if (std::strcmp(command, "gauge-scale") == 0)',1)[0]
  parser='if (std::strcmp(command, "gauge-native-recovery") == 0) {'+parser
  code=r'''
#include <cstdint>
#include <cstddef>
#include <cstring>
static int evidenceCalls,resolveCalls,pass;static uint32_t seen;
struct Hal {bool gaugeNativeRecoveryEvidence(char*,size_t){++evidenceCalls;return true;}bool gaugeNativeRecoveryResolve(uint32_t c,char*,size_t){++resolveCalls;seen=c;return true;}};
static Hal hal;Hal& GetHAL(){return hal;}
void result(const char*,const char* status,const char*){pass=!std::strcmp(status,"PASS");}
char* strtok_r(char* value,const char*,char** save){char* p=value?value:*save;if(!p)return nullptr;while(*p==' '||*p=='\t')++p;if(!*p){*save=p;return nullptr;}char* begin=p;while(*p&&*p!=' '&&*p!='\t')++p;if(*p)*p++=0;*save=p;return begin;}
void parse(char* save){const char* command="gauge-native-recovery";
'''+parser+r'''}
extern "C" volatile int test_results[12];volatile int test_results[12];
extern "C" __attribute__((noinline,used)) void test_done(){for(;;){asm volatile("");}}
extern "C" void _start(){
 const char* cases[]={"evidence","resolve CONFIRM 91336fc0","resolve CONFIRM ABCDEF01","resolve CONFIRM 00000000","","evidence extra","resolve 91336fc0","resolve CONFIRM 1234567","resolve CONFIRM 123456789","resolve CONFIRM 0x123456","resolve CONFIRM 91336fg0","resolve CONFIRM 91336fc0 extra"};
 for(int i=0;i<12;++i){char buf[96];size_t n=std::strlen(cases[i]);std::memcpy(buf,cases[i],n+1);evidenceCalls=resolveCalls=pass=0;seen=0;parse(buf);
 bool ok=i==0?(evidenceCalls==1&&resolveCalls==0&&pass):i<4?(resolveCalls==1&&evidenceCalls==0&&pass&&seen==(i==1?0x91336fc0U:i==2?0xabcdef01U:0)):(!resolveCalls&&!evidenceCalls&&!pass);test_results[i]=ok?0:1;
 }test_done();}
'''
  code+=base.MOCKS.split('static Hal hal;',1)[0]
  # No formatting is exercised; drop unused snprintf's compiler-runtime dependencies.
  with tempfile.TemporaryDirectory(prefix='.native-parser-',dir=ROOT) as temp:
   td=Path(temp)
   for name,text in base.HEADERS.items():(td/name).write_text(text)
   src=td/'parser.cpp';src.write_text(code);elf=td/'parser.elf'
   args=[str(base.CLANG),'--target=riscv32-unknown-elf','-march=rv32im','-mabi=ilp32','-std=c++20','-O1','-ffunction-sections','-fdata-sections','-fno-builtin','-fno-exceptions','-fno-rtti','-nostdinc++','-nostdlib','-I'+str(td),str(src),'--ld-path='+str(base.CLANG.parent/'ld.lld.exe'),'-Wl,--gc-sections','-Wl,-e,_start','-Wl,-Ttext=0x10000','-o',str(elf)]
   run=subprocess.run(args,capture_output=True,text=True,env={**os.environ,"TMP":str(td),"TEMP":str(td)});self.assertEqual(run.returncode,0,run.stderr)
   raw=elf.read_bytes()
  sys.path.insert(0,str(ROOT/'.artifacts/tools/unicorn-2.1.4-private-001/package'))
  from unicorn import Uc,UC_ARCH_RISCV,UC_MODE_RISCV32
  from unicorn.riscv_const import UC_RISCV_REG_SP,UC_RISCV_REG_PC
  uc=Uc(UC_ARCH_RISCV,UC_MODE_RISCV32);uc.mem_map(0,32*1024*1024)
  entry=struct.unpack_from('<I',raw,24)[0];ph=struct.unpack_from('<I',raw,28)[0];psz,pnum=struct.unpack_from('<HH',raw,42)
  for i in range(pnum):
   kind,off,addr,_,size,_,_,_=struct.unpack_from('<8I',raw,ph+i*psz)
   if kind==1:uc.mem_write(addr,raw[off:off+size])
  sh=struct.unpack_from('<I',raw,32)[0];ss,sn=struct.unpack_from('<HH',raw,46);symbols={}
  for i in range(sn):
   sec=struct.unpack_from('<10I',raw,sh+i*ss)
   if sec[1]!=2:continue
   st=struct.unpack_from('<10I',raw,sh+sec[6]*ss);text=raw[st[4]:st[4]+st[5]]
   for p in range(sec[4],sec[4]+sec[5],sec[9]):
    name,value,_,_,_,_=struct.unpack_from('<IIIBBH',raw,p);symbols[text[name:].split(b'\0',1)[0].decode()]=value
  uc.reg_write(UC_RISCV_REG_SP,0x1fffff0);uc.emu_start(entry,symbols['test_done'],timeout=10000000,count=1000000)
  self.assertEqual(uc.reg_read(UC_RISCV_REG_PC),symbols['test_done']);self.assertEqual(struct.unpack('<12i',bytes(uc.mem_read(symbols['test_results'],48))),(0,)*12)
if __name__=='__main__':unittest.main()
