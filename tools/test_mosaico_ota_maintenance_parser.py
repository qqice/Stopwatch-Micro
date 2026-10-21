"""Execute the extracted production maintenance OTA console parser; no device or private gauge data."""
import tempfile,subprocess,struct,sys,unittest,os
from pathlib import Path
import test_mosaico_gauge_scale_backend as base
ROOT=base.ROOT
class MaintenanceParserTests(unittest.TestCase):
 def test_production_parser(self):
  source=(ROOT/'main/debug/serial_debug.cpp').read_text()
  parser='if (command && !std::strcmp(command, "ota-maintenance")) {'+source.split('    if (command && !std::strcmp(command, "ota-maintenance")) {',1)[1].split('#if CONFIG_IDF_TARGET_ESP32S31',1)[0]
  unsigned='bool parseUnsignedStrict'+source.split('bool parseUnsignedStrict',1)[1].split('#ifdef MOSAICO_BOARD',1)[0]
  code='\n#include <cstdint>\n#include <cstddef>\n#include <cstring>\n#include <cstdio>\nstatic int powerCalls;struct FakeHal{bool maintenancePowerStatus(char*,size_t)const{++powerCalls;return true;}};static FakeHal hal;FakeHal& GetHAL(){return hal;}static int statusCalls,updateCalls,rebootCalls,pass,errno,wakeCalls;struct Network{void wakeForFirmwareUpdate(){++wakeCalls;}};static Network network;Network& GetNetworkQuota(){return network;}static bool accepted=true,statusResult=true;static char seen[65];static uint32_t seenTtl;\nenum class AsyncTest {None,Other};static AsyncTest _async_test=AsyncTest::None;\nnamespace std {unsigned long strtoul(const char* s,char** end,int){unsigned long v=0;const char* p=s;for(;*p>=\'0\'&&*p<=\'9\';++p){unsigned d=unsigned(*p-\'0\');if(v>(0xffffffffU-d)/10)errno=1;v=v*10+d;}*end=const_cast<char*>(p);return v;}}\nnamespace MosaicoOta {bool maintenanceStatus(char*,size_t){++statusCalls;return statusResult;}bool requestMaintenanceUpdate(const char* h,uint32_t t){++updateCalls;std::memcpy(seen,h,65);seenTtl=t;return accepted;}bool requestMaintenanceReboot(const char* h,uint32_t t){++rebootCalls;std::memcpy(seen,h,65);seenTtl=t;return accepted;}}\nvoid result(const char*,const char* status,const char*){pass=!std::strcmp(status,"PASS");}\nchar* strtok_r(char* value,const char*,char** save){char* p=value?value:*save;if(!p)return nullptr;while(*p==\' \'||*p==\'\\t\')++p;if(!*p){*save=p;return nullptr;}char* begin=p;while(*p&&*p!=\' \'&&*p!=\'\\t\')++p;if(*p)*p++=0;*save=p;return begin;}\n'+unsigned+'void parse(char* save){const char* command="ota-maintenance";'+parser+'}\n'
  code+='extern "C" volatile int test_results[24];volatile int test_results[24];\nextern "C" __attribute__((noinline,used)) void test_done(){for(;;){asm volatile("");}}\nextern "C" void _start(){\nconst char* cases[]={"","status","status extra","update CONFIRM_USB_POWER a1a1a1a1a1a1a1a1a1a1a1a1a1a1a1a1a1a1a1a1a1a1a1a1a1a1a1a1a1a1a1a1 30","update CONFIRM_USB_POWER A1A1A1A1A1A1A1A1A1A1A1A1A1A1A1A1A1A1A1A1A1A1A1A1A1A1A1A1A1A1A1A1 600","reboot CONFIRM_USB_POWER a1a1a1a1a1a1a1a1a1a1a1a1a1a1a1a1a1a1a1a1a1a1a1a1a1a1a1a1a1a1a1a1 45","update CONFIRM_EXTERNAL_POWER a1a1a1a1a1a1a1a1a1a1a1a1a1a1a1a1a1a1a1a1a1a1a1a1a1a1a1a1a1a1a1a1 30","update CONFIRM_USB_POWER a1a1a1a1a1a1a1a1a1a1a1a1a1a1a1a1a1a1a1a1a1a1a1a1a1a1a1a1a1a1a1a 30","update CONFIRM_USB_POWER a1a1a1a1a1a1a1a1a1a1a1a1a1a1a1a1a1a1a1a1a1a1a1a1a1a1a1a1a1a1a1a1f 30","update CONFIRM_USB_POWER a1a1a1a1a1a1a1a1a1a1a1a1a1a1a1a1a1a1a1a1a1a1a1a1a1a1a1a1a1a1a1ag 30","update CONFIRM_USB_POWER a1a1a1a1a1a1a1a1a1a1a1a1a1a1a1a1a1a1a1a1a1a1a1a1a1a1a1a1a1a1a1a1 29","update CONFIRM_USB_POWER a1a1a1a1a1a1a1a1a1a1a1a1a1a1a1a1a1a1a1a1a1a1a1a1a1a1a1a1a1a1a1a1 601","update CONFIRM_USB_POWER a1a1a1a1a1a1a1a1a1a1a1a1a1a1a1a1a1a1a1a1a1a1a1a1a1a1a1a1a1a1a1a1 -1","update CONFIRM_USB_POWER a1a1a1a1a1a1a1a1a1a1a1a1a1a1a1a1a1a1a1a1a1a1a1a1a1a1a1a1a1a1a1a1 4294967296","update CONFIRM_USB_POWER a1a1a1a1a1a1a1a1a1a1a1a1a1a1a1a1a1a1a1a1a1a1a1a1a1a1a1a1a1a1a1a1 30 extra","update CONFIRM_USB_POWER a1a1a1a1a1a1a1a1a1a1a1a1a1a1a1a1a1a1a1a1a1a1a1a1a1a1a1a1a1a1a1a1","reboot","bogus","update CONFIRM_USB_POWER a1a1a1a1a1a1a1a1a1a1a1a1a1a1a1a1a1a1a1a1a1a1a1a1a1a1a1a1a1a1a1a1 30","reboot CONFIRM_USB_POWER a1a1a1a1a1a1a1a1a1a1a1a1a1a1a1a1a1a1a1a1a1a1a1a1a1a1a1a1a1a1a1a1 30","update CONFIRM_USB_POWER a1a1a1a1a1a1a1a1a1a1a1a1a1a1a1a1a1a1a1a1a1a1a1a1a1a1a1a1a1a1a1a1 30","status","power","power extra"};\nint kinds[]={1,1,0,2,2,3,0,0,0,0,0,0,0,0,0,0,0,0,0,0,2,1,4,0};\nint asyncs[]={0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,1,1,0,0,0,0};\nint accepts[]={1,1,1,1,1,1,1,1,1,1,1,1,1,1,1,1,1,1,1,1,0,1,1,1};\nint statuses[]={1,1,1,1,1,1,1,1,1,1,1,1,1,1,1,1,1,1,1,1,1,0,1,1};\nfor(int i=0;i<24;++i){char buf[192];std::memcpy(buf,cases[i],std::strlen(cases[i])+1);statusCalls=updateCalls=rebootCalls=pass=wakeCalls=powerCalls=0;seen[0]=0;seenTtl=0;accepted=accepts[i];statusResult=statuses[i];_async_test=asyncs[i]?AsyncTest::Other:AsyncTest::None;parse(buf);bool ok=false;switch(kinds[i]){case 0:ok=!pass&&!statusCalls&&!updateCalls&&!rebootCalls;break;case 1:ok=pass==statuses[i]&&statusCalls==1&&!updateCalls&&!rebootCalls;break;case 2:ok=pass==accepts[i]&&updateCalls==1&&!rebootCalls&&statusCalls==1;break;case 3:ok=pass==accepts[i]&&rebootCalls==1&&!updateCalls&&statusCalls==1;break;case 4:ok=pass&&powerCalls==1&&!statusCalls&&!updateCalls&&!rebootCalls;break;}if(kinds[i]!=4)ok=ok&&!powerCalls;if(kinds[i]==2||kinds[i]==3)ok=ok&&!std::strcmp(seen,"a1a1a1a1a1a1a1a1a1a1a1a1a1a1a1a1a1a1a1a1a1a1a1a1a1a1a1a1a1a1a1a1")&&seenTtl>=30&&seenTtl<=600;ok=ok&&(wakeCalls==(((kinds[i]==2||kinds[i]==3)&&accepts[i])?1:0));test_results[i]=ok?0:1;}test_done();}\n'
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
  self.assertEqual(uc.reg_read(UC_RISCV_REG_PC),symbols['test_done']);self.assertEqual(struct.unpack('<24i',bytes(uc.mem_read(symbols['test_results'],96))),(0,)*24)
 def test_handler_transport_boundary(self):
  src=(ROOT/'main/debug/serial_debug.cpp').read_text()
  self.assertLess(src.index('std::strcmp(command, "ota-maintenance")'),src.index('if (MosaicoOta::busy()'))
  self.assertIn('"ota-maintenance"',(ROOT/'main/debug/serial_debug_transport.h').read_text())
if __name__=='__main__':unittest.main()
