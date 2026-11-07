#include <algorithm>
#include <atomic>
#include <cassert>
#include <climits>
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <fstream>
#include <iterator>
#include <mutex>
#include <string>
#include <vector>
#include "production/mosaico_ota.h"
#include "sdk/psa/crypto.h"
#include "sdk/hal/hal.h"
int64_t fakeTime=1;
static std::vector<uint8_t> sourceImage,written;
static bool live=true,failWrite=false;
static unsigned writeCalls=0,readCalls=0,callocCalls=0,failCalloc=0,preparingCalls=0;
static uint8_t internalBuffer[4096];
extern "C" void* delta_test_calloc(size_t n,size_t size) {
 ++callocCalls;if(failCalloc&&callocCalls==failCalloc)return nullptr;return std::calloc(n,size);
}
namespace MosaicoOta {
bool readRunningImage(uint32_t offset,uint8_t* data,size_t length) {
 assert(GetHAL().held);++readCalls;
 if(offset>sourceImage.size()||length>sourceImage.size()-offset)return false;
 std::memcpy(data,sourceImage.data()+offset,length);return true;
}
bool writeChunk(uint32_t offset,const uint8_t* data,size_t length) {
 assert(GetHAL().held);++writeCalls;
 assert(data==internalBuffer&&length>0&&length<=4096&&offset==written.size());
 if(!live||failWrite){live=false;return false;}
 written.insert(written.end(),data,data+length);return true;
}
void recordTransferProgress(bool,uint32_t,uint32_t,bool) {}
void recordWorkPhase(WorkPhase phase) {
 assert(phase==WorkPhase::Preparing||phase==WorkPhase::Verifying);
 if(phase==WorkPhase::Preparing)++preparingCalls;
}
}
#include "nesting_actual.h"
// Include the actual production implementation: no test copy of the parser,
// callback bounds, hash verification, aggregation, or codec integration.
#include "production/mosaico_delta.cpp"
namespace ResetActual {
using MosaicoOta::WorkPhase;using MosaicoOta::UiStage;
void publish(UiStage stage){assert(stage==UiStage::Downloading);}
std::mutex lock;
std::atomic<bool> active{true},selected{false},imageReady{false},fullFallbackUsed{false};
bool writing=false,hashLive=false,admitted=true;
unsigned handle=1,received=7,aborts=0,progressCalls=0;
std::atomic<bool> deltaTransfer{true};
std::atomic<uint32_t> transferReceived{7},transferSize{10};
psa_hash_operation_t hash=PSA_HASH_OPERATION_INIT;
struct StorageAdmission {explicit operator bool() const{return admitted;}};
void esp_ota_abort(unsigned){++aborts;}
void recordTransferProgress(bool delta,uint32_t bytes,uint32_t total) {assert(!delta&&!bytes&&!total);++progressCalls;}
#include "reset_actual.h"
void reset(){active=true;selected=false;imageReady=false;fullFallbackUsed=false;writing=true;hashLive=true;admitted=true;handle=1;received=7;aborts=progressCalls=preparingCalls=0;deltaTransfer=true;transferReceived=7;transferSize=10;}
void test() {
 for(unsigned fault=0;fault<5;++fault){reset();
  if(fault==0)active=false;if(fault==1)selected=true;if(fault==2)imageReady=true;
  if(fault==3)fullFallbackUsed=true;if(fault==4)admitted=false;
  assert(!resetDownloadForFullFallback()&&writing&&hashLive&&received==7&&aborts==0&&preparingCalls==0);
 }
 reset();assert(resetDownloadForFullFallback());
 assert(fullFallbackUsed&&!writing&&!hashLive&&!handle&&!received&&active&&aborts==1&&!deltaTransfer&&!transferReceived&&!transferSize);
 assert(preparingCalls==1);
 assert(!resetDownloadForFullFallback()&&preparingCalls==1);
 std::puts("PASS actual fallback reset: one retry, inactive non-revival, selected/ready/admission guards (maintenance power is an SDK boundary)");
}
}
static std::vector<uint8_t> load(const char* name) {
 std::ifstream f(name,std::ios::binary);assert(f.good());
 return std::vector<uint8_t>(std::istreambuf_iterator<char>(f),{});
}
static MosaicoOta::VerifiedImageDescriptor descriptor(const std::vector<uint8_t>& data) {
 MosaicoOta::VerifiedImageDescriptor out{};out.size=(uint32_t)data.size();size_t length=0;
 assert(psa_hash_compute(1,data.data(),data.size(),out.hash,32,&length)==0);return out;
}
static std::string planJson(const MosaicoOta::VerifiedImageDescriptor& base,
 const MosaicoOta::VerifiedImageDescriptor& target,const MosaicoDelta::Plan& plan) {
 char b[65],t[65];MosaicoDelta::hashHex(base.hash,b);MosaicoDelta::hashHex(target.hash,t);
 return std::string("{\"mode\":\"delta\",\"algorithm\":\"detools-heatshrink-v1\",\"base_sha256\":\"")+b+
 "\",\"target_sha256\":\""+t+"\",\"base_size\":"+std::to_string(base.size)+
 ",\"target_size\":"+std::to_string(target.size)+",\"sha256\":\""+plan.sha256+"\",\"size\":"+std::to_string(plan.size)+"}";
}
static std::string change(const std::string& json,const char* key,cJSON* value) {
 cJSON* root=cJSON_Parse(json.c_str());assert(root);
 assert(cJSON_ReplaceItemInObjectCaseSensitive(root,key,value));
 char* result=cJSON_PrintUnformatted(root);std::string out=result;cJSON_free(result);cJSON_Delete(root);return out;
}
static bool parse(const std::string& json,const MosaicoOta::VerifiedImageDescriptor& base,
 const MosaicoOta::VerifiedImageDescriptor& target) {
 MosaicoDelta::Plan out{};return MosaicoDelta::parsePlan(json.c_str(),json.size(),base,target,out);
}
static void testPlan(const MosaicoOta::VerifiedImageDescriptor& base,
 const MosaicoOta::VerifiedImageDescriptor& target,const MosaicoDelta::Plan& plan) {
 const std::string json=planJson(base,target,plan);assert(parse(json,base,target));
 for(const char* key:{"base_size","target_size","size"}) {
  for(double n:{-1.,0.,0.5,4294967296.,1e100})assert(!parse(change(json,key,cJSON_CreateNumber(n)),base,target));
  assert(!parse(change(json,key,cJSON_CreateString("123")),base,target));
 }
 for(const char* key:{"base_sha256","target_sha256","sha256"}) {
  assert(!parse(change(json,key,cJSON_CreateString("not-a-sha")),base,target));
  assert(!parse(change(json,key,cJSON_CreateNumber(0)),base,target));
 }
 assert(!parse(change(json,"base_size",cJSON_CreateNumber(base.size+1)),base,target));
 assert(!parse(change(json,"target_size",cJSON_CreateNumber(target.size+1)),base,target));
 assert(!parse(change(json,"size",cJSON_CreateNumber(MosaicoDelta::MaxPatchSize+1)),base,target));
 assert(!parse(change(json,"size",cJSON_CreateNumber(target.size)),base,target));
 char wrong[65];std::memset(wrong,'0',64);wrong[64]=0;
 assert(!parse(change(json,"base_sha256",cJSON_CreateString(wrong)),base,target));
 assert(!parse(change(json,"target_sha256",cJSON_CreateString(wrong)),base,target));
 assert(!parse(change(json,"mode",cJSON_CreateString("full")),base,target));
 assert(!parse(change(json,"mode",cJSON_CreateString("disabled")),base,target));
 assert(!parse(change(json,"algorithm",cJSON_CreateString("future")),base,target));
 std::string duplicate=json;duplicate.pop_back();duplicate+=",\"size\":1}";assert(!parse(duplicate,base,target));
 auto larger=target;larger.size=4U*1024U*1024U;
 auto capPlan=plan;capPlan.size=MosaicoDelta::MaxPatchSize;
 assert(parse(planJson(base,larger,capPlan),base,larger));
 capPlan.size++;assert(!parse(planJson(base,larger,capPlan),base,larger));
 auto ratioTarget=target;ratioTarget.size=100;auto ratioPlan=plan;ratioPlan.size=90;
 assert(!parse(planJson(base,ratioTarget,ratioPlan),base,ratioTarget));
 ratioPlan.size=89;assert(parse(planJson(base,ratioTarget,ratioPlan),base,ratioTarget));
 std::string embedded=json;embedded.insert(embedded.size()/2,1,'\0');assert(!parse(embedded,base,target));
 assert(!parse(json+"{}",base,target));assert(!parse("{\"size\":NaN}",base,target));
 assert(!parse("{\"size\":1e999}",base,target));assert(!parse("{",base,target));
 auto noBase=base;noBase.size=0;assert(!parse(json,noBase,target));
 std::string deep=json;deep.pop_back();deep+=",\"unknown\":"+std::string(100,'[')+"0"+std::string(100,']')+"}";
 assert(!parse(deep,base,target));
 assert(JsonNestingValid("{\"text\":\"[[[[{{{{\\\"still string\\\"\"}",std::strlen("{\"text\":\"[[[[{{{{\\\"still string\\\"\"}")));
 std::puts("PASS actual plan parser: signed descriptor binding, integer/range/ratio/types, duplicate/unknown/trailing/NaN");
}
static void testChunk() {
 size_t count=0;
 assert(MosaicoDelta::decodeChunk("{\"offset\":0,\"data\":\"YWJj\"}",std::strlen("{\"offset\":0,\"data\":\"YWJj\"}"),0,3,internalBuffer,4096,count)&&count==3);
 const std::vector<std::string> bad={"{\"offset\":1,\"data\":\"YWJj\"}","{\"offset\":0.5,\"data\":\"YWJj\"}",
 "{\"offset\":\"0\",\"data\":\"YWJj\"}","{\"offset\":0,\"data\":\"YWJj\",\"offset\":0}",
 "{\"offset\":0,\"data\":\"@@@\"}","{\"offset\":0,\"data\":\"YQ==\"}","{\"offset\":0,\"data\":\"YWJj\"}{}"};
 for(const auto& json:bad)assert(!MosaicoDelta::decodeChunk(json.c_str(),json.size(),0,3,internalBuffer,4096,count));
 assert(!MosaicoDelta::decodeChunk("{}",2,3,3,internalBuffer,4096,count));
 std::string deep="{\"offset\":0,\"data\":\"YWJj\",\"unknown\":"+std::string(1000,'[')+"0"+std::string(1000,']')+"}";
 assert(!MosaicoDelta::decodeChunk(deep.c_str(),deep.size(),0,3,internalBuffer,4096,count));
 std::puts("PASS actual chunk JSON/offset/count validation (Foundation base64 SDK shim)");
}
static void clean(){assert(!GetHAL().held);GetHAL()=Hal{};written.clear();writeCalls=readCalls=callocCalls=failCalloc=0;failWrite=false;live=true;fakeTime=1;}
static bool run(const std::vector<uint8_t>& patch,const MosaicoDelta::Plan& plan,
 const MosaicoOta::VerifiedImageDescriptor& base,const MosaicoOta::VerifiedImageDescriptor& target) {
 const bool ok=MosaicoDelta::reconstruct(patch.data(),plan,base,target,internalBuffer,4096,1000000);
 assert(!GetHAL().held);return ok;
}
int main(int argc,char** argv) {
 assert(argc==4);sourceImage=load(argv[1]);auto targetBytes=load(argv[2]);auto patch=load(argv[3]);
 auto base=descriptor(sourceImage),target=descriptor(targetBytes),patchDescriptor=descriptor(patch);
 MosaicoDelta::Plan plan{};plan.size=patchDescriptor.size;std::memcpy(plan.hash,patchDescriptor.hash,32);MosaicoDelta::hashHex(plan.hash,plan.sha256);
 testPlan(base,target,plan);testChunk();ResetActual::test();
 clean();assert(run(patch,plan,base,target)&&written==targetBytes&&writeCalls==(target.size+4095)/4096&&readCalls>0);
 const unsigned successBegins=GetHAL().begins;
 assert(GetHAL().ends==successBegins&&successBegins==(plan.size+255)/256+1);
 for(unsigned fail:{1U,2U,successBegins,~0U}) {
  clean();GetHAL().failBegin=fail;
  assert(!run(patch,plan,base,target)&&!GetHAL().held);
  assert(GetHAL().ends<=GetHAL().begins);
  if(fail==~0U)assert(!readCalls&&!writeCalls);
 }
 std::puts("PASS display windows: successful batching/finalize, first/mid/final/all acquisition failure, cleanup releases");
 std::puts("PASS real base -> target detools-heatshrink streaming reconstruction + exact internal 4096 aggregation + real SHA256");
 clean();auto corrupt=patch;corrupt[patch.size()/2]^=1;assert(!run(corrupt,plan,base,target)&&writeCalls==0);
 std::puts("PASS corrupted patch rejected by real SHA256 before any codec/flash output");
 clean();auto truncated=patch;truncated.resize(patch.size()/2);auto truncatedDescriptor=descriptor(truncated);auto shortPlan=plan;shortPlan.size=truncated.size();std::memcpy(shortPlan.hash,truncatedDescriptor.hash,32);
 assert(!run(truncated,shortPlan,base,target));std::puts("PASS hash-valid truncated patch codec/finalize failure");
 clean();auto smallTarget=target;smallTarget.size=1;assert(!run(patch,plan,base,smallTarget)&&written.empty());std::puts("PASS output target overflow rejected before NOR write");
 clean();auto smallBase=base;smallBase.size=1;assert(!run(patch,plan,smallBase,target));std::puts("PASS running-source image bounds reject codec base read overrun");
 clean();auto wrongTarget=target;wrongTarget.hash[0]^=1;assert(!run(patch,plan,base,wrongTarget));std::puts("PASS reconstructed signed target hash mismatch rejects before finishDownload");
 clean();failWrite=true;assert(!run(patch,plan,base,target)&&!live&&writeCalls==1);std::puts("PASS flash/safety failure not retried by finalize callbacks");
 for(unsigned n:{1U,2U}) {clean();failCalloc=n;assert(!run(patch,plan,base,target)&&writeCalls==0);}std::puts("PASS official codec context/apply-patch OOM returns full-fallback failure");
 clean();fakeTime=1000000;assert(!run(patch,plan,base,target)&&writeCalls==0&&readCalls==0&&GetHAL().ends==0);std::puts("PASS total deadline stops decode before output");
 clean();MosaicoDelta::Decoder d{internalBuffer,base.size,target.size};d.deadline=1000000;
 d.buffered=1;
 assert(MosaicoDelta::Decoder::read(internalBuffer,1,0,&d)!=ESP_OK);
 assert(MosaicoDelta::Decoder::write(internalBuffer,1,&d)!=ESP_OK);
 assert(!d.flush()&&!readCalls&&!writeCalls&&!d.produced);
 d.displayHeld=true;fakeTime=d.deadline;
 assert(!d.flush()&&!readCalls&&!writeCalls&&d.buffered==1);
 std::puts("PASS buffered tail flush after deadline performs no flash write");
 fakeTime=1;d.buffered=0;

 assert(MosaicoDelta::Decoder::read(internalBuffer,1,-1,&d)!=ESP_OK);
 assert(MosaicoDelta::Decoder::read(internalBuffer,1,INT_MAX,&d)!=ESP_OK);
 assert(MosaicoDelta::Decoder::read(internalBuffer,SIZE_MAX,0,&d)!=ESP_OK);
 assert(MosaicoDelta::Decoder::read(internalBuffer,1,(int)base.size,&d)!=ESP_OK);
 assert(MosaicoDelta::Decoder::write(internalBuffer,SIZE_MAX,&d)!=ESP_OK);
 assert(readCalls==0&&writeCalls==0);std::puts("PASS direct production signed offset/SIZE_MAX/read-end/output overflow guards");
 std::puts("ALL PASS");
}
