"""Bounded journal model/adapter host compilation tests; no device or firmware build."""
from pathlib import Path
import importlib.util
import re
import subprocess
import tempfile
import unittest

ROOT=Path(__file__).resolve().parents[1]
spec=importlib.util.spec_from_file_location('trace_tests',ROOT/'tools/test_mosaico_nand_store.py')
trace=importlib.util.module_from_spec(spec);spec.loader.exec_module(trace)

MODEL=r'''
#include "main/hal/mosaico/mosaico_nand_log_model.h"
namespace L=MosaicoNandStore::LogModel;
namespace M=MosaicoNandStore::Model;
using L::Page;using L::State;using L::Code;
struct FTL {
 struct Entry {uint32_t address=UINT32_MAX;Page page{};};
 std::array<Entry,16> durable{},current{};
 int calls=0,failAt=0;bool failAfter=false;unsigned writes=0,syncs=0;
 constexpr FTL(){Page p;M::marker(p);set(0,p);set(1,p);for(unsigned i=2;i<8;++i){p={};p[0]=uint8_t(i);set(i,p);}durable=current;}
 constexpr void set(uint32_t address,const Page& p){for(auto& e:current)if(e.address==address){e.page=p;return;}for(auto& e:current)if(e.address==UINT32_MAX){e={address,p};return;}}
 constexpr bool before(){return ++calls!=failAt||failAfter;}
 constexpr bool after()const{return calls!=failAt;}
 constexpr bool read(uint32_t address,Page& p){if(!before())return false;for(auto& b:p)b=255;for(auto& e:current)if(e.address==address){p=e.page;break;}return after();}
 constexpr bool write(uint32_t address,const Page& p){++writes;if(!before())return false;set(address,p);return after();}
 constexpr bool sync(){++syncs;if(!before())return false;durable=current;return after();}
 constexpr void cut(){current=durable;calls=0;failAt=0;}
 constexpr bool oldEqual(const FTL& other)const{for(unsigned i=0;i<8;++i)if(durable[i].page!=other.durable[i].page)return false;return true;}
};
constexpr Page input(){Page p{};L::put(p,16,71,4);L::put(p,20,1,2);p[60]=42;return p;}
constexpr void seed(FTL& f,uint64_t seq,int copy=0){Page p=input();L::finalize(p,seq);f.set(L::address(seq),p);L::index(p,seq);f.set(L::IndexBase+copy,p);f.durable=f.current;}
constexpr bool roundtrip(){FTL f;const auto before=f;M::Scratch s;State state;Page p=input();
 if(L::load(f,s,state)!=Code::Empty||f.writes)return false;
 if(L::append(f,s,p,state)!=Code::Ok||state.latestSeq!=1||state.oldestSeq!=1)return false;
 f.cut();State recovered;if(L::load(f,s,recovered)!=Code::Ok||recovered.latestSeq!=1)return false;
 Page out{};if(L::read(f,s,1,out)!=Code::Ok||out!=p)return false;
 if(L::get(out,16,4)!=71||L::get(out,20,2)!=1||out[60]!=42||!f.oldEqual(before))return false;
 out[80]^=1;if(L::validPage(out))return false;
 L::finalize(out,1);out[22]=1;L::put(out,2044,L::crc(out,2044),4);if(L::validPage(out))return false;
 return true;
}
constexpr bool appendFaults(){FTL initial;seed(initial,1);M::Scratch s;State state;
 if(L::load(initial,s,state)!=Code::Ok)return false;
 FTL count=initial;count.calls=0;Page p=input();State check=state;if(L::append(count,s,p,check)!=Code::Ok)return false;
 for(int point=1;point<=count.calls;++point)for(int after=0;after<2;++after){
  FTL f=initial;f.calls=0;f.failAt=point;f.failAfter=after;State now=state;p=input();
  if(L::append(f,s,p,now)==Code::Ok)return false;if(!f.oldEqual(initial))return false;
  f.cut();State recovered;if(L::load(f,s,recovered)!=Code::Ok||(recovered.latestSeq!=1&&recovered.latestSeq!=2))return false;
  const auto writes=f.writes,syncs=f.syncs;if(L::load(f,s,recovered)!=Code::Ok||f.writes!=writes||f.syncs!=syncs)return false;
  p=input();if(L::append(f,s,p,recovered)!=Code::Ok)return false;f.cut();
  if(L::load(f,s,now)!=Code::Ok||now.latestSeq!=recovered.latestSeq)return false;
 }
 return true;
}
constexpr bool loadFaults(){FTL initial;seed(initial,1);M::Scratch s;State state;FTL count=initial;
 if(L::load(count,s,state)!=Code::Ok)return false;
 for(int point=1;point<=count.calls;++point)for(int after=0;after<2;++after){FTL f=initial;f.calls=0;f.failAt=point;f.failAfter=after;state.latestSeq=99;
  if(L::load(f,s,state)!=Code::Io||state.latestSeq!=99||f.writes||f.syncs)return false;
 }
 return true;
}
constexpr bool badRegions(){M::Scratch s;State state;Page p{};
 FTL f;f.set(L::DataBase,p);if(L::load(f,s,state)!=Code::Corrupt||f.writes)return false;
 f=FTL{};f.set(L::IndexBase,p);if(L::load(f,s,state)!=Code::Corrupt||f.writes)return false;
 f=FTL{};seed(f,1);p=input();L::finalize(p,2);p[60]^=1;f.set(L::address(2),p);
 if(L::load(f,s,state)!=Code::Ok||state.latestSeq!=1||state.adopted)return false;
 p=input();if(L::append(f,s,p,state)!=Code::Ok)return false;
 f=FTL{};seed(f,1);L::index(p,2);p[2044]^=1;f.set(L::IndexBase+1,p);
 if(L::load(f,s,state)!=Code::Ok||state.latestSeq!=1||state.adopted)return false;
 f=FTL{};seed(f,1);p=input();L::finalize(p,2);f.set(L::address(2),p);
 if(L::load(f,s,state)!=Code::Ok||state.latestSeq!=2||!state.adopted||f.writes)return false;
 // Unused remote unknown pages are not scanned; first attempted overwrite refuses.
 f=FTL{};seed(f,1);p={};f.set(L::address(3),p);state={2,1,0,false};p=input();
 if(L::append(f,s,p,state)!=Code::Corrupt||f.writes)return false;
 return true;
}
constexpr bool fullRing(){FTL f;M::Scratch s;State state;seed(f,L::MaxPages);Page p=input();L::finalize(p,1);f.set(L::address(1),p);f.durable=f.current;
 if(L::load(f,s,state)!=Code::Ok||state.oldestSeq!=1)return false;
 p=input();if(L::append(f,s,p,state)!=Code::Ok||state.latestSeq!=8192||state.oldestSeq!=2)return false;
 f.cut();if(L::load(f,s,state)!=Code::Ok)return false;
 p=input();if(L::append(f,s,p,state)!=Code::Ok||state.latestSeq!=8193||state.oldestSeq!=3)return false;
 f.cut();if(L::load(f,s,state)!=Code::Ok||state.latestSeq!=8193)return false;
 // Full-ring orphan requires metadata sync BEFORE overwriting the old committed oldest.
 FTL orphan;seed(orphan,L::MaxPages);p=input();L::finalize(p,8192);orphan.set(L::address(8192),p);p=input();L::finalize(p,1);orphan.set(L::address(1),p);orphan.durable=orphan.current;
 if(L::load(orphan,s,state)!=Code::Ok||!state.adopted||state.oldestSeq!=2)return false;
 FTL count=orphan;count.calls=0;State cstate=state;p=input();if(L::append(count,s,p,cstate)!=Code::Ok)return false;
 for(int point=1;point<=count.calls;++point)for(int after=0;after<2;++after){FTL crash=orphan;crash.calls=0;crash.failAt=point;crash.failAfter=after;cstate=state;p=input();
  if(L::append(crash,s,p,cstate)==Code::Ok)return false;crash.cut();State recovered;
  if(L::load(crash,s,recovered)!=Code::Ok||(recovered.latestSeq!=8192&&recovered.latestSeq!=8193))return false;
 }
 return true;
}
constexpr bool tornWrites(){
 M::Scratch scratch;Page page=input();L::finalize(page,2);
 for(unsigned extent: {28U,60U,128U,1024U,2047U}){
  FTL f;seed(f,1);Page torn;for(auto& b:torn)b=255;
  for(unsigned i=0;i<extent;++i)torn[i]=page[i];
  // Valid header includes zero reserved bytes through 59. Below header size stays unknown.
  f.set(L::address(2),torn);f.durable=f.current;State state;
  if(extent<60){if(L::load(f,scratch,state)!=Code::Corrupt)return false;continue;}
  if(L::load(f,scratch,state)!=Code::Ok||state.latestSeq!=1||state.adopted||f.writes)return false;
  Page replacement=input();if(L::append(f,scratch,replacement,state)!=Code::Ok)return false;
  f.cut();if(L::load(f,scratch,state)!=Code::Ok||state.latestSeq!=2)return false;
 }
 FTL blank;Page torn=input();L::finalize(torn,1);torn[80]^=1;blank.set(L::DataBase,torn);State state;
 if(L::load(blank,scratch,state)!=Code::Empty||blank.writes)return false;
 page=input();if(L::append(blank,scratch,page,state)!=Code::Ok)return false;
 // A committed tail with valid header but broken payload is never hidden.
 blank.cut();blank.read(L::DataBase,torn);torn[80]^=1;blank.set(L::DataBase,torn);
 return L::load(blank,scratch,state)==Code::Corrupt;
}
constexpr bool overflow(){FTL f;seed(f,UINT64_MAX);M::Scratch s;State state;Page p=input();if(L::load(f,s,state)!=Code::Ok||state.latestSeq!=UINT64_MAX)return false;
 return L::append(f,s,p,state)==Code::Overflow&&f.writes==0&&f.syncs==0;
}
'''

class JournalTests(unittest.TestCase):
    def compile(self,source,name):
        with tempfile.TemporaryDirectory() as td:
            path=Path(td)/'test.cpp';path.write_text(source)
            result=subprocess.run([trace.COMPILER,'-std=c++20','-fconstexpr-ops-limit=500000000','-fsyntax-only','-I',str(ROOT),str(path)],capture_output=True,text=True)
            log=ROOT/'.artifacts/mosaico'/('nand-log-'+name+'.log');log.parent.mkdir(parents=True,exist_ok=True);log.write_text(result.stdout+result.stderr)
            self.assertEqual(result.returncode,0,str(log))
    def test_model(self):
        assertions='\n'.join('static_assert('+name+'(),"'+name+'");' for name in ('roundtrip','appendFaults','loadFaults','badRegions','fullRing','tornWrites','overflow'))
        self.compile(MODEL+assertions,'model')
    def test_actual_adapter_syntax(self):
        header=(ROOT/'main/hal/mosaico/mosaico_nand_store.h').read_text()
        body=(ROOT/'main/hal/mosaico/mosaico_nand_store.cpp').read_text()
        header=re.sub(r'^#.*$', '',header,flags=re.M);body=re.sub(r'^#.*$', '',body,flags=re.M)
        self.compile('#include "main/hal/mosaico/mosaico_nand_log_model.h"\n'+trace.ADAPTER_SYNTAX_PREFIX+header+body,'adapter')

if __name__=='__main__':unittest.main()

