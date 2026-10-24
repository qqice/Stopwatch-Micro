// Included AFTER mosaico_gauge_contrast_runtime.inc in the board HAL.
#include "mosaico_gauge_cold_runtime.h"
#include "mosaico_gauge_cold_abort_model.h"
#include "mosaico_gauge_cold_crypto.h"
#include "mosaico_gauge_cold_envelope.h"
#include "mosaico_nand_store.h"
#include <esp_system.h>
#include <atomic>
#include <new>

namespace MosaicoGaugeCold {
namespace {
namespace trial=mosaico_gauge_cold_trial;
namespace env=mosaico_gauge_cold_envelope;
namespace evidence=mosaico_gauge_cold_evidence;
namespace store=mosaico_gauge_cold_store;
namespace crypto=MosaicoColdCrypto;
namespace abortModel=mosaico_gauge_cold_abort;
using Nand=MosaicoNandStore::ColdRecord;
struct Runtime {
 crypto::Authenticated authenticated{};
 Nand proof{},saved{},next{},verify{};
 env::Decoded decoded{};
 env::Workspace codec{};
  // Exclusively owned PSRAM scratch, never convenience stack path.
 env::Frames frames{};
 trial::Context context{};
 trial::Session session{};
 trial::Decision decision{};
 trial::Record before{};
 trial::Digest bodyHash{},nextBodyHash{},actualHash{};
 mosaico_gauge_contrast::Raw source{},targetCache{};
 uint32_t targetGeneration=0;
 uint64_t targetEpoch=0,targetStartedMs=0,targetObservedMs=0;
 bool targetReceipt=false;
 evidence::Frame frame{},initialFrame{};
 uint8_t frameCount=0,fragment[384]{};
 bool loaded=false,durable=false,bound=false,applyInFlight=false,applied=false,historicalOnly=false;
 bool blocked=false,restoreWanted=false,restoreIssued=false,finalAttempted=false;
 bool quietRequested=false,quietOwned=false,quietFailed=false,nonceFresh=false;
 bool restoreIntentKnownThisBoot=false,ownedAbort=false,verifiedOriginalClosure=false,metadataClosureFailed=false;
 uint32_t sourceAfterArmed=0;
 int64_t nextSafetyMs=0,forwardStartUs=0;
 TaskHandle_t operationOwner=nullptr;
 const char* reason="not_loaded";
};
Runtime* runtime=nullptr;
// Candidate-only PSRAM is separate from the active Runtime. Serialized by the
// same gauge transaction mutex; never passed to coldSave/load/commit by verify.
struct CandidateWorkspace {
 crypto::Record proof{};crypto::Authenticated authenticated{};
 mosaico_gauge_contrast::Raw source{};uint8_t fragment[384]{};
 std::array<uint8_t,6> physical{},legacy{};
 char physicalHex[13]{},legacyHex[13]{};
};
CandidateWorkspace* candidate=nullptr;
bool candidateStorage(){
 if(candidate)return true;
 void* memory=heap_caps_calloc(1,sizeof(CandidateWorkspace),MALLOC_CAP_SPIRAM|MALLOC_CAP_8BIT);
 if(!memory)return false; // Never install a hold on a readonly probe failure.
 candidate=new(memory)CandidateWorkspace{};return true;
}
QuietHooks quietHooks{};
std::atomic<bool> protection{false},allowFallback{true};
evidence::Frame prefetched{};
 bool prefetchSet=false,startupEntered=false;
struct Lease {
 bool acquired=false,held=false;
 Lease(){if(MosaicoOta::currentTaskOwnsStorageLease())held=!MosaicoOta::busy();
 else acquired=held=MosaicoOta::tryStorageLease();
 }
 ~Lease(){if(acquired)MosaicoOta::releaseStorageLease();
 }
};
struct BatteryPause {
 std::unique_lock<std::mutex>* lock;
 explicit BatteryPause(std::unique_lock<std::mutex>* p):lock(p){if(lock)lock->unlock();
 }
 ~BatteryPause(){if(lock)lock->lock();
 }
};
bool storage(){
 if(runtime)return true;
 void* memory=heap_caps_calloc(1,sizeof(Runtime),MALLOC_CAP_SPIRAM|MALLOC_CAP_8BIT);
 if(!memory){protection.store(true,std::memory_order_release);
 allowFallback.store(false,std::memory_order_release);
 return false;
 }
 runtime=new(memory)Runtime{};
 return true;
}
int64_t nowMs(){const auto now=esp_timer_get_time();
 return now<0?-1:now/1000;
 }
bool reject(const char* why){if(runtime)runtime->reason=why;
 return false;
 }
bool fail(const char* why){if(runtime){runtime->reason=why;
 runtime->blocked=true;
 runtime->session.blocked=true;
 }protection.store(true,std::memory_order_release);
 allowFallback.store(false,std::memory_order_release);
 return false;
 }
bool grant(const MosaicoNandStore::Result& r,store::Code code=store::Code::Ready){return r.error==ESP_OK&&r.cleanupError==ESP_OK&&r.stage==MosaicoNandStore::Stage::Complete&&r.coldCode==code;
 }
bool safetyWords(uint16_t& op,uint16_t& mv,uint16_t& temp,uint16_t& dc,uint16_t& fcc){
 return gauge&&gauge_word(0x3a,op)&&gauge_word(8,mv)&&gauge_word(6,temp)&&gauge_word(0x3c,dc)&&gauge_word(0x12,fcc)&&mv>=3500&&mv<=4350&&temp>=2832&&temp<=3131;
}
// Caller holds battery. No keys/DM writes. This is mandatory BEFORE every NAND
// operation, including reads (official Dhara reads may refresh physical ECC).
bool mediaGate(){uint16_t mv=0,temp=0;
 return gauge&&gauge_word(8,mv)&&gauge_word(6,temp)&&mv>=3500&&mv<=4350&&temp>=2832&&temp<=3131;
 }
bool parentSource(mosaico_gauge_contrast::Raw& destination){
 if(contrast_load_locked(true)!=contrast_journal::Status::Ready||!contrast_rt||contrast_rt->journal.loaded.selected<0)return false;
 const auto& parent=contrast_rt->journal.loaded.records[size_t(contrast_rt->journal.loaded.selected)];
 if(!contrast_journal::shape(parent)||parent.arm!=mosaico_gauge_contrast::Arm::C)return false;
 for(size_t i=0;i<131;++i)for(unsigned j=0;j<parent.snapshot.fields[i].width;++j)destination[mosaico_gauge_contrast::rawOffset(i)+j]=parent.snapshot.fields[i].original[j];
 return true;
}
bool parentSource(){return runtime&&parentSource(runtime->source);}
bool historical(){native_recovery::Evidence e{};
 const auto mac=GetHAL().getFactoryMac();
 return native_evidence_locked(e)&&native_recovery::exact(e,mac.data());
 }
bool parentBound(){
 if(!parentSource())return false;
 const auto& t=*runtime;
 const auto& b=t.authenticated.binding;
 const auto& p=contrast_rt->journal.loaded.records[size_t(contrast_rt->journal.loaded.selected)].snapshot;
 if(b.originalGctGeneration>UINT32_MAX-4||b.originalGctEpoch>UINT64_MAX-4||p.generation<b.originalGctGeneration||p.epoch<b.originalGctEpoch)return false;
 const uint32_t delta=p.generation-b.originalGctGeneration;
 if(p.epoch-b.originalGctEpoch!=delta)return false;
 const uint32_t restoreDelta=t.ownedAbort?2:3,originalDelta=t.ownedAbort?3:4;
 const bool original=p.phase==scale_recovery::Phase::Original&&(delta==0||delta==originalDelta);
 const bool pending=p.phase==scale_recovery::Phase::Pending&&delta==1;
 const bool applied=!t.ownedAbort&&p.phase==scale_recovery::Phase::Applied&&delta==2;
 const bool restoring=p.phase==scale_recovery::Phase::Restoring&&delta==restoreDelta;
 if(!original&&!pending&&!applied&&!restoring)return false;
 // Before authenticated journal decode only the normal exact chain is known.
 // Stage-specific ownership is checked immediately after loading metadata.
 if(!t.durable)return true;
 switch(t.decoded.metadata.phase){
 case trial::Phase::Armed:return applied||(pending&&t.applyInFlight&&t.operationOwner==xTaskGetCurrentTaskHandle());
 case trial::Phase::HandedOff:case trial::Phase::CaptureClaimed:case trial::Phase::Captured:case trial::Phase::CaptureFailed:return applied;
 case trial::Phase::Restoring:return applied||restoring||(original&&delta==originalDelta)||(t.ownedAbort&&pending);
 case trial::Phase::Restored:return original&&delta==originalDelta;
 case trial::Phase::BlockedUnknown:return true;
  // Never releases an IC operation.
 }
 return false;
}
trial::StartupKind startupKind(){
 switch(esp_reset_reason()){
 case ESP_RST_POWERON:return trial::StartupKind::PowerOn;
 case ESP_RST_SW:return trial::StartupKind::Software;
 case ESP_RST_BROWNOUT:return trial::StartupKind::Brownout;
 case ESP_RST_TASK_WDT:case ESP_RST_INT_WDT:case ESP_RST_WDT:return trial::StartupKind::Watchdog;
 default:return trial::StartupKind::Unknown;
 }
}
trial::Image observedImage(){
 if(!contrast_rt||!contrast_rt->observed.authenticated||contrast_rt->observed.count!=131)return trial::Image::Unknown;
 bool source=true,target=true;
 for(size_t i=0;i<131;++i){const auto& f=contrast_rt->record.snapshot.fields[i];
 bool a=true,b=true;
  for(unsigned j=0;j<f.width;++j){a&=contrast_rt->observed.raw[i][j]==f.original[j];
 b&=contrast_rt->observed.raw[i][j]==f.target[j];
 }
  if(!a&&!b)return trial::Image::Unexpected;
 source&=a;
 target&=b;
 }
 return source?trial::Image::Source131:target?trial::Image::TargetC131:trial::Image::Owned3Mixed;
}
// Caller owns battery. Never infer attempt safety from a new Session or merely
// seeing SEALED. A persisted Restoring after reboot has unresolved key history.
bool context(bool fresh131=false,bool ownedFa=false){
 auto& t=*runtime;
 auto& c=t.context;
 c={};
 c.expected=t.authenticated.binding;
 c.boot=crypto::bootIdentity();
 const auto now=nowMs();
 if(now<0)return false;
 c.nowMs=uint64_t(now);
 c.startupKind=startupKind();
 c.recordDurable=t.durable;
 c.nonceReservedFresh=t.nonceFresh;
 auto& s=c.safety;
 uint16_t op=0,dc=0,fcc=0;
 if(!safetyWords(op,s.voltageMv,s.temperatureDK,dc,fcc))return false;
 s.identityVerified=factory_identity();
 s.originalGctBindingVerified=parentBound()&&historical();
 s.securityLevel=uint8_t((op>>1)&3);
 s.securityKnown=s.securityLevel==1||s.securityLevel==3;
 s.sealed=s.securityLevel==3;
 s.cfg0=!(op&0x400);
 s.cal0=!(op&1)&&(op&0x20);
 s.durableAuthenticatedSource131=t.authenticated.sessionVerified&&s.originalGctBindingVerified;
 s.reviewedOwned3Model=s.durableAuthenticatedSource131;
 s.twoRecoverySlotsSdkValid=t.authenticated.actualImagesVerified;
 s.targetStandard65=dc==65&&fcc==65;
 const auto gct=contrast_rt&&contrast_rt->journal.loaded.selected>=0?contrast_rt->journal.loaded.records[size_t(contrast_rt->journal.loaded.selected)].snapshot.phase:scale_recovery::Phase::Original;
 const bool currentApply=t.applyInFlight&&t.operationOwner==xTaskGetCurrentTaskHandle()&&contrast_rt&&contrast_rt->operation&&contrast_rt->keysStarted&&!contrast_rt->keyFailed&&!contrast_rt->coldMutatorAckUnknown&&!contrast_rt->possibleDirty&&gct==scale_recovery::Phase::Pending;
 const bool recordedClosed=t.durable&&t.decoded.metadata.phase!=trial::Phase::Restoring&&gct==scale_recovery::Phase::Applied&&contrast_rt&&!contrast_rt->coldMutatorAckUnknown;
 const bool currentRestore=(t.restoreIssued||t.restoreIntentKnownThisBoot)&&t.operationOwner==xTaskGetCurrentTaskHandle()&&contrast_rt&&!contrast_rt->keyFailed&&!contrast_rt->coldMutatorAckUnknown;
 s.operationKnownSafeForInspection=(currentApply||recordedClosed||currentRestore)&&!t.blocked&&s.securityKnown&&s.cfg0&&s.cal0;
 s.freshAuthenticated131=fresh131;
 s.image=fresh131?observedImage():trial::Image::Unknown;
 s.calibrationUnchanged=fresh131&&contrast_rt&&!contrast_rt->unsafeCalibration&&!contrast_rt->thirdValue&&!contrast_rt->untouchedMismatch&&!contrast_rt->unexpectedCount;
 if(ownedFa){
  s.ownedGctPending=currentApply;
 s.knownFullAccess=currentApply&&s.securityLevel==1&&!s.sealed;
  s.ownedGctDeadlineBound=currentApply&&contrast_rt->appliedUs>0;
 s.ownedGctStartedMs=s.ownedGctDeadlineBound?uint64_t(contrast_rt->appliedUs/1000):0;
 }
 s.source131AfterArmedSequence=t.sourceAfterArmed;
 return true;
}
bool abortWitness(abortModel::Witness& w){
 if(!runtime||!contrast_rt||contrast_rt->journal.loaded.selected<0)return false;
 auto& t=*runtime;
 const auto& p=contrast_rt->journal.loaded.records[size_t(contrast_rt->journal.loaded.selected)].snapshot;
 w={};
 w.exactCurrentOwner=t.operationOwner==xTaskGetCurrentTaskHandle()&&MosaicoOta::currentTaskOwnsStorageLease();
 w.forwardOperation=t.applyInFlight&&contrast_rt->operation;
 w.allMutatorAcksKnown=!contrast_rt->coldMutatorAckUnknown&&!contrast_rt->keyFailed;
 w.noSubsequentWriter=t.applyInFlight&&contrast_rt->operation&&contrast_rt->record.snapshot.generation==p.generation&&contrast_rt->record.snapshot.epoch==p.epoch;
 w.parentPhase=p.phase==scale_recovery::Phase::Pending?abortModel::ParentPhase::Pending:p.phase==scale_recovery::Phase::Restoring?abortModel::ParentPhase::Restoring:abortModel::ParentPhase::Unknown;
 w.parentGeneration=p.generation;
 w.parentEpoch=p.epoch;
 if(contrast_rt->appliedUs<=0)return false;
 w.originalStartedMs=uint64_t(contrast_rt->appliedUs/1000);
 return !contrast_rt->thirdValue&&!contrast_rt->unsafeCalibration&&!contrast_rt->untouchedMismatch&&!contrast_rt->unexpectedCount&&!contrast_safety_fault;
}
bool candidateValid(void*,const Nand& r){trial::Binding b{};
 return crypto::extractBinding(r,b);
 }
bool candidateInitial(void*,const Nand& r){return r.sequence==1&&candidateValid(nullptr,r);
 }
bool candidateTransition(void*,const Nand&,const Nand&){return false;
 } // immutable proof packet
MosaicoNandStore::ColdPolicy candidatePolicy(){return {nullptr,candidateValid,candidateInitial,candidateTransition};
 }
// Only an exactly Empty pair is acceptable without an authenticated proof.
// Every nonblank/corrupt/ambiguous capsule is rejected, never called Missing.
bool rejectAnyRecord(void*,const Nand&){return false;
 }
bool rejectAnyTransition(void*,const Nand&,const Nand&){return false;
 }
MosaicoNandStore::ColdPolicy emptyOnlyPolicy(){return {nullptr,rejectAnyRecord,rejectAnyRecord,rejectAnyTransition};
 }
using EnvelopePolicy=env::Policy<crypto::Hasher>;
bool envelopeValid(void* p,const Nand& r){return static_cast<EnvelopePolicy*>(p)->valid(r);
 }
bool envelopeInitial(void* p,const Nand& r){return static_cast<EnvelopePolicy*>(p)->initial(r);
 }
bool envelopeTransition(void* p,const Nand& a,const Nand& b){return static_cast<EnvelopePolicy*>(p)->transition(a,b);
 }
MosaicoNandStore::ColdPolicy envelopePolicy(EnvelopePolicy& p){return {&p,envelopeValid,envelopeInitial,envelopeTransition};
 }
bool authenticateProof(const Nand& proof,mosaico_gauge_contrast::Raw& source,crypto::Authenticated& authenticated,bool actual,const char*& reason){
 {std::lock_guard<std::mutex> battery(battery_mutex);
 if(!parentSource(source)||!historical()){reason="cold_source_or_historical_Failed_mismatch";return false;}}
 // Physical MAC is read INSIDE crypto. The source/Failed records intentionally
 // remain authenticated in the unchanged legacy journal-MAC domain.
 if(!crypto::authenticate(proof,source,authenticated,reason))return false;
 if(!actual){authenticated.actualImagesVerified=false;return true;}
 return crypto::verifyActualImages(authenticated,reason);
}
bool authenticate(bool persistent=true,bool historicalOnly=false){
 auto& t=*runtime;const char* reason=nullptr;
 if(!authenticateProof(t.proof,t.source,t.authenticated,!historicalOnly,reason))return persistent?fail(reason?reason:"cold_auth_failed"):reject(reason?reason:"cold_auth_failed");
 if(historicalOnly)return true;
 {std::lock_guard<std::mutex> battery(battery_mutex);
 if(!parentBound())return persistent?fail("cold_original_parent_binding_changed"):reject("cold_original_parent_binding_changed");}
 return true;
}
// Both bind and verify use this same complete production path. It has no Cold
// NAND IO and cannot authorize apply or mutate the active Runtime.
bool validateFragment(const char* fragment,CandidateWorkspace& c,const char*& reason){
 c.authenticated={};size_t decoded=0;
 if(!fragment||!std::strlen(fragment)||std::strlen(fragment)>356||mbedtls_base64_decode(c.fragment,sizeof(c.fragment),&decoded,
    reinterpret_cast<const uint8_t*>(fragment),std::strlen(fragment))||!decoded){reason="cold_fragment_base64_invalid";return false;}
 if(!crypto::buildFromFragment(c.fragment,decoded,contrast_rt->decodedProof,c.proof,reason))return false;
 // Preserve authenticate's real reason; builder's success description is not
 // an authentication failure and must NEVER overwrite the more specific one.
 if(!authenticateProof(c.proof,c.source,c.authenticated,true,reason))return false;
 {std::lock_guard<std::mutex> battery(battery_mutex);
 uint16_t op=0,mv=0,temp=0,dc=0,fcc=0;
 if(!contrast_maintenance_original_cached_locked()||!safetyWords(op,mv,temp,dc,fcc)||dc!=3000||fcc!=3000||((op>>1)&3)!=3||!mosaico_gauge::configExitAccepted(op)){reason="cold_candidate_Original_not_clean";return false;}
 const auto& parent=contrast_rt->journal.loaded.records[size_t(contrast_rt->journal.loaded.selected)].snapshot;
 if(parent.generation!=c.authenticated.binding.originalGctGeneration||parent.epoch!=c.authenticated.binding.originalGctEpoch){reason="cold_candidate_exact_Original_parent_required";return false;}}
 return true;
}
bool loadJournal(){
 auto& t=*runtime;
 EnvelopePolicy typed{t.authenticated.binding,t.authenticated.bindingHash,{},&t.codec};
 auto policy=envelopePolicy(typed);
 {std::lock_guard<std::mutex> battery(battery_mutex);
 if(!mediaGate())return fail("cold_NAND_safety_read_failed");
 }
 const auto result=MosaicoNandStore::coldLoad(MosaicoNandStore::ColdArea::Journal,t.saved,policy,true,true);
 if(grant(result,store::Code::Empty)){t.loaded=true;
 t.durable=false;
 t.nonceFresh=true;
 return true;
 }
 if(!grant(result)||!crypto::sha256(t.saved.payload.data(),t.saved.size,t.bodyHash)||!env::decode(t.saved,t.authenticated.binding,t.authenticated.bindingHash,crypto::Hasher{},t.decoded,t.codec))return fail("cold_journal_unknown_or_corrupt");
 t.loaded=t.durable=true;
 t.nonceFresh=false;
 t.frames=t.decoded.frames;
 t.frameCount=t.decoded.count;
 protection.store(t.decoded.metadata.phase!=trial::Phase::Restored,std::memory_order_release);
 return true;
}
// Isolated bounded stack for metadata-only preflight; callback scratch is PSRAM.
__attribute__((noinline)) bool bindJournalEmpty(){
 auto& t=*runtime;
 EnvelopePolicy typed{t.authenticated.binding,t.authenticated.bindingHash,{},&t.codec};
 auto policy=envelopePolicy(typed);
 {std::lock_guard<std::mutex> battery(battery_mutex);if(!mediaGate())return fail("cold_bind_journal_read_safety");}
 const auto journal=MosaicoNandStore::coldLoad(MosaicoNandStore::ColdArea::Journal,t.verify,policy,true,true);
 if(grant(journal))return reject("cold_journal_already_present_no_overwrite");
 if(!grant(journal,store::Code::Empty))return fail("cold_journal_unknown_no_overwrite");
 return true;
}
bool commit(const trial::Record& next,std::unique_lock<std::mutex>* battery=nullptr){
 auto& t=*runtime;
 if(t.blocked||!MosaicoOta::currentTaskOwnsStorageLease())return fail("cold_commit_without_owned_lease_or_blocked");
 if(battery){if(!battery->owns_lock()||!mediaGate())return fail("cold_commit_safety_or_battery_lock");
 }
 else{std::lock_guard<std::mutex> guard(battery_mutex);
 if(!mediaGate())return fail("cold_commit_safety");
 }
 t.before=t.decoded.metadata;
 if(!env::encode(next,t.frames,t.frameCount,t.authenticated.binding,t.authenticated.bindingHash,crypto::Hasher{},t.next,t.codec)||!crypto::sha256(t.next.payload.data(),t.next.size,t.nextBodyHash))return fail("cold_envelope_encode_or_SHA_failed");
 EnvelopePolicy typed{t.authenticated.binding,t.authenticated.bindingHash,{},&t.codec};
 auto policy=envelopePolicy(typed);
 {BatteryPause pause(battery);
  const auto saved=MosaicoNandStore::coldSave(MosaicoNandStore::ColdArea::Journal,t.next,policy,true,true);
  if(!grant(saved))return fail("cold_commit_unknown_no_retry");
  // Explicit media reload plus actual full-body SHA, not an async log receipt.
  {std::lock_guard<std::mutex> safety(battery_mutex);
 if(!mediaGate())return fail("cold_post_save_NAND_read_safety");
 }
  const auto loaded=MosaicoNandStore::coldLoad(MosaicoNandStore::ColdArea::Journal,t.verify,policy,true,true);
  if(!grant(loaded)||t.verify.size!=t.next.size||t.verify.payload!=t.next.payload||t.verify.sequence!=next.sequence||
     !crypto::sha256(t.verify.payload.data(),t.verify.size,t.actualHash)||t.actualHash!=t.nextBodyHash||
     !env::decode(t.verify,t.authenticated.binding,t.authenticated.bindingHash,crypto::Hasher{},t.decoded,t.codec))return fail("cold_commit_fresh_body_readback_unknown");
 }
 t.saved=t.verify;
 t.bodyHash=t.nextBodyHash;
 t.durable=t.loaded=true;
 t.nonceFresh=false;
 protection.store(next.phase!=trial::Phase::Restored,std::memory_order_release);
 return true;
}
trial::Proof proof(){auto& r=runtime->decoded.metadata;
 return {trial::Commit::ExactDurable,true,true,r.sequence,r.phase,r.binding.nonce};
 }
void normalize(evidence::Frame& f){f.standard.units.factor=0;
 f.standard.units.verified=false;
 f.engine.units.factor=0;
 f.engine.units.verified=false;
 }
void readFrame(evidence::Kind kind,uint32_t error=0){
 auto& f=runtime->frame;
 f={};
 f.kind=kind;
 f.bootNonce=crypto::bootIdentity().nonce;
 f.resetReason=uint32_t(esp_reset_reason());
 f.error=error;
 gauge_diagnostic_locked(f.standard,mosaico_gauge_status::Explicit,0);
 gauge_engine_locked(f.engine,mosaico_gauge_status::Explicit,0);
 normalize(f);
}
bool appendFailure(trial::CaptureResult result,bool interrupted=false,bool retainCurrentFrame=false){
 auto& t=*runtime;
 // No extra IC reads after expiry/interruption: preserve the real passive RAM
 // sample and its original timestamps/valid masks, including incomplete data.
 if(!retainCurrentFrame){if(!prefetchSet)return fail("cold_failure_current_boot_prefetch_missing");
 t.frame=prefetched;
 }
 const auto boot=crypto::bootIdentity();
 if(!trial::validBoot(boot)||t.frame.bootNonce!=boot.nonce)return fail("cold_failure_sample_boot_mismatch_no_relabel");
 // Preserve opaque transport error, reset reason and BOTH original sample
 // timestamps. CaptureResult is metadata, never a substitute raw error code.
 t.frame.kind=evidence::Kind::Failure;
 normalize(t.frame);
 const bool unclaimed=trial::unclaimedFailure(result);
 if(unclaimed)t.frameCount=0;
 if(t.frameCount>=env::MaxFrames||!evidence::encode(t.frame,t.frames[t.frameCount]))return fail("cold_failure_raw_shape");
 ++t.frameCount;
 for(size_t i=0;i<t.frameCount;++i)for(size_t j=0;j<evidence::WireSize;++j)t.next.payload[env::RawOffset+i*evidence::WireSize+j]=t.frames[i][j];
 if(!crypto::sha256(t.next.payload.data()+env::RawOffset,size_t(t.frameCount)*evidence::WireSize,t.actualHash))return fail("cold_failure_raw_SHA");
 {std::lock_guard<std::mutex> battery(battery_mutex);
 if(!context(false)){
   // Failure metadata never grants IC access. Keep safety UNKNOWN while
   // preserving the known read/interruption outcome and actual raw bytes.
   const auto now=nowMs();
 if(now<0)return fail("cold_failure_clock_unknown");
   t.context={};
 t.context.expected=t.authenticated.binding;
 t.context.boot=crypto::bootIdentity();
 t.context.nowMs=uint64_t(now);
 t.context.startupKind=startupKind();
 t.context.recordDurable=t.durable;
  }}
 t.decision=unclaimed?trial::rejectedStartupEvidence(t.decoded.metadata,t.context,t.actualHash):interrupted?trial::interruptedEvidence(t.decoded.metadata,t.context,t.actualHash):trial::captureEvidence(t.decoded.metadata,t.context,t.session,result,t.actualHash);
 if(!t.decision.hasNext)return fail("cold_failure_metadata_rejected");
 return commit(t.decision.next);
}
bool readyQuiet(){
 auto& t=*runtime;
 if(t.quietFailed)return false;
 if(!t.quietRequested){t.quietRequested=true;
 t.quietOwned=quietHooks.enter&&quietHooks.enter();
 if(!t.quietOwned){t.quietFailed=true;
 t.reason="cold_quiet_public_owner_hook_missing_or_failed";
 return false;
 }}
 return t.quietOwned;
}
bool requestRestore(RestoreRequest* out){
 auto& t=*runtime;
 if(t.blocked||t.restoreIssued||!readyQuiet()||!contrast_recovery_runtime())return false;
 if(!out){t.reason="cold_restore_dispatch_sink_required_no_release";
 return false;
 }
 {std::lock_guard<std::mutex> battery(battery_mutex);
 if(!context(false))return fail("cold_restore_fresh_context_failed");
 }
 t.restoreIntentKnownThisBoot=trial::safe(t.context,false);
 t.operationOwner=xTaskGetCurrentTaskHandle();
 t.decision=trial::restoreIntent(t.decoded.metadata,t.context);
 if(t.decision.hasNext){if(!commit(t.decision.next))return false;
 }
 else if(t.decision.action!=trial::Action::RestoreRequired)return fail("cold_restore_intent_not_authorized");
 {std::lock_guard<std::mutex> battery(battery_mutex);
 if(!context(false))return fail("cold_inspection_context");
 }
 t.decision=trial::authorizeRestoreInspection(t.decoded.metadata,t.context,t.session);
 if(t.decision.action!=trial::Action::InspectForRestore)return fail("cold_inspection_not_authorized");
 t.restoreIssued=true;
 t.operationOwner=xTaskGetCurrentTaskHandle();
 t.reason="cold_restore_request_requires_existing_backend_fresh131_seam";
 if(out)*out={true,t.decoded.metadata.sequence,crypto::bootIdentity().nonce};
 return true;
}
bool capacity(char* out,size_t n){if(out&&n>=1024)return true;
 if(out&&n)std::snprintf(out,n,"reason=cold_output_capacity_require1024 success=0");
 return false;
 }
bool report(char* out,size_t n,bool ok){
 if(!out||n<768)return false;
 const auto* t=runtime;
 const int count=std::snprintf(out,n,"success=%d reason=%s experimental_cold_protocol=1 loaded=%d durable=%d phase=%u sequence=%lu bound=%d blocked=%d cold_hold=%d fallback_allowed=%d restore_requested=%d raw_frames=%u raw_body_durable=%d quiet_hook=%d quiet_owned=%d original_closure_verified=%d metadata_closure_unknown=%d historical_only=%d actual_images_verified=%d normalboot_radiohold_confound=1 physical_units=UNKNOWN off_wall_clock_guaranteed=0",
 ok,t?t->reason:"cold_not_allocated",t&&t->loaded,t&&t->durable,unsigned(t&&t->durable?uint8_t(t->decoded.metadata.phase):0),static_cast<unsigned long>(t&&t->durable?t->decoded.metadata.sequence:0),t&&t->bound,t&&t->blocked,protection.load(),allowFallback.load(),t&&t->restoreIssued,unsigned(t?t->frameCount:0),t&&t->durable&&t->decoded.metadata.capture!=trial::CaptureResult::None&&t->decoded.count>0,quietHooks.enter!=nullptr,t&&t->quietOwned,t&&t->verifiedOriginalClosure,t&&t->metadataClosureFailed,t&&t->historicalOnly,t&&t->authenticated.actualImagesVerified);
 return count>=0&&size_t(count)<n&&ok;
}
} // anonymous

void setQuietHooks(QuietHooks hooks){std::lock_guard<std::recursive_mutex> txn(gauge_transaction_mutex);
 if(!runtime||(!runtime->quietRequested&&!runtime->applyInFlight))quietHooks=hooks;
 }
void earlyPrefetch(const mosaico_gauge_status::Snapshot& standard,const mosaico_gauge_engine::Snapshot& engine,uint32_t opaqueError){
 if(prefetchSet)return;
 prefetched={};
 prefetched.kind=evidence::Kind::Initial;
 prefetched.standard=standard;
 prefetched.engine=engine;
 prefetched.bootNonce=crypto::bootIdentity().nonce;
 prefetched.resetReason=uint32_t(esp_reset_reason());
 prefetched.error=opaqueError;
 normalize(prefetched);
 prefetchSet=true;
}
bool fallbackAllowed(){return allowFallback.load(std::memory_order_acquire);
 }
bool keyWindowAllowed(){
 if(!protectedState())return true;
  // Completely unchanged old short trials.
 if(!runtime||runtime->blocked||!ownsOperation()||!MosaicoOta::currentTaskOwnsStorageLease())return false;
 auto& t=*runtime;
 const auto now=esp_timer_get_time();
 const auto boot=crypto::bootIdentity();
 if(now<0||!trial::validBoot(boot))return false;
 if(t.durable){
  const auto& r=t.decoded.metadata;
  if(trial::sameBoot(r.armedBoot,boot)){const auto limit=t.restoreIssued?trial::armedLimitMs:trial::armedRestoreTriggerMs;
 return uint64_t(now/1000)>=r.armedMs&&uint64_t(now/1000)-r.armedMs<limit;
 }
  return now<120000000;
  // Never restart the120s window at claim/restore.
 }
 if(!t.applyInFlight||!t.bound||!t.authenticated.sessionVerified||!t.authenticated.actualImagesVerified||!contrast_rt||!contrast_rt->operation||contrast_rt->keyFailed)return false;
 const auto& old=*contrast_rt;
 if(old.appliedUs<=0){
  // Metadata-only forward admission precedes the old timer. No key path can
  // pass old open() until its Pending intent and real start have been set.
  return !old.keysStarted&&!old.possibleDirty&&old.record.snapshot.phase==scale_recovery::Phase::Original;
 }
 if(!t.forwardStartUs)t.forwardStartUs=old.appliedUs;
 return old.appliedUs==t.forwardStartUs&&now>=t.forwardStartUs&&now-t.forwardStartUs<600000000;
  // Forward never spends the120s restore reserve.
}
bool blocksAutomaticBankPulse(){return protectedState();
 }
void onRestoreFailed(){if(runtime&&runtime->restoreIssued)(void)fail("cold_dispatched_restore_failed_unknown_no_retry");
 }
bool protectedState(){return protection.load(std::memory_order_acquire);
 }
bool ownsOperation(){return runtime&&!runtime->historicalOnly&&runtime->operationOwner==xTaskGetCurrentTaskHandle()&&(runtime->applyInFlight||runtime->restoreIssued);
 }
bool startup(){
 Lease lease;
 std::lock_guard<std::recursive_mutex> txn(gauge_transaction_mutex);
 if(startupEntered)return !runtime||!runtime->blocked;
 startupEntered=true;
 const auto gct=contrast_load_locked(true);
 if(gct==contrast_journal::Status::Ready&&contrast_rt->journal.loaded.records[size_t(contrast_rt->journal.loaded.selected)].snapshot.phase==scale_recovery::Phase::Original)return true;
  // NO NAND; do not claim dirty metadata Complete.
 if(!storage())return false;
 if(!lease.held)return fail("cold_startup_lease");
 if(gct!=contrast_journal::Status::Ready&&gct!=contrast_journal::Status::Missing)return fail("cold_startup_GCT_unknown");
 auto& t=*runtime;
 protection.store(true,std::memory_order_release);
 allowFallback.store(false,std::memory_order_release);
 {std::lock_guard<std::mutex> battery(battery_mutex);
 if(!mediaGate())return fail("cold_startup_NAND_safety");
 }
 const auto loaded=MosaicoNandStore::coldLoad(MosaicoNandStore::ColdArea::Proof,t.proof,candidatePolicy(),true,true);
 if(grant(loaded,store::Code::Empty)){
  {std::lock_guard<std::mutex> battery(battery_mutex);
 if(!mediaGate())return fail("cold_missing_proof_journal_probe_safety");
 }
  const auto journal=MosaicoNandStore::coldLoad(MosaicoNandStore::ColdArea::Journal,t.verify,emptyOnlyPolicy(),true,true);
  if(!grant(journal,store::Code::Empty))return fail("cold_journal_present_or_unknown_without_authenticated_proof");
  protection.store(false,std::memory_order_release);
 allowFallback.store(true,std::memory_order_release);
 t.reason="cold_proof_and_journal_missing_legacy_recovery_only";
 return true;
 }
 if(gct!=contrast_journal::Status::Ready)return fail("cold_proof_present_without_source_GCT");
 if(!grant(loaded))return fail("cold_startup_proof_unknown");
 if(!authenticate()||!loadJournal())return false;
 if(!t.durable)return fail("cold_reserved_proof_without_Armed_unknown_prekey_attempt_no_legacy_fallback");
 t.bound=true;
 {std::lock_guard<std::mutex> battery(battery_mutex);
 if(!parentBound())return fail("cold_stage_parent_generation_or_epoch_mismatch");
 }
 if(t.decoded.metadata.phase==trial::Phase::Restoring)return fail("cold_reboot_Restoring_unresolved_keys_no_Session_waiver");
 {std::lock_guard<std::mutex> battery(battery_mutex);
 if(!context(false))return fail("cold_startup_context");
 }
 t.decision=trial::startup(trial::Load::Present,t.decoded.metadata,t.context);
 if(t.decision.hasNext){
  if(!prefetchSet||!evidence::encode(prefetched,t.frames[0]))return fail("cold_initial_prefetch_missing_or_unknown");
 t.frameCount=1;
  if(!commit(t.decision.next))return false;
  {std::lock_guard<std::mutex> battery(battery_mutex);
 if(!context(false))return fail("cold_claim_context");
 }
  t.decision=trial::afterPersist(t.before,t.decoded.metadata,proof(),t.context,t.session);
  if(t.decision.action!=trial::Action::ReadOnlyCapture)return fail("cold_claim_not_released");
 t.reason="cold_claim_durable_initial_raw_only";
 return true;
 }
 if(t.decision.action==trial::Action::FailureEvidenceRequired){
  const auto failure=t.decoded.metadata.phase==trial::Phase::CaptureClaimed?trial::CaptureResult::Interrupted:t.context.startupKind==trial::StartupKind::PowerOn?trial::CaptureResult::StartupWindowExpired:trial::CaptureResult::RejectedStartup;
  if(!appendFailure(failure,failure==trial::CaptureResult::Interrupted))return false;
 t.restoreWanted=true;
 return true;
 }
 if(t.decision.action==trial::Action::RestoreRequired){t.restoreWanted=true;
 return true;
 }
 if(t.decision.action==trial::Action::Complete)return fail("cold_terminal_Restored_conflicts_nonOriginal_GCT_no_Complete_waiver");
 return fail("cold_startup_model_rejected");
}
bool verifyFragment(const char* fragment,char* out,size_t n){
 if(!capacity(out,n))return false;
 Lease lease;std::lock_guard<std::recursive_mutex> txn(gauge_transaction_mutex);
 const char* reason="cold_verify_unverified";bool ok=false;
 if(candidate){candidate->physical={};candidate->legacy={};candidate->authenticated={};}
 if(!lease.held)reason="cold_verify_OTA_lease_required";
 else if(runtime&&(runtime->bound||runtime->durable||runtime->blocked||runtime->applyInFlight||runtime->restoreIssued))reason="cold_verify_active_or_blocked_rejected";
 else if(contrast_load_locked(true)!=contrast_journal::Status::Ready||!contrast_maintenance_original_cached_locked()||!contrast_rt->imagesSet)reason="cold_verify_clean_Original_images_required";
 else if(!candidateStorage())reason="cold_verify_PSRAM_unavailable";
 else{
  candidate->physical={};candidate->legacy=GetHAL().getFactoryMac();
  if(!crypto::physicalMac(candidate->physical))reason="cold_verify_physical_mac_unavailable";
  else if(validateFragment(fragment,*candidate,reason)){ok=true;reason="cold_verify_authenticated_candidate_only_not_bound";}
 }
 if(candidate){
  if(!ok)candidate->authenticated={};
  for(unsigned i=0;i<6;++i){std::snprintf(candidate->physicalHex+2*i,3,"%02x",candidate->physical[i]);std::snprintf(candidate->legacyHex+2*i,3,"%02x",candidate->legacy[i]);}
 }
 const int used=std::snprintf(out,n,"success=%d reason=%s candidate_only=1 operation_local=1 physical_mac=%s legacy_journal_mac=%s session_verified=%d actual_images_verified=%d active_session_installed=0 nonce_reserved=0 keys=0 parameter_writes=0 nand_proof_writes=0 nand_journal_writes=0 live_dm131_verified=0 physical_units=UNKNOWN",
 ok,reason,candidate?candidate->physicalHex:"unknown",candidate?candidate->legacyHex:"unknown",ok&&candidate->authenticated.sessionVerified,ok&&candidate->authenticated.actualImagesVerified);
 return used>=0&&size_t(used)<n&&ok;
}
bool bindFragment(const char* fragment,char* out,size_t n){
 if(!capacity(out,n))return false;
 Lease lease;
 std::lock_guard<std::recursive_mutex> txn(gauge_transaction_mutex);
 if(!lease.held||contrast_load_locked(true)!=contrast_journal::Status::Ready||!contrast_maintenance_original_cached_locked())return report(out,n,false);
 // This entire operation is metadata-only under a locked clean Original.
 // Retain unknown/blocked evidence, but never add a radio/OTA hold for it.
 struct HoldRestore {
  bool held=protection.load(),fallback=allowFallback.load();
  ~HoldRestore(){protection.store(held,std::memory_order_release);allowFallback.store(fallback,std::memory_order_release);}
 } holdRestore;
 if(!storage())return report(out,n,false);
 auto& t=*runtime;
 if(t.blocked||t.durable||(t.bound&&!t.historicalOnly)||!fragment||std::strlen(fragment)>512||!contrast_rt->imagesSet)return report(out,n,false);
 const char* reason=nullptr;
 if(!candidateStorage())return report(out,n,reject("cold_bind_PSRAM_unavailable"));
 if(!validateFragment(fragment,*candidate,reason))return report(out,n,reject(reason?reason:"cold_fragment_auth_failed"));
 t.proof=candidate->proof;t.source=candidate->source;t.authenticated=candidate->authenticated;
 {std::lock_guard<std::mutex> battery(battery_mutex);if(!mediaGate())return fail("cold_bind_NAND_safety");}
 const auto old=MosaicoNandStore::coldLoad(MosaicoNandStore::ColdArea::Proof,t.verify,candidatePolicy(),true,true);
 if(grant(old))return report(out,n,reject("cold_proof_nonce_already_reserved_no_overwrite"));
 if(!grant(old,store::Code::Empty))return fail("cold_proof_unknown_no_overwrite");
 // Prove BOTH areas empty before writing either. An orphan journal is never
 // overwritten, and no proof reservation is written ahead of this check.
 if(!bindJournalEmpty())return report(out,n,false);
 {std::lock_guard<std::mutex> battery(battery_mutex);if(!mediaGate())return fail("cold_bind_proof_write_safety");}
 const auto saved=MosaicoNandStore::coldSave(MosaicoNandStore::ColdArea::Proof,t.proof,candidatePolicy(),true,true);
 if(!grant(saved))return fail("cold_proof_save_unknown_no_retry");
 {std::lock_guard<std::mutex> battery(battery_mutex);if(!mediaGate())return fail("cold_bind_proof_readback_safety");}
 const auto reloaded=MosaicoNandStore::coldLoad(MosaicoNandStore::ColdArea::Proof,t.verify,candidatePolicy(),true,true);
 if(!grant(reloaded)||t.verify.payload!=t.proof.payload||t.verify.binding!=t.proof.binding)return fail("cold_proof_commit_unknown_no_retry");
 if(!loadJournal()||t.durable)return fail("cold_nonce_journal_not_missing");
 t.historicalOnly=false;t.bound=true;
 t.reason="cold_bound_metadata_only_no_keys_no_fresh_DM_claim";
 return report(out,n,true);
}
bool armSeam(std::unique_lock<std::mutex>& battery){
 if(!runtime||!runtime->applyInFlight)return true;
 auto& t=*runtime;
 if(!battery.owns_lock()||t.blocked||!t.bound||!MosaicoOta::currentTaskOwnsStorageLease()||t.operationOwner!=xTaskGetCurrentTaskHandle())return fail("cold_arm_seam_ownership");
 if(!context(true,true))return fail("cold_arm_fresh_source_context");
 t.decision=trial::arm(trial::Load::Missing,t.authenticated.binding,t.context);
 if(!t.decision.hasNext||!commit(t.decision.next,&battery))return fail("cold_arm_commit_unknown_no_automatic_rollback_keys");
 if(!context(true,true))return fail("cold_arm_readback_context");
 t.decision=trial::afterPersist(t.before,t.decoded.metadata,proof(),t.context,t.session,true);
 if(t.decision.action!=trial::Action::KeepArmed720)return fail("cold_arm_durable_metadata_not_released");
 contrast_rt->nextReadStage="cold_after_Armed_commit_source131";
 if(!contrast_rt_read_all()||!contrast_rt_inspect(false,false)||observedImage()!=trial::Image::Source131)return fail("cold_post_Armed_live_source_changed_no_CFG");
 t.sourceAfterArmed=t.decoded.metadata.sequence;
 if(!context(true,true))return fail("cold_post_Armed_context");
 t.decision=trial::authorizeApply(t.decoded.metadata,t.context,t.session);
 return t.decision.action==trial::Action::ApplyC65Owned3||fail("cold_CFG_not_authorized");
}
bool isOwnedAbort(){return runtime&&runtime->ownedAbort&&runtime->restoreIssued&&!runtime->blocked&&runtime->operationOwner==xTaskGetCurrentTaskHandle();
 }
bool beginRestore(std::unique_lock<std::mutex>& battery){
 if(!protectedState())return true;
 if(!runtime||!battery.owns_lock()||!MosaicoOta::currentTaskOwnsStorageLease()||!ownsOperation())return fail("cold_begin_restore_owner_required");
 auto& t=*runtime;
 if(t.blocked||!contrast_rt||contrast_rt->coldMutatorAckUnknown||contrast_rt->keyFailed||!contrast_recovery_runtime())return fail("cold_begin_restore_unknown_ACK_or_runtime");
 if(t.restoreIssued){
  if(!context(false)||!keyWindowAllowed())return fail("cold_existing_restore_window_or_binding");
  if(t.ownedAbort)return t.context.safety.securityLevel==1&&t.context.safety.cal0&&parentBound();
  return trial::safe(t.context,false)||fail("cold_existing_restore_closed_safety");
 }
 if(!t.applyInFlight||!t.durable||t.decoded.metadata.phase!=trial::Phase::Armed||t.decoded.metadata.sequence!=1||t.frameCount)return fail("cold_forward_abort_not_Armed_current_operation");
 if(!context(false))return fail("cold_forward_abort_fresh_context");
 abortModel::Witness witness{};
 if(!abortWitness(witness))return fail("cold_forward_abort_bad_witness");
 t.decision=abortModel::intent(t.decoded.metadata,t.context,t.session,witness);
 if(!t.decision.hasNext)return fail("cold_forward_abort_intent_not_authorized");
 t.ownedAbort=true;
 t.restoreIntentKnownThisBoot=true;
 if(!commit(t.decision.next,&battery))return false;
 t.restoreIssued=true;
 t.restoreWanted=true;
 t.reason="cold_owned_abort_durable_intent_retained_FA_only";
 return true;
}
bool beforeRestoreKeys(std::unique_lock<std::mutex>& battery){
 if(!protectedState())return true;
 if(!runtime||!battery.owns_lock()||!ownsOperation()||!MosaicoOta::currentTaskOwnsStorageLease())return fail("cold_before_restore_keys_owner");
 auto& t=*runtime;
 if(t.blocked||!t.restoreIssued||!contrast_rt||contrast_rt->coldMutatorAckUnknown||contrast_rt->keyFailed||!context(false)||!keyWindowAllowed())return fail("cold_before_restore_keys_unknown_or_expired");
 if(!t.ownedAbort)return trial::safe(t.context,false)||fail("cold_normal_restore_requires_closed_known_state");
 abortModel::Witness witness{};
 if(!abortWitness(witness))return fail("cold_abort_inspection_bad_witness");
 t.decision=abortModel::inspect(t.decoded.metadata,t.context,t.session,witness);
 return t.decision.action==trial::Action::InspectForRestore||fail("cold_abort_inspection_not_authorized");
}
bool restoreSeam(std::unique_lock<std::mutex>& battery){
 if(!runtime||!runtime->restoreIssued)return true;
 auto& t=*runtime;
 if(!battery.owns_lock()||t.operationOwner!=xTaskGetCurrentTaskHandle()||t.blocked||!context(true))return fail("cold_restore_seam_context");
 if(contrast_rt->coldMutatorAckUnknown)return fail("cold_restore_mutator_ACK_unknown");
 if(t.ownedAbort){abortModel::Witness witness{};
 if(!abortWitness(witness))return fail("cold_abort_restore_bad_witness");
 t.decision=abortModel::restore(t.decoded.metadata,t.context,t.session,witness);
 }
 else t.decision=trial::authorizeRestore(t.decoded.metadata,t.context,t.session);
 return t.decision.action==trial::Action::RestoreOriginalOwned3||fail("cold_restore_owned3_not_authorized");
}
void onApplied(bool success,bool knownNoDirty){
 if(!runtime)return;
 auto& t=*runtime;
 const bool owned=t.applyInFlight&&t.operationOwner==xTaskGetCurrentTaskHandle();
 t.applyInFlight=false;
 if(!success&&t.verifiedOriginalClosure){t.operationOwner=nullptr;
 return;
 } // Failed apply remains false; never relock a verified rollback.
 if(success&&t.durable&&owned&&t.decoded.metadata.phase==trial::Phase::Armed&&t.session.applyReleased){
  const auto& b=t.authenticated.binding;
 const auto& parent=contrast_rt->record.snapshot;
  if(parent.phase!=scale_recovery::Phase::Applied||parent.generation!=uint64_t(b.originalGctGeneration)+2||parent.epoch!=b.originalGctEpoch+2||
     contrast_rt->appliedUs<=0||uint64_t(contrast_rt->appliedUs/1000)!=t.decoded.metadata.armedMs||
     !contrast_rt->observed.authenticated||contrast_rt->observed.count!=131||contrast_rt->observedPhase!=scale_recovery::Phase::Pending||
     contrast_rt->observedGeneration!=uint64_t(b.originalGctGeneration)+1||contrast_rt->observedEpoch!=b.originalGctEpoch+1||
     contrast_rt->observedReadMs<0||contrast_rt->unsafeCalibration||contrast_rt->thirdValue||contrast_rt->untouchedMismatch||contrast_rt->unexpectedCount){fail("cold_completed_GCT_target_receipt_mismatch");
 return;
 }
  for(size_t i=0;i<131;++i)for(unsigned j=0;j<parent.fields[i].width;++j)t.targetCache[mosaico_gauge_contrast::rawOffset(i)+j]=contrast_rt->observed.raw[i][j];
  if(observedImage()!=trial::Image::TargetC131||mosaico_gauge_contrast::rawDigest(t.targetCache)!=trial::targetCrc){fail("cold_completed_full_target131_digest");
 return;
 }
  t.targetReceipt=true;
 t.targetGeneration=parent.generation;
 t.targetEpoch=parent.epoch;
 t.targetStartedMs=t.decoded.metadata.armedMs;
 t.targetObservedMs=uint64_t(contrast_rt->observedReadMs);
  t.applied=true;
 t.reason="cold_C65_applied_Armed_original720_anchor_preserved";
 protection.store(true,std::memory_order_release);
 return;
 }
 if(knownNoDirty&&!t.durable){t.reason="cold_known_prekey_no_dirty_no_automatic_keys";
 t.operationOwner=nullptr;
 allowFallback.store(true,std::memory_order_release);
 protection.store(false,std::memory_order_release);
 return;
 }
 (void)fail("cold_apply_unknown_attempt_or_commit_no_retry");
}
bool apply(char* out,size_t n){
 if(!capacity(out,n))return false;
 Lease lease;
 std::lock_guard<std::recursive_mutex> txn(gauge_transaction_mutex);
 if(!storage())return report(out,n,false);
 auto& t=*runtime;
 if(!lease.held||t.historicalOnly||t.blocked||!t.bound||t.durable||t.applyInFlight||t.restoreIssued)return report(out,n,false);
 if(contrast_load_locked(true)!=contrast_journal::Status::Ready||!contrast_maintenance_original_cached_locked())return report(out,n,reject("cold_apply_requires_clean_Original"));
 const auto& parent=contrast_rt->journal.loaded.records[size_t(contrast_rt->journal.loaded.selected)];
 if(parent.arm!=mosaico_gauge_contrast::Arm::C||parent.snapshot.generation!=t.authenticated.binding.originalGctGeneration||parent.snapshot.epoch!=t.authenticated.binding.originalGctEpoch)return report(out,n,reject("cold_apply_bound_Original_changed_no_rebase_no_keys"));
 const char* imageReason=nullptr;
 if(!crypto::verifyActualImages(t.authenticated,imageReason))return report(out,n,reject(imageReason?imageReason:"cold_apply_actual_images_changed"));
 t.applyInFlight=true;
 t.operationOwner=xTaskGetCurrentTaskHandle();
 allowFallback.store(false,std::memory_order_release);
 protection.store(true,std::memory_order_release);
 const bool result=GetHAL().gaugeContrastApply(out,n);
 const bool knownNoDirty=!result&&!t.durable&&contrast_rt&&contrast_status==contrast_journal::Status::Ready&&contrast_rt->journal.loaded.selected>=0&&contrast_rt->journal.loaded.records[size_t(contrast_rt->journal.loaded.selected)].snapshot.phase==scale_recovery::Phase::Original&&!contrast_rt->keysStarted&&!contrast_rt->keyFailed&&!contrast_rt->accessTouched&&!contrast_rt->possibleDirty;
 onApplied(result,knownNoDirty);
 return report(out,n,result);
}
bool handoff(char* out,size_t n){
 if(!capacity(out,n))return false;
 Lease lease;
 std::lock_guard<std::recursive_mutex> txn(gauge_transaction_mutex);
 if(!runtime||!lease.held)return report(out,n,false);
 auto& t=*runtime;
 if(t.historicalOnly||!t.durable||!t.applied||t.blocked)return report(out,n,false);
 {std::lock_guard<std::mutex> battery(battery_mutex);
 if(!context(false))return report(out,n,false);
  auto& safety=t.context.safety;
 const auto& parent=contrast_rt->journal.loaded.records[size_t(contrast_rt->journal.loaded.selected)].snapshot;
  safety.ownedCompletedGctTargetReceipt=t.targetReceipt;
  safety.targetReceiptNoSubsequentWriter=t.targetReceipt&&parent.phase==scale_recovery::Phase::Applied&&parent.generation==t.targetGeneration&&parent.epoch==t.targetEpoch&&
    !contrast_rt->keyFailed&&!contrast_rt->coldMutatorAckUnknown&&contrast_rt->appliedUs>0&&uint64_t(contrast_rt->appliedUs/1000)==t.targetStartedMs&&
    contrast_rt->observedReadMs>=0&&uint64_t(contrast_rt->observedReadMs)==t.targetObservedMs&&observedImage()==trial::Image::TargetC131;
  safety.targetReceiptGeneration=t.targetGeneration;
 safety.targetReceiptEpoch=t.targetEpoch;
  safety.targetReceiptStartedMs=t.targetStartedMs;
 safety.targetReceiptObservedMs=t.targetObservedMs;
  safety.calibrationUnchanged=t.targetReceipt&&!contrast_rt->unsafeCalibration&&!contrast_rt->thirdValue&&!contrast_rt->untouchedMismatch&&!contrast_rt->unexpectedCount;
  // It is an owned prior-transaction receipt, NEVER a fresh sealed-DM read.
 }
 t.decision=trial::handoff(t.decoded.metadata,t.context);
 if(!t.decision.hasNext)return report(out,n,false);
 const bool ok=commit(t.decision.next);
 if(ok)t.reason="cold_HandedOff_sameboot_shutdown_grace30_not_V1_off_proof";
 return report(out,n,ok);
}
bool poll(RestoreRequest* out){
 if(out)*out={};
 if(!protectedState())return false;
 Lease lease;
 std::lock_guard<std::recursive_mutex> txn(gauge_transaction_mutex);
 if(!runtime||!lease.held)return true;
 auto& t=*runtime;
 if(t.blocked||!t.durable)return true;
 const auto now=nowMs();
 if(now<0){fail("cold_monotonic_clock_invalid");
 return true;
 }
 if(t.decoded.metadata.phase==trial::Phase::CaptureClaimed&&!t.session.captureFinished){
  const auto cutoff=std::min<uint64_t>(t.decoded.metadata.claimMs+t.authenticated.binding.captureBudgetMs,trial::resumedCaptureLimitMs);
  if(uint64_t(now)>=cutoff){appendFailure(trial::CaptureResult::KnownTimeout);
 t.restoreWanted=true;
 }
  else if(!t.finalAttempted&&uint64_t(now)+5000>=cutoff){
   t.finalAttempted=true;
   {std::lock_guard<std::mutex> battery(battery_mutex);
 readFrame(evidence::Kind::Final);
 }
   if(t.frameCount!=1||!evidence::encode(t.frame,t.frames[1])){fail("cold_final_raw_shape");
 return true;
 }t.frameCount=2;
   for(size_t i=0;i<2;++i)for(size_t j=0;j<evidence::WireSize;++j)t.next.payload[env::RawOffset+i*evidence::WireSize+j]=t.frames[i][j];
   if(!crypto::sha256(t.next.payload.data()+env::RawOffset,2*evidence::WireSize,t.actualHash)){fail("cold_final_body_SHA");
 return true;
 }
   const bool complete=evidence::decode(t.frames[0],t.initialFrame)&&env::completeRead(t.initialFrame)&&env::completeRead(t.frame);
   if(!complete){t.frameCount=1;
 appendFailure(trial::CaptureResult::KnownReadFailure,false,true);
 t.restoreWanted=true;
 }
   else{
    {std::lock_guard<std::mutex> battery(battery_mutex);
 if(!context(false)){fail("cold_capture_context");
 return true;
 }}
    if(!trial::safe(t.context,false)||!t.context.safety.targetStandard65){
     t.frameCount=1;
 if(!appendFailure(trial::CaptureResult::KnownReadFailure,false,true))return true;
    }else{
     t.decision=trial::captureEvidence(t.decoded.metadata,t.context,t.session,trial::CaptureResult::Success,t.actualHash);
     if(!t.decision.hasNext){t.frameCount=1;
 appendFailure(trial::CaptureResult::KnownTimeout,false,true);
 }
     else if(!commit(t.decision.next))return true;
    }
    t.restoreWanted=true;
   }
  }
 }else if(t.decoded.metadata.phase==trial::Phase::Armed||t.decoded.metadata.phase==trial::Phase::HandedOff){
  const bool same=trial::sameBoot(t.decoded.metadata.armedBoot,crypto::bootIdentity());
  const bool due=!same||uint64_t(now)<t.decoded.metadata.armedMs||uint64_t(now)-t.decoded.metadata.armedMs>=trial::armedRestoreTriggerMs||
   (t.decoded.metadata.phase==trial::Phase::HandedOff&&(uint64_t(now)<t.decoded.metadata.handoffMs||uint64_t(now)-t.decoded.metadata.handoffMs>=trial::handoffGraceMs));
  t.restoreWanted|=due;
  if(now>=t.nextSafetyMs){t.nextSafetyMs=now+1000;
 std::lock_guard<std::mutex> battery(battery_mutex);
 uint16_t op=0,mv=0,temp=0,dc=0,fcc=0;
   if(!safetyWords(op,mv,temp,dc,fcc)||mv<=3900||dc!=65||fcc!=65||!mosaico_gauge::configExitAccepted(op)||((op>>1)&3)!=3)t.restoreWanted=true;
  }
 }
 if(t.restoreWanted&&!t.blocked)(void)requestRestore(out);
 return true;
}
bool recover(char* out,size_t n){if(!capacity(out,n))return false;
 Lease lease;
 std::lock_guard<std::recursive_mutex> txn(gauge_transaction_mutex);
 if(!runtime||!lease.held||runtime->blocked||runtime->historicalOnly)return report(out,n,false);
 if(!runtime->durable){runtime->reason="cold_no_active_record_proof_nonce_retained_no_keys";
 return report(out,n,true);
 }if(trial::terminal(runtime->decoded.metadata.phase))return report(out,n,reject("cold_terminal_metadata_no_new_recovery_authority"));
 runtime->restoreWanted=true;
 runtime->reason="cold_known_recover_requested_no_immediate_keys";
 return report(out,n,true);
 }
bool cancel(char* out,size_t n){return recover(out,n);
 } // never erase/force unknown state
bool onRestored(const mosaico_gauge_recovery::Snapshot& snapshot,const mosaico_gauge_recovery::Observation& observed,bool freshSealedStandard){
 Lease lease;
 std::lock_guard<std::recursive_mutex> txn(gauge_transaction_mutex);
 if(!runtime||!lease.held)return false;
 auto& t=*runtime;
 if(t.verifiedOriginalClosure)return t.durable&&t.decoded.metadata.phase==trial::Phase::Restored&&!t.metadataClosureFailed;
 if(t.blocked||!t.restoreIssued||t.operationOwner!=xTaskGetCurrentTaskHandle()||!t.session.restoreReleased||!contrast_rt||contrast_rt->coldMutatorAckUnknown||!freshSealedStandard||!observed.authenticated||observed.count!=131||snapshot.count!=131||!mosaico_gauge_recovery::knownIdentity(snapshot.identity))return fail("cold_restore_completion_missing_owned_proof");
 for(size_t i=0;i<131;++i){if(snapshot.fields[i].address!=mosaico_gauge_recovery::layout[i].address||snapshot.fields[i].width!=mosaico_gauge_recovery::layout[i].width)return fail("cold_restore_source_layout");
 for(unsigned j=0;j<snapshot.fields[i].width;++j)if(observed.raw[i][j]!=t.source[mosaico_gauge_contrast::rawOffset(i)+j]||snapshot.fields[i].original[j]!=t.source[mosaico_gauge_contrast::rawOffset(i)+j])return fail("cold_restore_source131_mismatch");
 }
 {std::lock_guard<std::mutex> battery(battery_mutex);
 if(!context(false))return fail("cold_restore_completion_context");
 uint16_t op=0,mv=0,temp=0,dc=0,fcc=0;
 if(!safetyWords(op,mv,temp,dc,fcc)||dc!=3000||fcc!=3000||!mosaico_gauge::configExitAccepted(op)||((op>>1)&3)!=3)return fail("cold_restore_fresh_sealed_standard_mismatch");
 t.context.safety.freshAuthenticated131=t.context.safety.calibrationUnchanged=true;
 t.context.safety.image=trial::Image::Source131;
 }
 t.decision=trial::restored(t.decoded.metadata,t.context);
 if(!t.decision.hasNext)return fail("cold_restore_completion_model_rejected");
 t.verifiedOriginalClosure=true;
 if(!commit(t.decision.next)){
  // Only metadata closure failed. Full authenticated source131 + fresh sealed
  // standard + exact old Original were already verified; never issue more IC
  // writes just to repair bookkeeping, and do not hold radio/OTA indefinitely.
  t.metadataClosureFailed=true;
 t.restoreIssued=t.restoreWanted=t.applyInFlight=false;
 t.operationOwner=nullptr;
  if(t.quietOwned&&quietHooks.leave)(void)quietHooks.leave();
 t.quietOwned=false;
  t.reason="cold_Original_verified_metadata_unknown_no_IC_retry_hold_released";
  protection.store(false,std::memory_order_release);
 allowFallback.store(false,std::memory_order_release);
 return false;
 }
 t.restoreIssued=t.restoreWanted=t.applyInFlight=false;
 t.operationOwner=nullptr;
 t.reason="cold_Restored_UNKNOWN_units_full_raw_preserved";
 if(t.quietOwned&&(!quietHooks.leave||!quietHooks.leave()))return fail("cold_quiet_owner_restore_failed_no_overwrite");
 t.quietOwned=false;
 allowFallback.store(true,std::memory_order_release);
 protection.store(false,std::memory_order_release);
 return true;
}
bool evidencePage(size_t index,char* out,size_t n){
 if(!capacity(out,n))return false;
 std::lock_guard<std::recursive_mutex> txn(gauge_transaction_mutex);
 if(!runtime||!runtime->loaded||!runtime->durable){std::snprintf(out,n,"success=0 reason=status_required");
 return false;
 }
 const auto& t=*runtime;
 trial::Digest hash{};
 if(!t.saved.size||t.saved.size>5*192||!crypto::sha256(t.saved.payload.data(),t.saved.size,hash)||hash!=t.bodyHash){std::snprintf(out,n,"success=0 reason=cached_body_SHA_mismatch");
 return false;
 }
 const size_t pages=(t.saved.size+191)/192;
 if(index>=pages){std::snprintf(out,n,"success=0 reason=page_out_of_range pages=%u",unsigned(pages));
 return false;
 }
 char body[65]{},binding[65]{},nonce[33]{};
 static constexpr char digits[]="0123456789abcdef";
 for(size_t i=0;i<32;++i){body[2*i]=digits[hash[i]>>4];
 body[2*i+1]=digits[hash[i]&15];
 binding[2*i]=digits[t.saved.binding[i]>>4];
 binding[2*i+1]=digits[t.saved.binding[i]&15];
 }
 for(size_t i=0;i<16;++i){nonce[2*i]=digits[t.saved.nonce[i]>>4];
 nonce[2*i+1]=digits[t.saved.nonce[i]&15];
 }
 const size_t offset=index*192,count=std::min<size_t>(192,t.saved.size-offset);
 const int used=std::snprintf(out,n,"success=1 page=%u pages=%u offset=%u count=%u envelope_size=%u sequence=%u phase=%u body_SHA=%s binding_SHA=%s nonce=%s data=",unsigned(index),unsigned(pages),unsigned(offset),unsigned(count),unsigned(t.saved.size),unsigned(t.saved.sequence),unsigned(t.saved.phase),body,binding,nonce);
 size_t encoded=0;
 if(used<0||size_t(used)>=n||mbedtls_base64_encode(reinterpret_cast<unsigned char*>(out+used),n-size_t(used),&encoded,t.saved.payload.data()+offset,count)||size_t(used)+encoded>=n){std::snprintf(out,n,"success=0 reason=page_encode_failed");
 return false;
 }
 out[size_t(used)+encoded]=0;
 return true;
}
bool status(char* out,size_t n){
 if(!capacity(out,n))return false;
 Lease lease;
 std::lock_guard<std::recursive_mutex> txn(gauge_transaction_mutex);
 if(protectedState()||!lease.held)return report(out,n,lease.held);
 const auto gct=contrast_load_locked(true);
 if(gct!=contrast_journal::Status::Ready||!contrast_maintenance_original_cached_locked())return report(out,n,false);
 const bool previousHold=protection.load(),previousFallback=allowFallback.load();
 if(!storage()){protection.store(previousHold);
 allowFallback.store(previousFallback);
 return report(out,n,false);
 }
 if(runtime->bound&&!runtime->durable&&!runtime->blocked&&!runtime->historicalOnly){
  const auto& parent=contrast_rt->journal.loaded.records[size_t(contrast_rt->journal.loaded.selected)].snapshot;
  const auto& bound=runtime->authenticated.binding;
  const bool exact=parent.generation==bound.originalGctGeneration&&parent.epoch==bound.originalGctEpoch;
  if(!exact)reject("cold_cached_bound_Original_changed_no_rebase");
  return report(out,n,exact);
 }
 // This path cannot be promoted into active-session authority. Historical
 // data may predate the current safe Original and current firmware slot pair.
 runtime->historicalOnly=true;
 runtime->bound=runtime->applied=runtime->applyInFlight=false;
 runtime->restoreIssued=runtime->restoreWanted=false;
 runtime->operationOwner=nullptr;
 runtime->session={};
 runtime->authenticated.actualImagesVerified=false;
 // Explicit local read-only inspection may expose stale/dirty terminal data,
 // but MUST NOT turn an already-safe Original boot into an upgrade/radio hold.
 bool ok=false;
 {std::lock_guard<std::mutex> battery(battery_mutex);
 ok=mediaGate();
 }
 if(ok){
  const auto loaded=MosaicoNandStore::coldLoad(MosaicoNandStore::ColdArea::Proof,runtime->proof,candidatePolicy(),true,true);
  if(grant(loaded,store::Code::Empty)){runtime->reason="cold_explicit_status_proof_missing_no_capture_or_keys";
 ok=true;
 }
  else ok=grant(loaded)&&authenticate(false,true)&&loadJournal();
 }
 protection.store(previousHold,std::memory_order_release);
 allowFallback.store(previousFallback,std::memory_order_release);
 if(ok&&runtime->durable)runtime->reason="cold_historical_only_signed_backup_NOT_current_images_or_recovery_authority";
 return report(out,n,ok);
}
} // namespace MosaicoGaugeCold
