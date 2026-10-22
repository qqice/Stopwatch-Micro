"""Independent receipt tests running the production C++ include on RV32."""
import unittest
import test_mosaico_factory_audit as audit
# Reuse the actual-inc compiler/emulator, replace only externally mocked cases.
FIXTURE=audit.FIXTURE.replace('static int fault=0,','static int failCommit=0;static int mismatch=-1;static int fault=0,').replace('++commits;', '++commits;if(failCommit&&commits>=failCommit)return 2;').replace('b[35]=36;', 'for(unsigned i=0;i<131;++i)if(int(i)==mismatch&&scale_recovery::layout[i].address==a)b[2]^=1;b[35]=36;')
FIXTURE=FIXTURE.replace('std::memcpy(p,savedReceipt.data(),80);*n=80;return 0;', 'std::memcpy(p,savedReceipt.data(),80);*n=fault==38?79:80;if(fault==36)static_cast<uint8_t*>(p)[79]^=1;if(fault==39&&reads%2)static_cast<uint8_t*>(p)[10]^=1;return 0;').replace('receiptExists=true;return 0;', 'receiptExists=true;return fault==37?2:0;')
CASES=audit.CASES.split('static int run_case(',1)[0]+r'''
static int run_case(int id){
 reset();if(id==178){using namespace mosaico_factory_audit;Receipt r{};r.sourceGeneration=1;r.access.generation=1;r.access.mac=hal.getFactoryMac();ReceiptBytes b{};Receipt d{};if(!encodeReceipt(r,b)||!decodeReceipt(b,r.access.mac,d))return 20;for(unsigned i=0;i<80;++i){b[i]^=1;if(decodeReceipt(b,r.access.mac,d))return 21;b[i]^=1;}return 0;}mismatch=-1;failCommit=0;char out[1024];if(!hal.gaugeFactoryAuditCapture(out,sizeof(out)))return 1;
 auto originalJournal=savedJournal;auto originalBackup=savedBackup;auto originalLegacy=legacyEvidence;
 factory_workspace=nullptr;sets=commits=reads=unsealWords=fullWords=seals=dmReads=0;available=100;
 // 0 success; 1..131 byte mismatch for every field, including 9180.
 if(id>=1&&id<=131)mismatch=id-1;
 if(id>=132&&id<=155)fault=id-132+8;
 if(id==156)available=91;
 if(id==157)validApp=false;
 if(id==158)lease=false;
 if(id==159)fw=false;
 if(id>=160&&id<=163){mosaico_factory_audit::Receipt r{};r.sourceGeneration=1;r.access.generation=1;r.access.mac=hal.getFactoryMac();r.access.sourceCrc=mosaico_factory_audit::get32(savedJournal.data()+28);r.access.state=id==160?mosaico_factory_audit::State::Pending:mosaico_factory_audit::State::Failed;mosaico_factory_audit::encodeReceipt(r,savedReceipt);receiptExists=true;if(id==162)savedReceipt[10]^=1;if(id==163)savedReceipt[0]='X';}
 if(id>=164&&id<=169)failCommit=id-163;
 if(id>=170&&id<=175){mosaico_factory_audit::Receipt r{};r.sourceGeneration=1;r.access.generation=1;r.access.mac=hal.getFactoryMac();r.access.sourceCrc=mosaico_factory_audit::get32(savedJournal.data()+28);r.access.flags=uint16_t((1U<<(id-169))-1);mosaico_factory_audit::encodeReceipt(r,savedReceipt);receiptExists=true;}
 if(id==176)fault=32;if(id==177)fault=33;
 if(id>=180)fault=id-180+36;
 const bool ok=hal.gaugeFactoryAuditRevalidate(out,sizeof(out));
 if(id==0||id==152||id==154||id==179){if(!ok||!factory_workspace->live||!factory_workspace->complete||dmReads!=262||unsealWords!=2||fullWords!=2||seals!=1||factory_workspace->record.generation!=1)return 2;
  if(id==179){factory_workspace=nullptr;unsealWords=fullWords=seals=dmReads=0;if(!hal.gaugeFactoryAuditRevalidate(out,sizeof(out))||factory_workspace->record.generation!=2||factory_workspace->sourceGeneration!=1)return 22;}
  auto receipt=savedReceipt;if(hal.gaugeFactoryAuditRevalidate(out,sizeof(out))||savedReceipt!=receipt)return 3;
  factory_workspace=nullptr;if(!hal.gaugeFactoryAuditRevalidationStatus(out,sizeof(out))||factory_workspace->live||factory_workspace->complete||hal.gaugeFactoryAuditField(0,out,sizeof(out)))return 4;
 }else{
  if(ok||factory_workspace->live||factory_workspace->complete)return 5;
  // Commit points4/5 fail read/Seal receipt intents after owned FA.
  // Cleanup must still Seal once, while never reporting success or freshness.
  if((id==167||id==168)&&(seals!=1||op!=0x26))return 23;
  if(id<=131){if(dmReads!=262||factory_workspace->record.count!=131||seals!=1||!hal.gaugeFactoryAuditRevalidationField(id-1,out,sizeof(out)))return 6;}
  if(id>=160&&id<=163&&(unsealWords||fullWords||seals||sets))return 7;
  // A durable Failed/Pending/corrupt receipt must block both APIs after reboot.
  if(receiptExists){factory_workspace=nullptr;auto u=unsealWords,f=fullWords,s=seals;if(hal.gaugeFactoryAuditRevalidate(out,sizeof(out))||hal.gaugeFactoryAuditCapture(out,sizeof(out))||unsealWords!=u||fullWords!=f||seals!=s)return 8;}
 }
 if(savedJournal!=originalJournal||savedBackup!=originalBackup||std::memcmp(&legacyEvidence,&originalLegacy,sizeof(legacyEvidence)))return 9;
 if(unsealWords>2||fullWords>2||seals>1||legacyWrites)return 10;
 return 0;
}
extern "C" {volatile int test_results[184]={};}
extern "C" __attribute__((noinline,used)) void test_done(){for(;;){asm volatile("");}}
extern "C" void _start(){for(int i=0;i<184;++i)test_results[i]=run_case(i);test_done();}
'''
class RevalidationTests(audit.FactoryTests):
 fixture=FIXTURE
 cases=CASES
 case_count=184
 run_directory=audit.ROOT/'artifacts/factory-revalidate-tests'
if __name__=='__main__':unittest.main()
