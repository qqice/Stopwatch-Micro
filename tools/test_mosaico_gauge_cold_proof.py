"""Ephemeral signed Python bytes -> pure C++ proof codec; no actual key/device."""
import hashlib
from pathlib import Path
import struct
import subprocess
import tempfile
import unittest

import mosaico_gauge_cold_capability as cap
import test_mosaico_gauge_cold_capability as capability_tests
from test_mosaico_gauge_cold_trial import COMPILER

ROOT = Path(__file__).resolve().parents[1]


def array(raw):
    return '{' + ','.join(str(x) for x in raw) + '}'


class ColdProofTests(unittest.TestCase):
    def setUp(self):
        self.directory = ROOT / 'artifacts/gauge-cold-proof-tests'
        self.directory.mkdir(parents=True, exist_ok=True)
        self.tmp = tempfile.TemporaryDirectory(dir=self.directory)
        self.addCleanup(self.tmp.cleanup)
        self.fixture = capability_tests.ColdCapabilityTests()
        self.fixture.setUp()
        self.addCleanup(self.fixture.tearDown)
        self.raw_source = self.fixture.raw_source()
        binding = self.fixture.binding
        binding['source131_receipt_digest'] = cap.source_receipt(self.raw_source, bytes.fromhex(binding['mac']), 0x220, 3, 4, 19, 19).hex()
        self.raw_pair = b'\n ' + self.fixture.raw + b' \n'
        self.payload = self.fixture.signed(self.raw_pair)
        # Actual crypto is deliberately Python/trusted-wrapper work, never a
        # claim made by the structural C++ decode result.
        cap.verify_payload(self.payload, self.fixture.pub, self.fixture.images, binding)
        b = binding
        self.prefix = r'''
#include "mosaico_gauge_cold_proof_model.h"
namespace p=mosaico_gauge_cold_proof;
namespace m=mosaico_gauge_cold_trial;
constexpr m::Binding binding(){m::Binding b{};
''' + 'b.mac=' + array(bytes.fromhex(b['mac'])) + ';b.originalGctGeneration=19;b.originalGctEpoch=19;b.nonce=' + array(bytes.fromhex(b['nonce'])) + \
            ';b.slotHashes[0]=' + array(bytes.fromhex(b['slot_hashes'][0])) + ';b.slotHashes[1]=' + array(bytes.fromhex(b['slot_hashes'][1])) + \
            ';b.source131ReceiptDigest=' + array(bytes.fromhex(b['source131_receipt_digest'])) + ';return b;}\n' + \
            'inline constexpr std::array<uint8_t,' + str(len(self.payload)) + '> signedPayload=' + array(self.payload) + ';\n' + \
            'inline constexpr std::array<uint8_t,' + str(len(self.raw_pair)) + '> pairRaw=' + array(self.raw_pair) + ';\n'

    def compile(self, body):
        source = Path(self.tmp.name) / 'proof.cpp'
        source.write_text(self.prefix + body)
        result = subprocess.run([str(COMPILER), '-std=c++20', '-Wall', '-Wextra', '-Werror',
            '-fconstexpr-ops-limit=100000000', '-fsyntax-only', '-I' + str(ROOT / 'main/hal/mosaico'), str(source)],
            capture_output=True, text=True)
        log = self.directory / (self._testMethodName + '.log')
        log.write_text(result.stdout + result.stderr + ('\n' + source.read_text() if result.returncode else ''))
        self.assertEqual(result.returncode, 0, str(log))

    def test_python_signed_package_offsets_fields_nonce_and_encode_roundtrip(self):
        self.compile(r'''
constexpr bool cases(){const auto decoded=p::decode(signedPayload,signedPayload.size(),binding());
 if(!decoded.canonical()||decoded.signatureVerified||decoded.sdkVerified||decoded.actualFlashVerified)return false;
 const auto& v=decoded.view;if(v.totalSize!=signedPayload.size()||v.bindingOffset!=56||v.pairOffset!=244||v.binding.size!=188||v.nonce.size!=16)return false;
 if(v.binding.data!=signedPayload.data()+56||v.pairRaw.data!=signedPayload.data()+244||v.signature.data!=signedPayload.data()+v.signatureOffset)return false;
 if(v.signatureOffset!=244+pairRaw.size()||v.pairRaw.size!=pairRaw.size()||v.signatureOffset+v.signature.size!=v.totalSize)return false;
 for(size_t i=0;i<16;++i)if(v.nonce[i]!=binding().nonce[i])return false;
 for(size_t i=0;i<pairRaw.size();++i)if(v.pairRaw[i]!=pairRaw[i])return false;
 std::array<uint8_t,1536> out{};size_t used=0;
 if(p::encode(binding(),v.pairRaw,v.signature,out,used)!=p::Error::None||used!=signedPayload.size())return false;
 for(size_t i=0;i<used;++i)if(out[i]!=signedPayload[i])return false;
 for(size_t i=used;i<out.size();++i)if(out[i])return false;
 return p::decode(out,used,binding()).canonical();
}static_assert(cases());
''')

    def test_canonical_hash_inputs_match_python_exact_domains_and_bytes(self):
        wire = cap.encode_binding(self.fixture.binding)
        binding_sha, pair_sha = hashlib.sha256(wire).digest(), hashlib.sha256(self.raw_pair).digest()
        session = cap.session_message(wire, self.raw_pair)
        source = cap.SOURCE_DOMAIN + bytes.fromhex(self.fixture.binding['mac']) + struct.pack('<HHBIQ', 0x220, 3, 4, 19, 19) + self.raw_source
        self.compile('constexpr p::BindingInput expectedBinding=' + array(wire) + ';\n' +
            'constexpr m::Digest bindingSha=' + array(binding_sha) + ',pairSha=' + array(pair_sha) + ';\n' +
            'constexpr p::SessionInput expectedSession=' + array(session) + ';\n' +
            'constexpr p::SourceRaw raw=' + array(self.raw_source) + ';\n' +
            'constexpr p::SourceReceiptInput expectedSource=' + array(source) + ';\n' + r'''
constexpr bool cases(){p::BindingInput b{};p::SessionInput s{};p::SourceReceiptInput r{};
 if(!p::bindingHashInput(binding(),b)||b!=expectedBinding)return false;
 if(!p::sessionMessage(bindingSha,pairSha,s)||s!=expectedSession)return false;
 if(!p::sourceReceiptHashInput(binding(),raw,r)||r!=expectedSource)return false;
 if(p::sessionMessage({},pairSha,s)||p::sessionMessage(bindingSha,{},s))return false;
 return s.size()==122&&r.size()==274;
}static_assert(cases());
''')

    def test_reserved_header_contract_length_overflow_and_torn_EOF_rejected(self):
        self.compile(r'''
constexpr bool cases(){
 for(size_t used=0;used<signedPayload.size();++used)if(p::decode(signedPayload,used,binding()).canonical())return false;
 for(size_t at:{size_t(0),size_t(4),size_t(6),size_t(8),size_t(10),size_t(12),size_t(14)}){
  auto bad=signedPayload;bad[at]^=1;if(p::decode(bad,bad.size(),binding()).canonical())return false;}
 for(size_t at=16;at<56;++at){auto bad=signedPayload;bad[at]^=1;if(p::decode(bad,bad.size(),binding()).canonical())return false;}
 auto bad=signedPayload;bad[12]=bad[13]=bad[14]=bad[15]=255;
 if(p::decode(bad,bad.size(),binding()).canonical())return false;
 std::array<uint8_t,1536> padded{};for(size_t i=0;i<signedPayload.size();++i)padded[i]=signedPayload[i];
 if(p::decode(padded,signedPayload.size()+1,binding()).canonical()||p::decode(padded,1537,binding()).canonical())return false;
 std::array<uint8_t,8> shortBuffer{};return !p::decode(shortBuffer,8,binding()).canonical();
}static_assert(cases());
''')

    def test_all188_binding_bytes_expected_nonce_hashes_and_slot_mode_are_checked(self):
        self.compile(r'''
constexpr bool cases(){for(size_t at=56;at<244;++at){auto bad=signedPayload;bad[at]^=1;
 if(p::decode(bad,bad.size(),binding()).canonical())return false;}
 auto expected=binding();expected.nonce[0]^=2;if(p::decode(signedPayload,signedPayload.size(),expected).canonical())return false;
 expected=binding();expected.originalGctEpoch++;if(p::decode(signedPayload,signedPayload.size(),expected).canonical())return false;
 expected=binding();expected.source131ReceiptDigest[31]^=1;if(p::decode(signedPayload,signedPayload.size(),expected).canonical())return false;
 expected=binding();expected.recovery=m::RecoveryBinding::ContractDigest;expected.slotHashes={};expected.contractDigest[0]=1;
 p::BindingInput bytes{};return !p::bindingHashInput(expected,bytes)&&!p::decode(signedPayload,signedPayload.size(),expected).canonical();
}static_assert(cases());
''')

    def test_pair_NUL_DER_aliases_upper_lengths_and_input_output_alias_rejected(self):
        self.compile(r'''
constexpr bool cases(){const auto original=p::decode(signedPayload,signedPayload.size(),binding());if(!original.canonical())return false;
 auto bad=signedPayload;bad[244]=0;if(p::decode(bad,bad.size(),binding()).canonical())return false;
 const auto sigAt=original.view.signatureOffset;
 for(size_t at:{size_t(0),size_t(1),size_t(2),size_t(3)}){bad=signedPayload;bad[sigAt+at]=0;
 if(p::decode(bad,bad.size(),binding()).canonical())return false;}
 std::array<uint8_t,72> maxDer{};maxDer[0]=0x30;maxDer[1]=70;maxDer[2]=maxDer[37]=2;maxDer[3]=maxDer[38]=33;maxDer[5]=maxDer[40]=0x80;
 if(!p::derShape({maxDer.data(),maxDer.size()}))return false;
 std::array<uint8_t,73> overDer{};if(p::derShape({overDer.data(),overDer.size()}))return false;
 const std::array<uint8_t,8> sig{0x30,6,2,1,1,2,1,1};
 const std::array<uint8_t,8> zero{0x30,6,2,1,0,2,1,1};if(p::derShape({zero.data(),zero.size()}))return false;
 std::array<uint8_t,1051> pair{};pair.fill('A');std::array<uint8_t,1536> out{};size_t used=1;
 if(p::encode(binding(),{pair.data(),pair.size()},{sig.data(),sig.size()},out,used)!=p::Error::Pair||used!=0)return false;
 if(p::encode(binding(),{pair.data(),1050},{sig.data(),sig.size()},out,used)!=p::Error::None)return false;
 if(!p::decode(out,used,binding()).canonical())return false;
 const auto before=out;const auto view=p::decode(out,used,binding()).view;
 if(p::encode(binding(),view.pairRaw,view.signature,out,used)!=p::Error::Alias||out!=before)return false;
 std::array<uint8_t,32> tooSmall{};return p::encode(binding(),{pair.data(),1},{sig.data(),sig.size()},tooSmall,used)==p::Error::Bounds;
}static_assert(cases());
''')

    def test_checked_get_bounds_and_failure_preserves_output(self):
        self.compile(r'''
constexpr bool cases(){
 const std::array<uint8_t,9> raw{1,2,3,4,5,6,7,8,9};
 const p::Bytes bytes{raw.data(),raw.size()};uint64_t out=0x12345678;
 if(p::checkedGet({},0,1,out)||out!=0x12345678)return false;
 if(p::checkedGet({nullptr,9},0,8,out)||out!=0x12345678)return false;
 if(p::checkedGet(bytes,0,0,out)||out!=0x12345678)return false;
 if(p::checkedGet(bytes,0,9,out)||out!=0x12345678)return false;
 if(p::checkedGet(bytes,size_t(-1),1,out)||out!=0x12345678)return false;
 if(p::checkedGet(bytes,1,unsigned(-1),out)||out!=0x12345678)return false;
 if(p::checkedGet(bytes,raw.size(),1,out)||out!=0x12345678)return false;
 if(p::checkedGet(bytes,raw.size()+1,1,out)||out!=0x12345678)return false;
 if(p::checkedGet(bytes,2,8,out)||out!=0x12345678)return false;
 if(p::checkedGet({raw.data(),0},0,1,out)||out!=0x12345678)return false;
 if(!p::checkedGet(bytes,1,8,out)||out!=0x0908070605040302ULL)return false;
 if(!p::checkedGet(bytes,8,1,out)||out!=9)return false;
 if(!p::checkedGet(bytes,0,8,out)||out!=0x0807060504030201ULL)return false;
 return true;
}static_assert(cases());
''')

    def test_structural_decode_never_invents_JSON_crypto_or_SDK_authority(self):
        self.compile(r'''
constexpr bool cases(){const std::array<uint8_t,8> notJSON{'n','o','t','_','j','s','o','n'};
 const std::array<uint8_t,8> fakeCrypto{0x30,6,2,1,1,2,1,1};std::array<uint8_t,1536> out{};size_t used=0;
 if(p::encode(binding(),{notJSON.data(),notJSON.size()},{fakeCrypto.data(),fakeCrypto.size()},out,used)!=p::Error::None)return false;
 const auto decoded=p::decode(out,used,binding());
 return decoded.canonical()&&!decoded.signatureVerified&&!decoded.sdkVerified&&!decoded.actualFlashVerified;
}static_assert(cases());
''')


if __name__ == '__main__':
    unittest.main()
