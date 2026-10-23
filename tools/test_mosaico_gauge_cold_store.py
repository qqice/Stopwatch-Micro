"""Offline storage fault tests; no ports, SDK changes, or NAND operations."""
from pathlib import Path
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
COMPILER = Path('C:/Espressif/tools/riscv32-esp-elf/esp-15.2.0_20251204/riscv32-esp-elf/bin/riscv32-esp-elf-g++.exe')
FIXTURE = r'''
#include "mosaico_gauge_cold_store_model.h"
namespace c=mosaico_gauge_cold_store;
struct Policy {
 constexpr bool valid(const c::Record& r)const{return c::shape(r)&&r.phase<=4&&r.binding[0]==17;}
 constexpr bool initial(const c::Record& r)const{return valid(r)&&r.phase==1;}
 constexpr bool transition(const c::Record& a,const c::Record& b)const{return valid(a)&&valid(b)&&b.phase==a.phase+1;}
};
constexpr c::Record first(){c::Record r;r.sequence=1;r.phase=1;r.nonce[0]=5;r.binding[0]=17;r.size=2;r.payload[0]=9;return r;}
constexpr c::Record step(c::Record r){++r.sequence;++r.phase;return r;}
struct IO {
 std::array<c::Page,10> pages{};unsigned writes=0,syncs=0;int failRead=-1;
 bool failWrite=false,failSync=false,badReadback=false;
 constexpr IO(){for(auto& p:pages)p.fill(255);c::nand::marker(pages[0]);c::nand::marker(pages[1]);}
 constexpr bool read(unsigned i,c::Page& p){if(i>=pages.size()||int(i)==failRead)return false;p=pages[i];if(badReadback&&writes&&i>=8)p[100]^=1;return true;}
 constexpr bool write(unsigned i,const c::Page& p){++writes;if(failWrite||i<8||i>9)return false;pages[i]=p;return true;}
 constexpr bool sync(){++syncs;return !failSync;}
};
'''


class ColdStoreTests(unittest.TestCase):
    def compile(self, code):
        directory = ROOT / 'artifacts/gauge-cold-store-tests'
        directory.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(dir=directory) as tmp:
            source = Path(tmp) / 'test.cpp'
            source.write_text(FIXTURE + code, encoding='utf-8')
            result = subprocess.run([str(COMPILER), '-std=c++20', '-Wall', '-Wextra', '-Werror',
                                     '-fconstexpr-ops-limit=100000000', '-fsyntax-only',
                                     '-I' + str(ROOT / 'main/hal/mosaico'), str(source)],
                                    capture_output=True, text=True)
            log = directory / (self._testMethodName + '.log')
            log.write_text(result.stdout + result.stderr, encoding='utf-8')
            self.assertEqual(result.returncode, 0, str(log))

    def test_codec_and_padding(self):
        self.compile(r'''
constexpr bool test(){auto r=first();c::Page p{};c::Record out;
 if(!c::encode(r,p)||!c::decode(p,out)||out.payload!=r.payload||out.binding!=r.binding)return false;
 for(auto at:{0,4,6,8,16,17,24,40,72,74,80,2043,2044}){auto q=p;q[at]^=1;if(c::decode(q,out))return false;}
 for(auto at:{17,23,74,79,82,1616,2043}){auto q=p;q[at]=1;c::nand::put(q,2044,c::nand::crc(q,2044),4);if(c::decode(q,out))return false;}
 r.size=1537;if(c::encode(r,p))return false;r=first();r.payload[2]=1;if(c::encode(r,p))return false;
 r=first();r.nonce={};if(c::encode(r,p))return false;r=first();r.binding={};if(c::encode(r,p))return false;
 return true;
}static_assert(test());
''')

    def test_chain_and_protected_pages(self):
        self.compile(r'''
constexpr bool test(){IO io;const auto before=io.pages;c::Scratch s;Policy policy;auto r=first();
 if(c::load(io,s,policy).code!=c::Code::Empty||c::save(io,s,r,policy,false)!=c::Code::Rejected||io.writes)return false;
 for(unsigned n=1;n<=4;++n){if(c::save(io,s,r,policy,true)!=c::Code::Ready)return false;
  const auto l=c::load(io,s,policy);if(l.code!=c::Code::Ready||s.records[l.selected].sequence!=n)return false;
  r=step(r);}
 for(unsigned i=0;i<8;++i)if(before[i]!=io.pages[i])return false;
 return io.writes==4&&io.syncs==4;
}static_assert(test());
''')

    def test_no_replay_alias_or_binding_switch(self):
        self.compile(r'''
constexpr bool test(){IO io;c::Scratch s;Policy policy;const auto r=first();
 if(c::save(io,s,r,policy,true)!=c::Code::Ready)return false;
 const auto before=io.writes;auto next=step(r);next.nonce[0]^=1;
 if(c::save(io,s,next,policy,true)!=c::Code::Rejected)return false;
 next=step(r);next.binding[1]=1;if(c::save(io,s,next,policy,true)!=c::Code::Rejected)return false;
 next=step(r);next.sequence=3;if(c::save(io,s,next,policy,true)!=c::Code::Rejected)return false;
 if(c::save(io,s,r,policy,true)!=c::Code::Rejected)return false;
 if(c::save(io,s,s.records[0],policy,true)!=c::Code::Rejected)return false;
 return io.writes==before;
}static_assert(test());
''')

    def test_corruption_and_missing_successor_do_not_fallback(self):
        self.compile(r'''
constexpr bool test(){IO io;c::Scratch s;Policy policy;auto r=first();
 if(c::save(io,s,r,policy,true)!=c::Code::Ready)return false;
 r=step(r);if(c::save(io,s,r,policy,true)!=c::Code::Ready)return false;
 const auto good=io.pages;io.pages[9][80]^=1;
 if(c::load(io,s,policy).code!=c::Code::Corrupt)return false;
 if(c::save(io,s,step(r),policy,true)!=c::Code::Corrupt)return false;
 io.pages=good;io.pages[8].fill(255);
 if(c::load(io,s,policy).code!=c::Code::Ambiguous)return false;
 io.pages=good;io.pages[8]=io.pages[9];
 if(c::load(io,s,policy).code!=c::Code::Ambiguous)return false;
 io.pages=good;r.sequence=8;c::encode(r,io.pages[9]);
 return c::load(io,s,policy).code==c::Code::Ambiguous;
}static_assert(test());
''')

    def test_write_sync_readback_failure(self):
        self.compile(r'''
constexpr bool test(){Policy policy;const auto r=first();
 {IO io;c::Scratch s;io.failWrite=true;if(c::save(io,s,r,policy,true)!=c::Code::Io||io.syncs)return false;}
 {IO io;c::Scratch s;io.failSync=true;if(c::save(io,s,r,policy,true)!=c::Code::Io||io.writes!=1)return false;}
 {IO io;c::Scratch s;io.badReadback=true;if(c::save(io,s,r,policy,true)!=c::Code::Verify)return false;}
 {IO io;c::Scratch s;io.failRead=8;if(c::save(io,s,r,policy,true)!=c::Code::Io||io.writes)return false;}
 {IO io;c::Scratch s;io.pages[0].fill(255);io.pages[1].fill(255);if(c::save(io,s,r,policy,true)!=c::Code::NotFormatted||io.writes)return false;}
 return true;
}static_assert(test());
''')

    def test_visible_initial_record_cannot_skip_initial_policy(self):
        self.compile(r'''
constexpr bool test(){IO io;c::Scratch s;Policy policy;auto old=first();old.phase=2;
 auto next=step(old);
 if(!c::encode(old,io.pages[8])||!c::encode(next,io.pages[9]))return false;
 if(c::load(io,s,policy).code!=c::Code::Ambiguous)return false;
 return c::save(io,s,step(next),policy,true)==c::Code::Ambiguous&&io.writes==0;
}static_assert(test());
''')


if __name__ == '__main__':
    unittest.main()
