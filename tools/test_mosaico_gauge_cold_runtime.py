"""Execute staged cold supervisor inc on RV32 with mocked crypto/media/IC transport.

This is not hardware, cryptographic authenticity, or final HAL-hook certification.
"""
from pathlib import Path
import subprocess
import unittest
from unittest.mock import patch
import test_mosaico_gauge_contrast_runtime as base

ROOT=Path(__file__).resolve().parents[1]
RUN=ROOT/'artifacts/gauge-cold-runtime-tests'
MOCKS=r'''
namespace coldtest {
namespace m=mosaico_gauge_cold_trial;namespace e=mosaico_gauge_cold_envelope;namespace s=mosaico_gauge_cold_store;
static MosaicoGaugeCold::Runtime state;
static std::array<s::Page,14> pages{};static s::Scratch scratch{};
static unsigned mediaCalls=0,mediaWrites=0,frameReads=0,allocations=0;
static bool failSave=false,failReadback=false,cryptoGood=true,quietGood=true,batteryViolation=false,engineIncomplete=false;
static bool actualImagesGood=true;static unsigned actualVerifyCalls=0;
static uint32_t opaqueError=0;static uint8_t boot=10;static int resetReason=ESP_RST_POWERON;
static m::Binding binding(){m::Binding b{};b.mac=GetHAL().getFactoryMac();b.originalGctGeneration=1;b.originalGctEpoch=1;b.nonce[0]=1;b.slotHashes[0][0]=1;b.slotHashes[1][0]=2;b.source131ReceiptDigest[0]=3;return b;}
struct IO {uint32_t base;
 bool read(uint32_t i,s::Page& out){if(i>=8)i=base+i-8;if(i>=pages.size())return false;out=pages[i];return true;}
 bool write(uint32_t i,const s::Page& in){if(i<8||i>9)return false;i=base+i-8;pages[i]=in;++mediaWrites;return true;}bool sync(){return true;}};
}
void* heap_caps_calloc(size_t,size_t,int){++coldtest::allocations;return nullptr;}
int esp_reset_reason(){return coldtest::resetReason;}
namespace MosaicoOta {bool currentTaskOwnsStorageLease(){return leaseDepth!=0&&xTaskGetCurrentTaskHandle()==&coldTaskTag;}}
namespace MosaicoColdCrypto {
bool sha256(const uint8_t* p,size_t n,Digest& out){uint32_t h=2166136261U;for(size_t i=0;i<n;++i)h=(h^p[i])*16777619U;for(unsigned i=0;i<32;++i)out[i]=uint8_t((h>>(8*(i%4)))^i);return coldtest::cryptoGood;}
void prepareBootIdentity(){}mosaico_gauge_cold_trial::BootToken bootIdentity(){mosaico_gauge_cold_trial::BootToken b{};b.nonce[0]=coldtest::boot;b.verified=true;return b;}
bool extractBinding(const Record& r,Binding& b){b=coldtest::binding();return mosaico_gauge_cold_store::shape(r)&&r.sequence==1&&r.phase==1&&r.nonce==b.nonce;}
bool authenticate(const Record& r,const std::array<uint8_t,6>& mac,const mosaico_gauge_contrast::Raw& raw,Authenticated& out,const char*& reason){
 out={};out.binding=coldtest::binding();if(!coldtest::cryptoGood||mac!=out.binding.mac||raw!=mosaico_gauge_contrast::sourceFixture){reason="mock_auth_reject";return false;}
 out.bindingHash=r.binding;out.sessionVerified=true;reason="mock_session";return true;}
bool verifyActualImages(Authenticated& out,const char*& reason){++coldtest::actualVerifyCalls;out.actualImagesVerified=coldtest::cryptoGood&&coldtest::actualImagesGood;reason="mock_actual";return out.actualImagesVerified;}
bool buildFromFragment(const uint8_t*,size_t,const char*,Record& out,const char*&){out={};out.sequence=1;out.phase=1;out.nonce=coldtest::binding().nonce;out.binding[0]=55;out.size=1;out.payload[0]=99;return true;}
}
namespace MosaicoNandStore {
static Result result(mosaico_gauge_cold_store::Code code){Result r{};r.stage=Stage::Complete;r.coldCode=code;r.error=(code==mosaico_gauge_cold_store::Code::Ready||code==mosaico_gauge_cold_store::Code::Empty)?ESP_OK:9;return r;}
Result coldLoad(ColdArea area,ColdRecord& out,const ColdPolicy& p,bool authorized,bool inherit){
 ++coldtest::mediaCalls;coldtest::batteryViolation|=battery_mutex.held!=0;if(coldtest::failReadback&&coldtest::mediaWrites)return result(mosaico_gauge_cold_store::Code::Io);if(!authorized||!inherit||!MosaicoOta::currentTaskOwnsStorageLease())return result(mosaico_gauge_cold_store::Code::Rejected);
 coldtest::IO io{area==ColdArea::Journal?8U:12U};auto loaded=mosaico_gauge_cold_store::load(io,coldtest::scratch,p);
 if(loaded.code==mosaico_gauge_cold_store::Code::Ready)out=coldtest::scratch.records[unsigned(loaded.selected)];return result(loaded.code);}
Result coldSave(ColdArea area,const ColdRecord& in,const ColdPolicy& p,bool authorized,bool inherit){
 ++coldtest::mediaCalls;coldtest::batteryViolation|=battery_mutex.held!=0;if(coldtest::failSave)return result(mosaico_gauge_cold_store::Code::Io);
 if(!authorized||!inherit||!MosaicoOta::currentTaskOwnsStorageLease())return result(mosaico_gauge_cold_store::Code::Rejected);
 coldtest::IO io{area==ColdArea::Journal?8U:12U};return result(mosaico_gauge_cold_store::save(io,coldtest::scratch,in,p,true));}
}
void gauge_diagnostic_locked(mosaico_gauge_status::Snapshot& out,uint8_t phase,uint8_t outcome){++coldtest::frameReads;out={};out.phase=phase;out.outcome=outcome;out.sampledUs=nowUs;out.validMask=mosaico_gauge_status::AllRaw;gauge_word(0x3c,out.designMah);gauge_word(0x12,out.fullMah);out.voltageMv=4200;out.operationStatus=operation;}
void gauge_engine_locked(mosaico_gauge_engine::Snapshot& out,uint8_t phase,uint8_t outcome){++coldtest::frameReads;out={};out.phase=phase;out.outcome=outcome;out.sampledUs=nowUs;out.validMask=coldtest::engineIncomplete?0x1ffe:0x1fff;out.echo73=0x73;out.echo76=0x76;out.echo02=2;out.rawIdentity[0]=2;out.rawIdentity[1]=0x20;out.rawIdentity[3]=3;out.rawIdentity[5]=4;}
namespace coldtest {
static bool quietEnter(){return quietGood;}static bool quietLeave(){return true;}
static void coldReset(bool target=false){resetContrast();if(target){(void)contrastApply();writes=enters=defaultKeyCalls=0;}
 state.~Runtime();new(&state)MosaicoGaugeCold::Runtime{};MosaicoGaugeCold::runtime=&state;MosaicoGaugeCold::protection.store(false);MosaicoGaugeCold::allowFallback.store(true);MosaicoGaugeCold::prefetchSet=false;MosaicoGaugeCold::startupEntered=false;MosaicoGaugeCold::quietHooks={};
 for(auto& p:pages)p.fill(255);s::nand::marker(pages[0]);s::nand::marker(pages[1]);mediaCalls=mediaWrites=frameReads=allocations=0;failSave=failReadback=engineIncomplete=false;cryptoGood=quietGood=actualImagesGood=true;actualVerifyCalls=0;batteryViolation=false;boot=10;opaqueError=0;resetReason=ESP_RST_POWERON;nowUs=1000000;leaseDepth=1;
 state.authenticated.binding=binding();state.authenticated.bindingHash[0]=55;state.authenticated.sessionVerified=state.authenticated.actualImagesVerified=true;state.bound=true;state.nonceFresh=true;
}
static m::Record armed(){m::Record r{};r.binding=binding();r.sequence=1;r.armedBoot.nonce[0]=10;r.armedBoot.verified=true;r.armedMs=1000;return r;}
static m::Record handed(){auto r=armed();r.sequence=2;r.phase=m::Phase::HandedOff;r.handoffMs=1100;return r;}
static void seedProof(){s::Record p{};p.sequence=p.phase=1;p.nonce=binding().nonce;p.binding[0]=55;p.size=1;p.payload[0]=99;s::encode(p,pages[12]);}
static bool seed(const m::Record& r,const e::Frames& f={},uint8_t count=0){s::Record p{};if(!e::encode(r,f,count,binding(),state.authenticated.bindingHash,MosaicoColdCrypto::Hasher{},p))return false;return s::encode(p,pages[8+((r.sequence-1)%2)]);}
static void prefetch(){state.frame={};state.frame.kind=mosaico_gauge_cold_evidence::Kind::Initial;state.frame.bootNonce=MosaicoColdCrypto::bootIdentity().nonce;gauge_diagnostic_locked(state.frame.standard,0,0);gauge_engine_locked(state.frame.engine,0,0);MosaicoGaugeCold::earlyPrefetch(state.frame.standard,state.frame.engine,opaqueError);frameReads=0;}
static bool seedHanded(){seedProof();if(!seed(armed())||!seed(handed()))return false;boot=11;nowUs=10000;prefetch();return true;}
static bool finalCapture(){MosaicoGaugeCold::RestoreRequest r{};nowUs=25010000;return MosaicoGaugeCold::poll(&r)&&state.durable&&state.decoded.metadata.phase==m::Phase::Captured&&!r.requested;}
}
extern "C" int run_case(int id){if(id>=35)return 0;using namespace coldtest;using namespace MosaicoGaugeCold;
 coldReset(id>=5&&id!=15&&id!=16&&id!=17&&id!=18&&id!=34);
 if(id==0){std::unique_lock<std::mutex> b(battery_mutex);auto before=mediaCalls;return !armSeam(b)||mediaCalls!=before?1:0;}
 if(id==1){MosaicoGaugeCold::runtime=nullptr;return !startup()||mediaCalls||allocations||protectedState()?2:0;}
 if(id==2||id==3||id==4||id==15||id==16||id==17||id==18){contrast_begin_locked();ContrastBackend old;if(!old.admit(false)||!old.intent(false)||!old.open(false)||!old.observe(false))return 3;
  state.applyInFlight=true;state.operationOwner=xTaskGetCurrentTaskHandle();failSave=id==3;failReadback=id==18;
  std::unique_lock<std::mutex> b(battery_mutex);bool ok=armSeam(b);
  if(id==3||id==18)return ok||!b.owns_lock()||fallbackAllowed()||writes||enters?4:0;
  if(!ok||!state.durable||!state.session.applyReleased||state.decoded.metadata.armedMs!=uint64_t(contrast_rt->appliedUs/1000)||defaultKeyCalls!=4||writes||enters||batteryViolation)return 5;
  if(id==4){state.sourceAfterArmed=0;state.session.applyReleased=false;return armSeam(b)||fallbackAllowed()?6:0;}
  if(id>=15){
   if(!old.enter()||!old.write(false)||!old.verify(false)||!old.exit()||!old.seal()||!old.final(false)||!old.complete(false))return 27;
   contrastStorage.operation=false;b.unlock();onApplied(true);if(!state.targetReceipt||!state.applied)return 28;
   nowUs+=id==16?61000000:10000000;
   if(id==17&&!contrast_rt_intent(scale_recovery::Phase::Restoring))return 37;
   char reportBuffer[1024];const auto beforeWrites=mediaWrites;bool handedOk=handoff(reportBuffer,sizeof(reportBuffer));
   if(id==16)return handedOk||mediaWrites!=beforeWrites?29:0;
   if(id==17)return handedOk?30:0;
   if(!handedOk||state.context.safety.freshAuthenticated131||state.decoded.metadata.phase!=m::Phase::HandedOff)return 31;
   setQuietHooks({quietEnter,quietLeave});RestoreRequest r{};nowUs+=29999000;poll(&r);if(r.requested)return 32;
   nowUs+=1000;poll(&r);return !r.requested||state.decoded.metadata.armedMs!=1000||defaultKeyCalls!=4?33:0;
  }return 0;
 }
 if(id==26||id==27){if(id==26){coldReset(false);contrast_begin_locked();ContrastBackend old;if(!old.admit(false)||!old.intent(false))return 47;}seedProof();return startup()||!protectedState()||fallbackAllowed()||defaultKeyCalls?48:0;}
 if(id==30){coldReset(false);contrast_begin_locked();state.applyInFlight=true;state.operationOwner=xTaskGetCurrentTaskHandle();MosaicoGaugeCold::protection.store(true);const auto calls=mediaCalls;
  if(!keyWindowAllowed())return 49;ContrastBackend old;if(!old.admit(false)||!old.intent(false)||!keyWindowAllowed())return 50;const auto reads=readCalls;
  nowUs=contrast_rt->appliedUs+600000000;if(keyWindowAllowed())return 51;nowUs=contrast_rt->appliedUs+599999999;if(!keyWindowAllowed())return 52;
  ++contrast_rt->appliedUs;return keyWindowAllowed()||mediaCalls!=calls||readCalls!=reads?53:0;
 }
 if(id==34){auto changed=contrastStorage.record;++changed.snapshot.generation;++changed.snapshot.epoch;if(contrast_journal::saveNvs(changed,{true,true,true},contrastStorage.journal)!=contrast_journal::Status::Ready)return 67;char buffer[1024];if(apply(buffer,sizeof(buffer))||defaultKeyCalls||writes||enters||mediaCalls||protectedState())return 68;return 0;}
 if(id==25){if(!seed(armed())||!seed(handed()))return 45;return startup()||!protectedState()||fallbackAllowed()||defaultKeyCalls?46:0;}
 if(id==5){return !startup()||protectedState()||!fallbackAllowed()||defaultKeyCalls?7:0;}
 if(id==6){seedProof();pages[12][100]^=1;return startup()||!protectedState()||fallbackAllowed()||defaultKeyCalls?8:0;}
 if(id==23){coldReset(false);state.bound=false;seedProof();if(!seed(armed())||!seed(handed()))return 38;char buffer[1024];if(!status(buffer,sizeof(buffer))||protectedState()||!fallbackAllowed()||!state.durable||state.decoded.metadata.phase!=m::Phase::HandedOff||!mediaCalls||defaultKeyCalls)return 39;return 0;}
 if(id==21)engineIncomplete=true;
 if(id==28)opaqueError=0xdeadbeef;
 if(id==33){if(!seedHanded())return 63;nowUs=2000000;if(!contrast_rt_intent(scale_recovery::Phase::Restoring)||!contrast_rt_intent(scale_recovery::Phase::Original)||!contrast_rt_intent(scale_recovery::Phase::Pending)||!contrast_rt_intent(scale_recovery::Phase::Applied))return 64;if(contrast_rt->record.snapshot.generation!=binding().originalGctGeneration+6)return 65;nowUs=10000;const auto before=mediaWrites;bool ok=startup();return ok||!state.blocked||fallbackAllowed()||state.session.claimReleased||state.decoded.metadata.phase==m::Phase::CaptureClaimed||mediaWrites!=before||defaultKeyCalls?66:0;}
 if(!seedHanded()||!startup())return 9;
 if(id==32){state.frame=MosaicoGaugeCold::prefetched;state.frame.kind=mosaico_gauge_cold_evidence::Kind::Final;state.frame.error=0xfedcba98;state.frame.engine.validMask=0x1ffe;const auto sample=state.frame.standard.sampledUs;if(!MosaicoGaugeCold::appendFailure(m::CaptureResult::KnownReadFailure,false,true))return 61;mosaico_gauge_cold_evidence::Frame last{};return !mosaico_gauge_cold_evidence::decode(state.decoded.frames[1],last)||last.error!=0xfedcba98||last.standard.sampledUs!=sample||last.engine.validMask!=0x1ffe?62:0;}
 if(id==28){nowUs=30010000;poll(nullptr);mosaico_gauge_cold_evidence::Frame first{},last{};return state.decoded.metadata.capture!=m::CaptureResult::KnownTimeout||!mosaico_gauge_cold_evidence::decode(state.decoded.frames[0],first)||!mosaico_gauge_cold_evidence::decode(state.decoded.frames[1],last)||first.error!=0xdeadbeef||last.error!=0xdeadbeef||last.standard.sampledUs!=first.standard.sampledUs||last.resetReason!=first.resetReason?54:0;}
 if(id==29){MosaicoGaugeCold::prefetched.bootNonce[0]=99;const auto writes=mediaWrites;nowUs=30010000;poll(nullptr);return !state.blocked||mediaWrites!=writes||MosaicoGaugeCold::prefetched.bootNonce[0]!=99||defaultKeyCalls?55:0;}
 if(id==31){setQuietHooks({quietEnter,quietLeave});sdkValid=false;if(!finalCapture())return 56;sdkValid=true;RestoreRequest r{};poll(&r);if(!r.requested)return 57;const auto calls=mediaCalls,reads=readCalls;nowUs=119999999;if(!keyWindowAllowed())return 58;nowUs=120000000;if(keyWindowAllowed())return 59;return mediaCalls!=calls||readCalls!=reads||defaultKeyCalls?60:0;}
 if(id==21){engineIncomplete=false;nowUs=25010000;poll(nullptr);mosaico_gauge_cold_evidence::Frame initial{},last{};if(state.decoded.metadata.capture!=m::CaptureResult::KnownReadFailure||!mosaico_gauge_cold_evidence::decode(state.decoded.frames[0],initial)||!mosaico_gauge_cold_evidence::decode(state.decoded.frames[1],last)||initial.engine.validMask!=0x1ffe||last.engine.validMask!=0x1fff||defaultKeyCalls)return 40;return 0;}
 if(id==22){auto i=fieldIndex(0x929d);gaugeRaw[i][0]=0x0b;gaugeRaw[i][1]=0xb8;nowUs=25010000;poll(nullptr);mosaico_gauge_cold_evidence::Frame last{};if(state.decoded.metadata.capture!=m::CaptureResult::KnownReadFailure||!mosaico_gauge_cold_evidence::decode(state.decoded.frames[1],last)||last.standard.fullMah!=3000||defaultKeyCalls)return 41;return 0;}
 if(id==24){setQuietHooks({quietEnter,quietLeave});sdkValid=false;if(!finalCapture())return 42;sdkValid=true;RestoreRequest r{};poll(&r);if(!r.requested)return 43;state.~Runtime();new(&state)MosaicoGaugeCold::Runtime{};MosaicoGaugeCold::runtime=&state;MosaicoGaugeCold::startupEntered=false;boot=12;nowUs=10000;const auto before=mediaWrites;if(startup()||!state.blocked||fallbackAllowed()||defaultKeyCalls||mediaWrites!=before)return 44;return 0;}
 if(id==19){engineIncomplete=true;if(!poll(nullptr))return 34;nowUs=25010000;poll(nullptr);mosaico_gauge_cold_evidence::Frame failed{};if(!state.durable||state.decoded.metadata.capture!=m::CaptureResult::KnownReadFailure||!mosaico_gauge_cold_evidence::decode(state.decoded.frames[1],failed)||failed.engine.validMask!=0x1ffe||failed.engine.sampledUs!=25010000||defaultKeyCalls)return 35;return 0;}
 if(id==20){nowUs=30010000;poll(nullptr);return state.decoded.metadata.capture!=m::CaptureResult::KnownTimeout||frameReads||defaultKeyCalls?36:0;}
 if(id==7){return !state.session.claimReleased||state.decoded.metadata.phase!=m::Phase::CaptureClaimed||state.frameCount!=1||defaultKeyCalls||batteryViolation?10:0;}
 if(id==8){auto calls=mediaCalls;return !startup()||calls!=mediaCalls||state.session.captureFinished?11:0;}
 if(id==9){if(!finalCapture()||frameReads!=2||defaultKeyCalls||state.decoded.count!=2||!state.durable)return 12;return 0;}
 if(id==10){if(!finalCapture())return 13;MosaicoGaugeCold::onRestoreFailed();return state.blocked?14:0;}
 if(id==11){ // Second boot sees durable claim: interrupted evidence, never final recapture.
  const auto initial=state.frames[0];state.~Runtime();new(&state)MosaicoGaugeCold::Runtime{};MosaicoGaugeCold::runtime=&state;MosaicoGaugeCold::startupEntered=false;MosaicoGaugeCold::prefetchSet=false;boot=12;nowUs=10000;prefetch();
  if(!startup()||state.decoded.metadata.capture!=m::CaptureResult::Interrupted||state.decoded.count!=2||state.decoded.frames[0]!=initial||frameReads||defaultKeyCalls)return 15;return 0;
 }
 if(id==12){setQuietHooks({quietEnter,quietLeave});sdkValid=false;if(!finalCapture())return 16;RestoreRequest r{};poll(&r);if(r.requested||defaultKeyCalls)return 17;sdkValid=true;poll(&r);return !r.requested||!state.session.inspectionReleased||defaultKeyCalls||state.decoded.metadata.phase!=m::Phase::Restoring?18:0;}
 if(id==13){setQuietHooks({quietEnter,quietLeave});sdkValid=false;if(!finalCapture())return 19;sdkValid=true;RestoreRequest r{};poll(&r);if(!r.requested)return 20;onRestoreFailed();return !state.blocked||fallbackAllowed()?21:0;}
 if(id==14){setQuietHooks({quietEnter,quietLeave});sdkValid=false;if(!finalCapture())return 22;sdkValid=true;RestoreRequest r{};poll(&r);if(!r.requested)return 23;
  contrast_begin_locked();ContrastBackend old;if(!old.admit(true)||!old.intent(true)||!old.open(true)||!old.observe(true))return 24;
  {std::unique_lock<std::mutex> b(battery_mutex);if(!restoreSeam(b)||!old.enter()||!old.write(true)||!old.verify(true)||!old.exit()||!old.seal()||!old.final(true)||!old.complete(true))return 25;}
  if(!onRestored(contrast_rt->record.snapshot,contrast_rt->observed,true)||protectedState()||state.decoded.metadata.phase!=m::Phase::Restored||state.decoded.count!=2||writes!=3||batteryViolation)return 26;return 0;
 }
 return 0;
}
'''


HOOK_CASES=r"""
int mbedtls_base64_encode(unsigned char* out,size_t cap,size_t* count,const unsigned char* data,size_t n){static constexpr char abc[]="ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/";*count=((n+2)/3)*4;if(cap<=*count)return -1;size_t j=0;for(size_t i=0;i<n;i+=3){unsigned v=unsigned(data[i])<<16;if(i+1<n)v|=unsigned(data[i+1])<<8;if(i+2<n)v|=data[i+2];out[j++]=abc[(v>>18)&63];out[j++]=abc[(v>>12)&63];out[j++]=i+1<n?abc[(v>>6)&63]:'=';out[j++]=i+2<n?abc[v&63]:'=';}out[j]=0;return 0;}

namespace { static unsigned coldDeadlineReads=0;
void coldAfterWord(uint8_t reg){if(reg==0x3a&&coldDeadlineReads&&!--coldDeadlineReads)nowUs=contrast_rt->appliedUs+600000000;}}
extern "C" int run_case(int id){
 if(id>=22)return 0;
 using namespace coldtest;using namespace MosaicoGaugeCold;
 coldReset(false);coldDeadlineReads=0;coldForeignTask=false;
 if(id==0&&(!contrast_rt_version("0.13.95-r21-mosaico-ota")||!contrast_rt_version("0.13.95-r22-mosaico-ota")||contrast_rt_version("0.13.95-r17-mosaico-ota")||contrast_rt_version("0.13.95-r18-mosaico-ota")))return 131;
 if(id==20||id==21){const auto before=mediaWrites;std::snprintf(contrast_rt->images[0].version,sizeof(contrast_rt->images[0].version),"%s","0.13.95-r17-mosaico-ota");
  if(id==21)std::snprintf(contrast_rt->images[1].version,sizeof(contrast_rt->images[1].version),"%s","0.13.95-r18-mosaico-ota");
  return contrast_rt_images_current()||defaultKeyCalls||writes||mediaWrites!=before?132:0;}

 if(id==19){coldForeignTask=true;const auto before=mediaCalls;{ContrastStorageLease denied;if(denied.held)return 129;}coldForeignTask=false;return leaseDepth!=1||!MosaicoOta::currentTaskOwnsStorageLease()||mediaCalls!=before||defaultKeyCalls?130:0;}
 if(id>=14){
  state.bound=false;char out[1024];capMode=true;
  if(!status(out,sizeof(out))||!state.historicalOnly)return 120;
  if(id==15)seedProof();
  if(id==16&&!seed(armed()))return 121;
  if(id==17)failSave=true;
  const auto before=mediaWrites;
  bool bound=bindFragment("cap",out,sizeof(out));
  if(id==15||id==16)return bound||state.blocked||mediaWrites!=before||protectedState()||!fallbackAllowed()||defaultKeyCalls?122:0;
  if(id==17)return bound||!state.blocked||state.bound||protectedState()||!fallbackAllowed()||defaultKeyCalls||recover(out,sizeof(out))||apply(out,sizeof(out))?123:0;
  if(!bound||!state.bound||state.historicalOnly||!state.authenticated.actualImagesVerified)return 124;
  if(id==18){auto next=contrast_rt->record;++next.snapshot.generation;++next.snapshot.epoch;if(contrast_journal::saveNvs(next,{true,true,true},contrast_rt->journal)!=contrast_journal::Status::Ready)return 125;const auto calls=mediaCalls;return status(out,sizeof(out))||mediaCalls!=calls||defaultKeyCalls?126:0;}
  const auto calls=mediaCalls;
  if(!status(out,sizeof(out))||mediaCalls!=calls||!state.bound||state.historicalOnly||!state.authenticated.actualImagesVerified)return 127;
  if(!apply(out,sizeof(out))||!state.applied||!state.durable||defaultKeyCalls!=4||writes!=3||leaseDepth!=1||leaseViolation||!MosaicoOta::currentTaskOwnsStorageLease())return 128;
  return 0;
 }

 if(id==7){char out[1024];const auto calls=mediaCalls,reads=readCalls;return evidencePage(0,out,sizeof(out))||mediaCalls!=calls||readCalls!=reads?91:0;}
 contrast_begin_locked();state.applyInFlight=true;state.operationOwner=xTaskGetCurrentTaskHandle();MosaicoGaugeCold::protection.store(true);
 std::unique_lock<std::mutex> lock(battery_mutex);ContrastBackend old(&lock);
 if(id==8||id==9){coldOnceReadFault=id==8;commitAckLossOnce=id==9;auto result=mosaico_gauge_contrast_trial::apply(old);if(id==9)return result!=mosaico_gauge_contrast_trial::Result::FailedClosed||!contrast_rt->coldMutatorAckUnknown||defaultKeyCalls!=4||seals||fallbackAllowed()?82:0;if(result!=mosaico_gauge_contrast_trial::Result::Restored||!state.ownedAbort||defaultKeyCalls!=4||contrast_rt->record.snapshot.generation!=binding().originalGctGeneration+3||writes!=2)return 83;lock.unlock();if(!onRestored(contrast_rt->record.snapshot,contrast_rt->observed,true))return 84;onApplied(false);return protectedState()||state.blocked||state.decoded.metadata.phase!=m::Phase::Restored?85:0;}

 if(!old.admit(false)){const char* reasons[]={"cold_exclusive_backend_owner_required","cold_owned_operation_deadline_or_owner","actual_SDK_VALID_quiet_radios_phase2_required","apply_explicit_prepare_and_two_audited_images_required","contrast_requires_GSJ2_missing_GSJ3_missing_or_Original","apply_nvs_available_entries_headroom"};for(unsigned z=0;z<6;++z)if(!std::strcmp(contrast_rt->reason,reasons[z]))return 110+z;return 101;}if(!old.intent(false))return 102;if(!old.open(false))return 103;if(!old.observe(false))return 104;if(!state.durable)return 105;if(!state.session.applyReleased)return 106;if(!old.enter())return 107;
 if(id==0){nowUs=contrast_rt->appliedUs+599999999;coldDeadlineReads=2;const auto count=writes;if(old.write(false)||writes!=count||contrast_rt->possibleDirty||contrast_rt->coldMutatorAckUnknown)return 72;coldDeadlineReads=0;}
 else if(!old.write(false))return 73;
 if(id==1){contrast_rt->coldMutatorAckUnknown=true;const auto keys=defaultKeyCalls,sealCount=seals;auto result=mosaico_gauge_contrast_trial::restore(old);return result==mosaico_gauge_contrast_trial::Result::Restored||defaultKeyCalls!=keys||seals!=sealCount||fallbackAllowed()?74:0;}
 if(id==2){boot=11;auto result=mosaico_gauge_contrast_trial::restore(old);return result==mosaico_gauge_contrast_trial::Result::Restored||defaultKeyCalls!=4?75:0;}
 if(id==3){auto i=fieldIndex(0x9184);gaugeRaw[i][0]^=1;}
 if(id==4){auto i=fieldIndex(0x9264);gaugeRaw[i][0]=0x12;gaugeRaw[i][1]=0x34;}
 const auto keys=defaultKeyCalls;auto result=mosaico_gauge_contrast_trial::restore(old);
 if(id==3||id==4)return result==mosaico_gauge_contrast_trial::Result::Restored||defaultKeyCalls!=keys?76:0;
 if(result!=mosaico_gauge_contrast_trial::Result::Restored||defaultKeyCalls!=keys||!state.ownedAbort||contrast_rt->record.snapshot.generation!=binding().originalGctGeneration+3)return 77;
 lock.unlock();if(id==5)failSave=true;
 bool closed=onRestored(contrast_rt->record.snapshot,contrast_rt->observed,true);
 if(id==5){onApplied(false);char out[1024];return closed||protectedState()||!state.metadataClosureFailed||!state.verifiedOriginalClosure||!state.blocked||fallbackAllowed()||!evidencePage(0,out,sizeof(out))?78:0;}
 if(!closed||protectedState()||state.decoded.metadata.phase!=m::Phase::Restored||state.decoded.metadata.sequence!=3)return 79;
 if(id>=10){
  seedProof();
  if(id==12){for(unsigned j=0;j<3;++j){auto next=contrast_rt->record;++next.snapshot.generation;++next.snapshot.epoch;if(contrast_journal::saveNvs(next,{true,true,true},contrast_rt->journal)!=contrast_journal::Status::Ready)return 92;contrast_rt->record=next;}}
  state.~Runtime();new(&state)MosaicoGaugeCold::Runtime{};MosaicoGaugeCold::runtime=&state;MosaicoGaugeCold::startupEntered=false;
  contrastStorage=ContrastRuntime{};contrast_rt=&contrastStorage;contrast_status=contrast_journal::Status::IoError;boot=11;nowUs=10000;
  const auto calls=mediaCalls;if(!startup()||mediaCalls!=calls)return 93;
  actualImagesGood=id!=11;cryptoGood=id!=13;actualVerifyCalls=0;
  const auto before=mediaWrites,keyCount=defaultKeyCalls,parameterCount=writes;
  char out[1024];bool ok=status(out,sizeof(out));
  if(id==13)return ok||actualVerifyCalls||mediaWrites!=before||defaultKeyCalls!=keyCount||protectedState()||!fallbackAllowed()?94:0;
  if(!ok||!state.historicalOnly||state.authenticated.actualImagesVerified||actualVerifyCalls||!state.durable||state.bound||ownsOperation()||state.session.inspectionReleased||state.session.restoreReleased||protectedState()||!fallbackAllowed()||!evidencePage(0,out,sizeof(out)))return 95;
  if(recover(out,sizeof(out))||handoff(out,sizeof(out))||apply(out,sizeof(out))||state.restoreWanted||state.applyInFlight||mediaWrites!=before||defaultKeyCalls!=keyCount||writes!=parameterCount)return 96;
  return 0;
 }
 if(id==6){char out[1024];const auto calls=mediaCalls,reads=readCalls;const auto saved=state.saved;bool ok=evidencePage(0,out,sizeof(out));if(!ok||mediaCalls!=calls||readCalls!=reads||state.saved.payload!=saved.payload||evidencePage(5,out,sizeof(out)))return 80;state.saved.payload[0]^=1;return evidencePage(0,out,sizeof(out))?81:0;}
 return 0;
}
"""


class ColdRuntimeTests(unittest.TestCase):
    def test_source_boundaries(self):
        source=(ROOT/'main/hal/mosaico/mosaico_gauge_cold_runtime.inc').read_text()
        for forbidden in ('gauge_access_word(', 'gauge_control(', 'i2c_master_transmit(', 'nvs_set_', 'formatConfirm(', 'setTemporary(', 'restoreTemporary('):
            self.assertNotIn(forbidden,source)
        self.assertIn('BatteryPause pause(battery)',source)
        self.assertIn('env::Workspace codec{}',source)
        self.assertEqual(source.count('EnvelopePolicy typed{t.authenticated.binding,t.authenticated.bindingHash,{},&t.codec}'),3)
        self.assertIn('crypto::Hasher{},t.next,t.codec)',source)
        self.assertIn('crypto::Hasher{},t.decoded,t.codec)',source)
        self.assertIn('now-t.forwardStartUs<600000000',source)
        self.assertNotIn('t.frame.error=uint32_t(result)',source)
        self.assertIn('sourceAfterArmed=t.decoded.metadata.sequence',source)
        self.assertIn('cold_after_Armed_commit_source131',source)
        self.assertIn('cold_reboot_Restoring_unresolved_keys_no_Session_waiver',source)
        self.assertIn('trial::authorizeRestoreInspection',source)
        self.assertIn('trial::authorizeRestore(',source)
        self.assertIn('raw_body_durable',source)
        self.assertIn('normalboot_radiohold_confound=1',source)

    def test_production_hooks_known_abort_rv32(self):
        self.test_production_inc_state_machine_rv32(True)

    def test_production_inc_state_machine_rv32(self, hooks=False):
        # Reuse the proven production contrast harness. Only mock dependencies,
        # insert the NEW actual inc and replace cases; never rewrite production.
        before_cases,before_run=base.CASES,base.RUN
        base.CASES=before_cases.split('extern "C" int run_case(int id){',1)[0]+(MOCKS.split('extern "C" int run_case(int id){',1)[0]+HOOK_CASES if hooks else MOCKS)
        base.RUN=RUN/'hooks' if hooks else RUN
        original=subprocess.run
        def compile_modified(args,*a,**kw):
            if any(str(v).endswith('runtime.cpp') for v in args):
                path=next(Path(v) for v in args if str(v).endswith('runtime.cpp'))
                code=path.read_text()
                code=code.replace('void gauge_engine_locked(Hal::GaugeEngineSnapshot& s,uint8_t p,uint8_t o){mockEngine(s,p,o);}', '')
                injection=r'''
#include "mosaico_gauge_cold_runtime.h"
using TaskHandle_t=const void*;static int coldTaskTag,coldForeignTaskTag;static bool coldForeignTask=false;
TaskHandle_t xTaskGetCurrentTaskHandle(){return coldForeignTask?&coldForeignTaskTag:&coldTaskTag;}
void* heap_caps_calloc(size_t,size_t,int);
namespace MosaicoOta {bool currentTaskOwnsStorageLease();}
void gauge_diagnostic_locked(mosaico_gauge_status::Snapshot&,uint8_t,uint8_t);
void gauge_engine_locked(mosaico_gauge_engine::Snapshot&,uint8_t,uint8_t);
#include "mosaico_gauge_cold_runtime.inc"
'''
                code=code.replace('#include "mosaico_gauge_contrast_runtime.inc"','#include "mosaico_gauge_contrast_runtime.inc"\n'+injection)
                if hooks:
                    code=code.replace('0.13.95-r15-mosaico-ota','0.13.95-r21-mosaico-ota').replace('0.13.95-r16-mosaico-ota','0.13.95-r22-mosaico-ota')
                    code=code.replace('#include "mosaico_gauge_contrast_runtime.inc"','#define MOSAICO_GAUGE_COLD_RUNTIME 1\n#include "mosaico_gauge_cold_runtime.h"\nnamespace MosaicoOta {bool currentTaskOwnsStorageLease();}\n#include "mosaico_gauge_contrast_runtime.inc"')
                    code=code.replace('static bool readModel(uint16_t a,uint8_t* b){++readCalls;', 'static bool coldOnceReadFault=false;static bool readModel(uint16_t a,uint8_t* b){++readCalls;if(coldOnceReadFault&&writes==1&&(operation&0x400)){coldOnceReadFault=false;return false;}')
                    code=code.replace('bool gauge_word(uint8_t r,uint16_t& v){return mockGaugeWord(r,v);}', 'void coldAfterWord(uint8_t);bool gauge_word(uint8_t r,uint16_t& v){bool ok=mockGaugeWord(r,v);coldAfterWord(r);return ok;}')
                path.write_text(code)
                temp=path.parent
                (temp/'esp_err.h').write_text('#pragma once\n')
                (temp/'esp_system.h').write_text('#pragma once\nenum {ESP_RST_POWERON=1,ESP_RST_SW=2,ESP_RST_BROWNOUT=3,ESP_RST_TASK_WDT=4,ESP_RST_INT_WDT=5,ESP_RST_WDT=6};int esp_reset_reason();\n')
                (temp/'limits').write_text('#pragma once\n')
                traits=(temp/'type_traits').read_text()
                if 'is_constant_evaluated' not in traits:
                    traits+='\nnamespace std {constexpr bool is_constant_evaluated() noexcept{return __builtin_is_constant_evaluated();}}\n'
                (temp/'type_traits').write_text(traits)
                (temp/'ota/mosaico_ota.h').write_text('#pragma once\n')
                array=(temp/'array').read_text().replace('constexpr size_t size()const', 'constexpr void fill(const T& v){for(size_t i=0;i<N;++i)values[i]=v;}constexpr size_t size()const')
                (temp/'array').write_text(array)
                atomic=(temp/'atomic').read_text().replace('memory_order){','memory_order=memory_order_release){').replace('load(memory_order)const','load(memory_order=memory_order_acquire)const')
                (temp/'atomic').write_text(atomic)
                (temp/'mutex').write_text('''#pragma once
namespace std {struct mutex{unsigned held=0;void lock(){++held;}void unlock(){--held;}bool try_lock(){++held;return true;}};using recursive_mutex=mutex;struct try_to_lock_t{};inline constexpr try_to_lock_t try_to_lock{};template<class T>struct lock_guard{T& m;explicit lock_guard(T& x):m(x){m.lock();}~lock_guard(){m.unlock();}};template<class T>struct unique_lock{T& m;bool own=true;explicit unique_lock(T& x):m(x){m.lock();}unique_lock(T& x,try_to_lock_t):m(x){m.lock();}~unique_lock(){if(own)m.unlock();}bool owns_lock()const{return own;}void unlock(){m.unlock();own=false;}void lock(){m.lock();own=true;}};}
''')
            return original(args,*a,**kw)
        try:
            with patch.object(base.subprocess,'run',side_effect=compile_modified):
                base.ContrastRuntimeTests('test_actual_runtime_rv32').test_actual_runtime_rv32()
            stack=(base.RUN/'runtime.su').read_text()
            own=[int(line.split('\t')[1]) for line in stack.splitlines() if 'mosaico_gauge_cold_runtime.inc:' in line]
            self.assertTrue(own)
            public=[int(line.split('\t')[1]) for line in stack.splitlines() if 'mosaico_gauge_cold_runtime.inc:' in line and 'GLOBAL' not in line]
            self.assertLessEqual(max(public),512) # Shared envelope scratch is a separately reported deployment gate.
            with (base.RUN/'execution.log').open('a') as log:
                log.write('meaningful_hook_cases=22 inherited_empty_tail_cases=33\n' if hooks else 'meaningful_cold_cases=35 inherited_empty_tail_cases=20\n')
        finally:
            base.CASES,base.RUN=before_cases,before_run


if __name__=='__main__':unittest.main()
