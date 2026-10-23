"""Offline production-control-flow mocks + real SDK syntax, NOT on-device crypto.

PSA/P256/SDK image IO are mocked in constexpr decision tests. Ephemeral Python
signatures establish fixture consistency only, not execution of the C++ crypto.
"""
import json
from pathlib import Path
import re
import subprocess
import tempfile
import unittest

import mosaico_gauge_cold_capability as capability
import test_mosaico_gauge_cold_capability as capability_tests
from test_mosaico_gauge_cold_trial import COMPILER

ROOT = Path(__file__).resolve().parents[1]
CPP = ROOT / 'main/hal/mosaico/mosaico_gauge_cold_crypto.cpp'
LOG = ROOT / 'artifacts/gauge-cold-crypto-tests'


def function(name):
    """Extract an unmodified production function body, then mock dependencies."""
    source = CPP.read_text()
    match = re.search(r'^(?:bool|void|trial::BootToken) ' + name + r'\(', source, re.M)
    if not match:
        raise AssertionError(name)
    start = source.index('{', match.start())
    depth = 1
    at = start + 1
    # Ignore braces in C++ string/character literals and comments.
    tokens = re.compile(r'''"(?:\\.|[^"\\])*"|'(?:\\.|[^'\\])*'|//[^\n]*|/\*.*?\*/|[{}]''', re.S)
    for token in tokens.finditer(source, at):
        if token.group() == '{':
            depth += 1
        elif token.group() == '}':
            depth -= 1
        if not depth:
            at = token.end()
            break
    else:
        raise AssertionError('unterminated body: ' + name)
    return source[match.start():at]


def cpparray(raw):
    return '{' + ','.join(str(v) for v in raw) + '}'


class ColdCryptoTests(unittest.TestCase):
    def setUp(self):
        LOG.mkdir(parents=True, exist_ok=True)
        self.tmp = tempfile.TemporaryDirectory(dir=LOG)
        self.addCleanup(self.tmp.cleanup)

    def compile(self, source, flags=()):
        path = Path(self.tmp.name) / 'test.cpp'
        path.write_text(source)
        process = subprocess.run([str(COMPILER), '-std=c++20', '-Wall', '-Wextra', '-Werror',
            '-fconstexpr-ops-limit=100000000', '-fsyntax-only', '-I' + str(ROOT / 'main'),
            '-I' + str(ROOT / 'main/hal/mosaico'), *flags, str(path)], capture_output=True, text=True)
        log = LOG / (self._testMethodName + '.log')
        log.write_text(process.stdout + process.stderr + ('\n' + source if process.returncode else ''))
        self.assertEqual(process.returncode, 0, str(log))

    def prefix(self):
        code = r'''
#include "mosaico_gauge_cold_crypto.h"
#include <cstring>
namespace proof=mosaico_gauge_cold_proof;
namespace capsule=mosaico_gauge_cold_capsule;
namespace trial=mosaico_gauge_cold_trial;
namespace store=mosaico_gauge_cold_store;
using namespace MosaicoColdCrypto;
constexpr int compare(const void* a,const void* b,size_t n)=delete;
constexpr int compare(const uint8_t* a,const uint8_t* b,size_t n){for(size_t i=0;i<n;++i)if(a[i]!=b[i])return 1;return 0;}
constexpr int compare(const uint8_t* a,const char* b,size_t n){for(size_t i=0;i<n;++i)if(a[i]!=uint8_t(b[i]))return 1;return 0;}
constexpr void copy(uint8_t* a,const uint8_t* b,size_t n){for(size_t i=0;i<n;++i)a[i]=b[i];}
constexpr int compareTextN(const char* a,const char* b,size_t n){for(size_t i=0;i<n;++i){if(a[i]!=b[i])return 1;if(!a[i])return 0;}return 0;}
constexpr size_t boundedLength(const char* a,size_t n){size_t i=0;while(i<n&&a[i])++i;return i;}
constexpr Digest toyHash(const uint8_t* data,size_t size){Digest d{};for(size_t i=0;i<size;++i)d[i%32]=uint8_t((d[i%32]*33)^data[i]^uint8_t(i));return d;}
constexpr Binding binding(){Binding b{};b.mac={0x24,1,2,3,4,5};b.originalGctGeneration=19;b.originalGctEpoch=19;b.nonce.fill(1);b.slotHashes[0].fill(2);b.slotHashes[1].fill(3);b.source131ReceiptDigest.fill(4);proof::SourceReceiptInput input{};proof::sourceReceiptHashInput(b,mosaico_gauge_contrast::sourceFixture,input);b.source131ReceiptDigest=toyHash(input.data(),input.size());return b;}
struct Harness {
 static constexpr const char* Versions[2]={"0.13.95-r19-mosaico-ota","0.13.95-r20-mosaico-ota"};
 bool lease=true,hashOk=true,pairOk=true,signatureOk=true,actualOk=true;
 unsigned pairCalls=0,signatureCalls=0,actualCalls=0;bool pairBeforeSession=false;
 constexpr bool ownsLease(){return lease;}
 constexpr bool sha256(const uint8_t* data,size_t size,Digest& out){out={};if(!hashOk||!data||!size)return false;out=toyHash(data,size);return true;}
 constexpr bool signature(const uint8_t*,size_t,const uint8_t*,size_t){++signatureCalls;return signatureOk;}
 constexpr bool pair(const proof::Bytes&,Authenticated& out){pairBeforeSession|=signatureCalls==0;++pairCalls;for(unsigned i=0;i<2;++i)for(size_t j=0;Versions[i][j];++j)out.images[i].version[j]=Versions[i][j];for(unsigned i=0;i<2;++i){for(unsigned j=0;j<32;++j)out.images[i].hash[j]=out.binding.slotHashes[i][j];out.images[i].size=500;}return pairOk;}
 constexpr bool actual(unsigned,const MosaicoOta::VerifiedImageDescriptor&){++actualCalls;return actualOk;}
'''
        for name in ('allowedVersion', 'allowedPairVersions', 'fail', 'bindingWire', 'extractBinding', 'authenticate', 'verifyActualImages', 'buildFromFragment'):
            method = function(name)
            method = 'constexpr ' + method
            method = method.replace('MosaicoOta::currentTaskOwnsStorageLease()', 'ownsLease()')
            method = method.replace('std::memcpy', 'copy').replace('std::memcmp', 'compare').replace('std::strncmp', 'compareTextN')
            method = method.replace('strnlen(', 'boundedLength(')
            # C++20 void-pointer casts are not constexpr. Source still uses the
            # exact byte strings; only that cast is replaced for these mocks.
            method = method.replace('reinterpret_cast<const uint8_t*>(exactPair)', 'pairBytes.data()')
            if name == 'buildFromFragment':
                method = method.replace('size_t used=0;', 'std::array<uint8_t,1051> pairBytes{};for(size_t i=0;i<pairSize;++i)pairBytes[i]=uint8_t(exactPair[i]);size_t used=0;')
            code += method + '\n'
        return code + r'''
};
constexpr std::array<uint8_t,202> fragment(){std::array<uint8_t,202> f{};f[0]='G';f[1]='C';f[2]='S';f[3]='1';capsule::BindingBytes wire{};proof::bindingHashInput(binding(),wire);for(size_t i=0;i<wire.size();++i)f[4+i]=wire[i];f[192]=8;const std::array<uint8_t,8> sig{0x30,6,2,1,1,2,1,1};for(size_t i=0;i<8;++i)f[194+i]=sig[i];return f;}
constexpr Record packet(){Harness h;Record r{};const char* reason=nullptr;const auto f=fragment();h.buildFromFragment(f.data(),f.size(),"[]",r,reason);return r;}
'''

    def test_production_fragment_exact_lengths_bindings_and_failure_clear(self):
        self.compile(self.prefix() + r'''
constexpr bool cases(){Harness h;const auto f=fragment();Record out{};const char* why=nullptr;
 if(!h.buildFromFragment(f.data(),f.size(),"[]",out,why)||out.phase!=1||out.sequence!=1||out.nonce!=binding().nonce)return false;
 Binding b{};if(!h.extractBinding(out,b)||!trial::sameBinding(b,binding()))return false;
 if(out.binding!=toyHash(out.payload.data()+56,188))return false;
 for(size_t n=0;n<f.size();++n){out=packet();if(h.buildFromFragment(f.data(),n,"[]",out,why)||out.size||out.sequence)return false;}
 auto bad=f;bad[0]='X';if(h.buildFromFragment(bad.data(),bad.size(),"[]",out,why)||out.size)return false;
 bad=f;bad[193]=1;if(h.buildFromFragment(bad.data(),bad.size(),"[]",out,why)||out.size)return false;
 bad=f;bad[4+8+11]=2;if(h.buildFromFragment(bad.data(),bad.size(),"[]",out,why)||out.size)return false;
 if(h.buildFromFragment(f.data(),f.size(),"",out,why)||out.size)return false;
 h.hashOk=false;if(h.buildFromFragment(f.data(),f.size(),"[]",out,why)||out.size)return false;
 return true;
}static_assert(cases());
''')

    def test_production_authenticate_lease_source_nonce_hash_signature_failures(self):
        self.compile(self.prefix() + r'''
constexpr bool invalidSessionNeverParses(){const auto r=packet();Harness invalid;invalid.signatureOk=false;
 Authenticated out{};const char* why=nullptr;
 return !invalid.authenticate(r,binding().mac,mosaico_gauge_contrast::sourceFixture,out,why)&&
  invalid.pairCalls==0&&invalid.signatureCalls==1&&!out.sessionVerified;
}static_assert(invalidSessionNeverParses());
constexpr bool cases(){const auto r=packet();Harness h;Authenticated out{};const char* why=nullptr;
 if(!h.authenticate(r,binding().mac,mosaico_gauge_contrast::sourceFixture,out,why)||!out.sessionVerified||out.actualImagesVerified||h.pairCalls!=1||h.signatureCalls!=1||h.pairBeforeSession)return false;
 h.lease=false;out.sessionVerified=true;if(h.authenticate(r,binding().mac,mosaico_gauge_contrast::sourceFixture,out,why)||out.sessionVerified||h.pairCalls!=1)return false;
 h.lease=true;auto source=mosaico_gauge_contrast::sourceFixture;source[0]^=1;
 if(h.authenticate(r,binding().mac,source,out,why)||out.sessionVerified)return false;
 auto mac=binding().mac;mac[5]^=1;if(h.authenticate(r,mac,mosaico_gauge_contrast::sourceFixture,out,why))return false;
 auto bad=r;bad.nonce[0]^=1;if(h.authenticate(bad,binding().mac,mosaico_gauge_contrast::sourceFixture,out,why))return false;
 bad=r;bad.binding[31]^=1;if(h.authenticate(bad,binding().mac,mosaico_gauge_contrast::sourceFixture,out,why))return false;
 bad=r;bad.phase=2;if(h.authenticate(bad,binding().mac,mosaico_gauge_contrast::sourceFixture,out,why))return false;
 bad=r;bad.sequence=2;if(h.authenticate(bad,binding().mac,mosaico_gauge_contrast::sourceFixture,out,why))return false;
 bad=r;bad.payload[bad.size]=1;if(h.authenticate(bad,binding().mac,mosaico_gauge_contrast::sourceFixture,out,why))return false;
 h.hashOk=false;if(h.authenticate(r,binding().mac,mosaico_gauge_contrast::sourceFixture,out,why))return false;
 h.hashOk=true;h.pairOk=false;if(h.authenticate(r,binding().mac,mosaico_gauge_contrast::sourceFixture,out,why)||out.sessionVerified)return false;
 h.pairOk=true;h.signatureOk=false;if(h.authenticate(r,binding().mac,mosaico_gauge_contrast::sourceFixture,out,why)||out.sessionVerified)return false;
 return true;
}static_assert(cases());
''')

    def test_actual_gate_separate_lease_both_images_and_changed_binding_clear(self):
        self.compile(self.prefix() + r'''
constexpr bool cases(){Harness h;Authenticated out{};const char* why=nullptr;const auto r=packet();
 if(!h.allowedVersion("0.13.95-r19-mosaico-ota")||!h.allowedVersion("0.13.95-r20-mosaico-ota"))return false;
 if(h.allowedVersion("0.13.95-r17-mosaico-ota")||h.allowedVersion("0.13.95-r18-mosaico-ota")||h.allowedVersion("0.13.95-r21-mosaico-ota")||h.allowedVersion("0.13.95-r19-cold-draft"))return false;
 if(!h.authenticate(r,binding().mac,mosaico_gauge_contrast::sourceFixture,out,why)||out.actualImagesVerified)return false;
 const auto good=out;if(!h.verifyActualImages(out,why)||!out.actualImagesVerified||h.actualCalls!=2)return false;
 out=good;h.lease=false;if(h.verifyActualImages(out,why)||out.sessionVerified||out.actualImagesVerified||h.actualCalls!=2)return false;
 h.lease=true;out=good;out.binding.originalGctEpoch++;if(h.verifyActualImages(out,why)||out.sessionVerified)return false;
 out=good;out.images[1].hash[0]^=1;if(h.verifyActualImages(out,why)||out.sessionVerified)return false;
 out=good;h.actualOk=false;if(h.verifyActualImages(out,why)||out.sessionVerified||out.actualImagesVerified)return false;
 out=good;out.sessionVerified=false;if(h.verifyActualImages(out,why)||out.actualImagesVerified)return false;
 out=good;for(unsigned j=0;j<32;++j)out.images[1].version[j]=out.images[0].version[j];if(h.verifyActualImages(out,why)||out.sessionVerified)return false;
 out=good;for(unsigned j=0;j<32;++j){const char v=out.images[0].version[j];out.images[0].version[j]=out.images[1].version[j];out.images[1].version[j]=v;}h.actualOk=true;if(!h.verifyActualImages(out,why))return false;
 return true;
}static_assert(cases());
''')

    def test_boot_identity_latched_good_zero_5a_and_target_bypass(self):
        body = function('prepareBootIdentity').replace('void prepareBootIdentity', 'constexpr void prepareBootIdentity')
        getter = function('bootIdentity').replace('trial::BootToken bootIdentity', 'constexpr trial::BootToken bootIdentity')
        code = r'''
#include "mosaico_gauge_cold_crypto.h"
#include <atomic>
namespace trial=mosaico_gauge_cold_trial;
// Constexpr atomic semantic mock; production std::atomic is separately SDK
// compiled. Reentrant initialization/read models the forbidden partial publish.
struct State {uint8_t value=0;unsigned exchanges=0;
 constexpr bool compare_exchange_strong(uint8_t& expected,uint8_t desired,std::memory_order,std::memory_order){++exchanges;if(value!=expected){expected=value;return false;}value=desired;return true;}
 constexpr void store(uint8_t v,std::memory_order){value=v;}
 constexpr uint8_t load(std::memory_order)const{return value;}
};
struct Boot {State bootState{};trial::BootToken bootToken{};uint8_t value=1;unsigned enables=0,fills=0,disables=0;bool partialRead=false,reentered=false;
 constexpr void bootloader_random_enable(){++enables;}
 constexpr void bootloader_random_disable(){++disables;}
 constexpr void esp_fill_random(uint8_t* p,size_t n){++fills;prepareBootIdentity();reentered=true;partialRead=bootIdentity().verified;for(size_t i=0;i<n;++i)p[i]=value;}
''' + body + getter + r'''
};
constexpr bool cases(){Boot good;if(good.bootIdentity().verified)return false;good.prepareBootIdentity();good.value=2;good.prepareBootIdentity();
#if CONFIG_IDF_TARGET_ESP32S31 && !CONFIG_ESP_BRINGUP_BYPASS_RANDOM_SETTING
 if(!good.bootIdentity().verified||good.bootIdentity().nonce[0]!=1||good.enables!=1||good.fills!=1||good.disables!=1||good.partialRead||!good.reentered||good.bootState.value!=2)return false;
 for(uint8_t v:{uint8_t(0),uint8_t(0x5a)}){Boot b;b.value=v;b.prepareBootIdentity();b.value=3;b.prepareBootIdentity();if(b.bootIdentity().verified||trial::nonzero(b.bootIdentity().nonce)||b.fills!=1||b.bootState.value!=3)return false;}
#else
 if(good.bootIdentity().verified||good.enables||good.fills||good.disables||good.bootState.value!=3)return false;
#endif
 Boot inflight;inflight.bootState.value=1;inflight.bootToken.verified=true;if(inflight.bootIdentity().verified)return false;inflight.prepareBootIdentity();if(inflight.fills)return false;
 return true;
}static_assert(cases());
'''
        for target, bypass in ((1, 0), (1, 1), (0, 0)):
            self.compile(code, [f'-DCONFIG_IDF_TARGET_ESP32S31={target}', f'-DCONFIG_ESP_BRINGUP_BYPASS_RANDOM_SETTING={bypass}'])

    def test_strict_tree_duplicate_decoded_keys_nan_nul_and_exact_field_families(self):
        code = r'''
#include <cstddef>
#include <limits>
#include <cmath>
struct cJSON {int type=0;const char* string=nullptr;double valuedouble=0;cJSON* child=nullptr;cJSON* next=nullptr;};
constexpr bool cJSON_IsNumber(const cJSON* p){return p&&p->type==1;}
constexpr bool cJSON_IsObject(const cJSON* p){return p&&p->type==2;}
constexpr int compareText(const char* a,const char* b){while(*a&&*a==*b){++a;++b;}return *a-*b;}
'''
        for name in ('strictTree', 'fields', 'noEscapedNul'):
            code += 'constexpr ' + function(name).replace('std::strcmp', 'compareText') + '\n'
        self.compile(code + r'''
constexpr bool cases(){cJSON second{0,"image"};cJSON first{0,"image",0,nullptr,&second};cJSON obj{2,nullptr,0,&first};
 if(strictTree(&obj))return false;
 second.string="signature";if(!strictTree(&obj))return false;
 const char* names[]={"image","signature"};if(!fields(&obj,names,2))return false;
 second.string="unknown";if(fields(&obj,names,2))return false;
 second.type=1;second.valuedouble=std::numeric_limits<double>::quiet_NaN();if(strictTree(&obj))return false;
 second.valuedouble=std::numeric_limits<double>::infinity();if(strictTree(&obj))return false;
 if(noEscapedNul("\"\\u0000suffix\"",14))return false;
 if(!noEscapedNul("\"\\\\u0000\"",10))return false;
 return true;
}static_assert(cases());
''')

    def test_production_actual_image_full_stream_sdk_identity_and_failures(self):
        actual = function('actual').replace('bool actual', 'constexpr bool actual')
        actual = actual.replace('std::strcmp', 'textCompare').replace('std::strncmp', 'textCompareN').replace('std::memcmp', 'bytesCompare')
        code = r"""
#include "mosaico_gauge_cold_crypto.h"
#include <algorithm>
using namespace MosaicoColdCrypto;
constexpr int ESP_OK=0,PSA_SUCCESS=0,PSA_ALG_SHA_256=1,ESP_PARTITION_TYPE_APP=0,ESP_PARTITION_SUBTYPE_APP_OTA_0=0,ESP_PARTITION_SUBTYPE_APP_OTA_1=1,ESP_OTA_IMG_VALID=2;
using esp_ota_img_states_t=int;
struct esp_partition_t {size_t size=1024;unsigned slot=0;};
struct esp_app_desc_t {char version[32]{};char project_name[32]{};uint8_t app_elf_sha256[32]{};};
struct psa_hash_operation_t {Digest hash{};size_t used=0;};
#define PSA_HASH_OPERATION_INIT {}
constexpr int textCompare(const char* a,const char* b){while(*a&&*a==*b){++a;++b;}return *a-*b;}
constexpr int textCompareN(const char* a,const char* b,size_t n){for(size_t i=0;i<n;++i){if(a[i]!=b[i])return 1;if(!a[i])return 0;}return 0;}
constexpr int bytesCompare(const uint8_t* a,const uint8_t* b,size_t n){for(size_t i=0;i<n;++i)if(a[i]!=b[i])return 1;return 0;}
struct Actual {
 static constexpr const char* Versions[2]={"0.13.95-r19-mosaico-ota","0.13.95-r20-mosaico-ota"};
 esp_partition_t part{};int state=ESP_OTA_IMG_VALID;bool ioOk=true,descOk=true,hashOk=true;
 bool badVersion=false,badProject=false,zeroElf=false,subscribed=true;unsigned descOrder[2]={0,1};
 unsigned reads=0,feeds=0,aborts=0;size_t bytes=0;
 constexpr const esp_partition_t* esp_partition_find_first(int,int slot,const char*){part.slot=unsigned(slot);return &part;}
 constexpr int esp_ota_get_state_partition(const esp_partition_t*,int* out){*out=state;return ESP_OK;}
 constexpr int esp_ota_get_partition_description(const esp_partition_t*,esp_app_desc_t* out){
  if(!descOk)return -1;for(size_t i=0;Versions[descOrder[part.slot]][i];++i)out->version[i]=Versions[descOrder[part.slot]][i];
  const char* project="Stopwatch-Mosaico";for(size_t i=0;project[i];++i)out->project_name[i]=project[i];
  if(badVersion)out->version[0]='X';if(badProject)out->project_name[0]='X';out->app_elf_sha256[0]=zeroElf?0:1;return ESP_OK;
 }
 constexpr int psa_crypto_init(){return hashOk?PSA_SUCCESS:-1;}
 constexpr int psa_hash_setup(psa_hash_operation_t*,int){return PSA_SUCCESS;}
 constexpr int esp_task_wdt_status(void*){return subscribed?ESP_OK:-1;}
 constexpr int esp_task_wdt_reset(){++feeds;return ESP_OK;}
 constexpr int esp_partition_read(const esp_partition_t*,size_t offset,uint8_t* out,size_t n){++reads;bytes+=n;for(size_t i=0;i<n;++i)out[i]=uint8_t((offset+i)%17);return ioOk?ESP_OK:-1;}
 constexpr int psa_hash_update(psa_hash_operation_t* op,const uint8_t* data,size_t n){for(size_t i=0;i<n;++i){const size_t at=op->used++;op->hash[at%32]=uint8_t(op->hash[at%32]^data[i]);}return PSA_SUCCESS;}
 constexpr int psa_hash_finish(psa_hash_operation_t* op,uint8_t* out,size_t,size_t* n){for(size_t i=0;i<32;++i)out[i]=op->hash[i];*n=32;return PSA_SUCCESS;}
 constexpr int psa_hash_abort(psa_hash_operation_t*){++aborts;return PSA_SUCCESS;}
""" + "constexpr " + function('allowedVersion').replace('std::strncmp', 'textCompareN') + actual + r"""
};
constexpr MosaicoOta::VerifiedImageDescriptor image(unsigned version=0){MosaicoOta::VerifiedImageDescriptor out{};out.size=513;for(size_t i=0;Actual::Versions[version][i];++i)out.version[i]=Actual::Versions[version][i];for(size_t i=0;i<out.size;++i)out.hash[i%32]^=uint8_t(i%17);return out;}
constexpr bool cases(){Actual a;if(!a.actual(0,image())||a.reads!=3||a.bytes!=513||a.feeds!=3||a.aborts!=1)return false;
 Actual b;b.state=1;if(b.actual(0,image())||b.reads)return false;
 Actual c;c.badVersion=true;if(c.actual(0,image())||c.reads)return false;
 Actual d;d.badProject=true;if(d.actual(0,image())||d.reads)return false;
 Actual e;e.zeroElf=true;if(e.actual(0,image())||e.reads)return false;
 Actual f;f.ioOk=false;if(f.actual(0,image())||f.reads!=1||f.aborts!=1)return false;
 Actual g;auto changed=image();changed.hash[0]^=1;if(g.actual(0,changed)||g.bytes!=513)return false;
 Actual h;h.subscribed=false;if(!h.actual(0,image())||h.feeds)return false;
 Actual i;changed=image();changed.size=1025;if(i.actual(0,changed)||i.reads)return false;
 Actual reversed;reversed.descOrder[0]=1;reversed.descOrder[1]=0;if(!reversed.actual(0,image(1))||!reversed.actual(1,image(0)))return false;
 Actual wrongSlot;wrongSlot.descOrder[0]=1;if(wrongSlot.actual(0,image(0))||wrongSlot.reads)return false;
 return true;
}static_assert(cases());
"""
        # Formatting-only split for mock declarations under -Werror.
        code = code.replace('return -1;for', 'return -1;\n  for').replace("='X';if", "='X';\n  if").replace("='X';out", "='X';\n  out")
        self.compile(code)

    def test_pair_lexical_depth_gate_precedes_parser_and_bounds_nested_input(self):
        lexical = 'constexpr ' + function('pairLexicalDepth')
        code = r"""
#include "mosaico_gauge_cold_proof_model.h"
namespace proof=mosaico_gauge_cold_proof;
""" + lexical + r"""
template<size_t N> constexpr bool check(const char (&text)[N]){std::array<uint8_t,N-1> bytes{};for(size_t i=0;i<N-1;++i)bytes[i]=uint8_t(text[i]);return pairLexicalDepth(bytes.data(),bytes.size());}
constexpr bool cases(){
 std::array<uint8_t,1000> deep{};for(size_t i=0;i<500;++i){deep[i]='[';deep[999-i]=']';}if(pairLexicalDepth(deep.data(),deep.size()))return false;
 if(check("[[[[[[[[[]]]]]]]]]"))return false; // Nine containers.
 if(!check("[[[[[[[[]]]]]]]]"))return false; // Eight, lexical ONLY; not pair shape.
 if(!check("[\"[[[{{{]]]}}}\"]"))return false;
 if(!check("[\"quoted\\\"[[[\"]"))return false;
 if(!check("[\"\\\\\",{}]"))return false;
 if(check("[\"unterminated"))return false;
 if(check("[\"trailing\\"))return false;
 if(check("]")||check("[}")||check("{]}")||check("[[[]]"))return false;
 if(pairLexicalDepth(nullptr,1000)||pairLexicalDepth(deep.data(),0))return false;
 return true;
}static_assert(cases());
"""
        self.compile(code)
        pair_body = function('pair')
        self.assertLess(pair_body.index('pairLexicalDepth'), pair_body.index('cJSON_ParseWithLengthOpts'))
        auth_body = function('authenticate')
        self.assertLess(auth_body.index('signature(message.data()'), auth_body.index('pair(view.pairRaw,candidate)'))

    def test_ephemeral_signed_fixture_has_exact_production_roster_and_raw_bytes(self):
        fixture = capability_tests.ColdCapabilityTests()
        fixture.setUp()
        self.addCleanup(fixture.tearDown)
        raw = b'\n ' + fixture.raw + b' \n'
        packet = fixture.signed(raw)
        capability.verify_payload(packet, fixture.pub, fixture.images, fixture.binding)
        _, binding, pair, sig = capability.decode_payload(packet)
        self.assertEqual(pair, raw)
        fragment = b'GCS1' + binding + len(sig).to_bytes(2, 'little') + sig
        self.assertLessEqual(len(fragment), 266)
        self.assertEqual({p['image']['version'] for p in fixture.pairs}, {'0.13.95-r19-mosaico-ota', '0.13.95-r20-mosaico-ota'})
        text = CPP.read_text()
        self.assertIn(capability.GCT_DIGEST, text)
        self.assertIn('mbedtls_pk_verify', text)
        self.assertIn('MosaicoOta::verifySignedImageDescriptor', text)
        self.assertIn('esp_ota_get_state_partition', text)
        self.assertIn('psa_hash_update', text)

    def test_real_sdk_s31_syntax_adapter_only_no_shared_outputs(self):
        db = ROOT / '.artifacts/mosaico/ota-build/compile_commands.json'
        self.assertTrue(db.exists(), 'configured SDK compile database required')
        entries = json.loads(db.read_text())
        entry = next(e for e in entries if e['file'].replace('\\', '/').endswith('/mosaico_ota.cpp'))
        command = re.sub(r' -o \S+ -c ', ' -fsyntax-only ', entry['command']).replace(entry['file'], str(CPP))
        result = subprocess.run(command, cwd=entry['directory'], capture_output=True, text=True)
        path = LOG / 'sdk-syntax.log'
        path.write_text(result.stdout + result.stderr)
        self.assertEqual(result.returncode, 0, str(path))


if __name__ == '__main__':
    unittest.main()
