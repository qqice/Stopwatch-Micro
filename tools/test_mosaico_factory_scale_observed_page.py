"""Pure cached131 page codec; no SDK runtime, serial port or gauge writes."""
import unittest
from test_mosaico_factory_scale_journal import FactoryScaleJournalTests


class ObservedPageTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        FactoryScaleJournalTests.setUpClass.__func__(cls)

    compile = FactoryScaleJournalTests.compile
    def test_pages_reassemble_actual_source_and_target_crc(self):
        self.compile(r'''
#include "mosaico_factory_scale_observed_page.h"
namespace p=mosaico_factory_observed_page;
constexpr bool pages(bool target) {
 const auto s=sample().snapshot; r::Observation o{};
 o.count=131;o.authenticated=true;
 for(unsigned i=0;i<131;++i)for(unsigned b=0;b<4;++b)
  o.raw[i][b]=target?s.fields[i].target[b]:s.fields[i].original[b];
 uint32_t combined=0xffffffffU;unsigned seen=0;
 for(unsigned index=0;index<5;++index){p::Page page{};
  if(!p::encode(s,o,index,page)||page.first!=seen||page.size!=page.count*7||
     page.observedCrc!=f::digest(s,target)||
     page.pageCrc!=old::crc32(page.bytes.data(),page.size))return false;
  if(page.count!=(index==4?3:32))return false;
  for(unsigned i=0;i<page.size;++i)combined=p::feed(combined,page.bytes[i]);
  seen+=page.count;
 }
 return seen==131&&~combined==(target?f::candidateTargetCrc:f::factorySourceCrc);
}
static_assert(pages(false));static_assert(pages(true));
''')

    def test_actual_not_template_digest_padding_and_fail_closed(self):
        self.compile(r'''
#include "mosaico_factory_scale_observed_page.h"
namespace p=mosaico_factory_observed_page;
constexpr bool test(){auto s=sample().snapshot;r::Observation o{};
 o.count=131;o.authenticated=true;
 for(unsigned i=0;i<131;++i)for(unsigned b=0;b<4;++b)o.raw[i][b]=s.fields[i].original[b];
 p::Page a{},b{};if(!p::encode(s,o,0,a))return false;
 o.raw[0][0]^=1;if(!p::encode(s,o,0,b)||a.observedCrc==b.observedCrc||a.pageCrc==b.pageCrc)return false;
 o.raw[0][0]^=1;o.raw[0][3]=0xa5;
 if(!p::encode(s,o,0,b)||a.bytes!=b.bytes||a.observedCrc!=b.observedCrc)return false;
 for(auto index:{size_t(5),size_t(-1)}){b.count=99;if(p::encode(s,o,index,b)||b.count||b.size||b.observedCrc)return false;}
 o.authenticated=false;if(p::encode(s,o,0,b))return false;o.authenticated=true;
 o.count=130;if(p::encode(s,o,0,b))return false;o.count=131;
 s.count=130;if(p::encode(s,o,0,b))return false;s.count=131;
 s.layoutId=r::Layout::Unknown;if(p::encode(s,o,0,b))return false;s.layoutId=r::Layout::Public131V1;
 s.fields[130].address^=1;if(p::encode(s,o,0,b))return false;s.fields[130].address^=1;
 s.fields[0].width=3;return !p::encode(s,o,0,b);
}
static_assert(test());
''')


if __name__ == '__main__':
    # Only the two new codec tests; the imported journal suite is run separately.
    unittest.main(defaultTest='ObservedPageTests')
