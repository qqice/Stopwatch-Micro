"""Raw cold evidence codec only; offline RV32 compile, zero hardware."""
import unittest
import test_mosaico_gauge_cold_store as store_tests


class ColdEvidenceTests(unittest.TestCase):
    compile = store_tests.ColdStoreTests.compile

    def test_raw_roundtrip_signed_and_unknown_transport_flags(self):
        self.compile(r'''
#include "mosaico_gauge_cold_evidence_model.h"
namespace e=mosaico_gauge_cold_evidence;
constexpr bool test(){e::Frame f;f.bootNonce[0]=3;f.resetReason=0xffffffff;f.error=42;
 f.standard.validMask=0xff008801;f.standard.sampledUs=UINT64_MAX;f.standard.rawSoc=255;
 f.standard.remainingMah=0xffff;f.standard.currentMa=-32768;f.standard.averageCurrentMa=-1;
 f.standard.operationStatus=0x7fff;f.standard.rawCoulombMah=0xfffe;f.standard.phase=1;
 f.standard.units={0,UINT64_MAX,42,false,3};f.engine.units={0,77,999,false,2};
 f.engine.validMask=0x55aa;f.engine.sampledUs=1;f.engine.echo73=0x1234;f.engine.echo76=0xffff;
 for(unsigned i=0;i<23;++i)f.engine.rawCedv[i]=uint8_t(255-i);
 for(unsigned i=0;i<17;++i)f.engine.rawSmooth[i]=uint8_t(i);
 for(unsigned i=0;i<11;++i)f.engine.rawIdentity[i]=uint8_t(71+i);
 e::Wire w{},again{};e::Frame out;
 if(!e::encode(f,w)||!e::decode(w,out)||!e::encode(out,again)||w!=again)return false;
 return out.standard.currentMa==-32768&&out.standard.averageCurrentMa==-1&&
  out.standard.rawSoc==255&&out.engine.validMask==0x55aa&&out.engine.units.factor==0&&!out.engine.units.verified&&out.error==42;
}static_assert(test());
''')

    def test_padding_tamper_and_no_physical_units(self):
        self.compile(r'''
#include "mosaico_gauge_cold_evidence_model.h"
namespace e=mosaico_gauge_cold_evidence;
constexpr bool test(){e::Frame f;f.bootNonce[0]=1;e::Wire w{};e::Frame out;
 if(!e::encode(f,w))return false;
 for(auto at:{0,8,12,16,32,104,114,188,251,252}){auto bad=w;bad[at]^=1;if(e::decode(bad,out))return false;}
 for(auto at:{13,15,73,91,95,165,167,185,187,192,251}){auto bad=w;bad[at]=1;e::bytes::put(bad,252,e::bytes::crc(bad,252),4);if(e::decode(bad,out))return false;}
 f.standard.units.factor=1;if(e::encode(f,w))return false;f.standard.units.factor=0;
 f.engine.units.verified=true;if(e::encode(f,w))return false;f.engine.units.verified=false;
 f.bootNonce={};if(e::encode(f,w))return false;
 return true;
}static_assert(test());
''')


if __name__ == '__main__':
    unittest.main()
