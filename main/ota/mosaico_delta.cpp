#include "mosaico_delta.h"
#include <hal/hal.h>
#include <host/token_history.h>
#include <cJSON.h>
#include <esp_delta_ota.h>
#include <esp_timer.h>
#include <esp_log.h>
#include <psa/crypto.h>
#include <mbedtls/base64.h>
#include <algorithm>
#include <climits>
#include <cmath>
#include <cstring>
#include <freertos/FreeRTOS.h>
#include <freertos/task.h>
namespace MosaicoDelta {
namespace {
bool text(const cJSON* root,const char* name,const char* expected)
{
    const auto* v=cJSON_GetObjectItemCaseSensitive(root,name);
    return cJSON_IsString(v)&&v->valuestring&&!std::strcmp(v->valuestring,expected);
}
bool integer(const cJSON* root,const char* name,uint32_t& out)
{
    const auto* v=cJSON_GetObjectItemCaseSensitive(root,name);
    if(!cJSON_IsNumber(v)||!std::isfinite(v->valuedouble)||v->valuedouble<=0||
       v->valuedouble>UINT32_MAX||std::floor(v->valuedouble)!=v->valuedouble)return false;
    out=static_cast<uint32_t>(v->valuedouble);return true;
}
bool digest(const char* sha,uint8_t out[32])
{
    if(!sha||std::strlen(sha)!=64)return false;
    for(unsigned i=0;i<64;++i) {
        const char c=sha[i];
        if(!((c>='0'&&c<='9')||(c>='a'&&c<='f')))return false;
        const uint8_t n=c<='9'?c-'0':c-'a'+10;
        if(i%2)out[i/2]|=n;else out[i/2]=n<<4;
    }
    return true;
}
bool uniqueKeys(const cJSON* root)
{
    if(!cJSON_IsObject(root))return false;
    for(auto* a=root->child;a;a=a->next)
        for(auto* b=a->next;b;b=b->next)
            if(!a->string||!b->string||!std::strcmp(a->string,b->string))return false;
    return true;
}
struct Decoder {
    uint8_t* chunk;
    uint32_t baseSize,targetSize,produced=0,written=0;
    size_t buffered=0;
    bool cancelled=false;
    bool displayHeld=false;
    int64_t deadline=0;
    psa_hash_operation_t hash=PSA_HASH_OPERATION_INIT;
    bool flush() {
        if(!displayHeld)return false;
        if(!buffered)return true;
        if(!MosaicoOta::writeChunk(written,chunk,buffered))return false;
        written+=static_cast<uint32_t>(buffered);buffered=0;return true;
    }
    static esp_err_t read(uint8_t* data,size_t length,int offset,void* opaque) {
        auto& d=*static_cast<Decoder*>(opaque);
        // esp_delta_ota passes absolute signed offset, incremented after each read.
        // Also bound the subsequent vendor int advance, not merely partition IO.
        if(!d.displayHeld||d.cancelled||esp_timer_get_time()>=d.deadline||offset<0||!length||
           static_cast<uint32_t>(offset)>d.baseSize||length>d.baseSize-static_cast<uint32_t>(offset)||
           length>static_cast<size_t>(INT_MAX-offset))return ESP_FAIL;
        return MosaicoOta::readRunningImage(static_cast<uint32_t>(offset),data,length)?ESP_OK:ESP_FAIL;
    }
    static esp_err_t write(const uint8_t* data,size_t length,void* opaque) {
        auto& d=*static_cast<Decoder*>(opaque);
        if(!d.displayHeld||d.cancelled||esp_timer_get_time()>=d.deadline||!data||!length||d.produced>d.targetSize||
           length>d.targetSize-d.produced)return ESP_FAIL;
        if(psa_hash_update(&d.hash,data,length)!=PSA_SUCCESS)return ESP_FAIL;
        d.produced+=static_cast<uint32_t>(length);
        while(length) {
            const size_t count=std::min<size_t>(4096-d.buffered,length);
            std::memcpy(d.chunk+d.buffered,data,count);
            d.buffered+=count;data+=count;length-=count;
            if(d.buffered==4096&&!d.flush())return ESP_FAIL;
        }
        return ESP_OK;
    }
};
// Acquire outside Storage/OTA owner locks. Cleanup may run without a window,
// but Decoder callbacks then fail closed before touching either flash path.
class DisplayFlashWindow {
public:
    explicit DisplayFlashWindow(Decoder& decoder) : d(decoder) {
        d.displayHeld=GetHAL().beginDisplayFlashWindow();
    }
    ~DisplayFlashWindow() {
        if(d.displayHeld) {d.displayHeld=false;GetHAL().endDisplayFlashWindow();}
    }
    explicit operator bool() const {return d.displayHeld;}
    DisplayFlashWindow(const DisplayFlashWindow&)=delete;
    DisplayFlashWindow& operator=(const DisplayFlashWindow&)=delete;
private:
    Decoder& d;
};
}
void hashHex(const uint8_t hash[32],char out[65])
{
    const char* digits="0123456789abcdef";
    for(unsigned i=0;i<32;++i){out[2*i]=digits[hash[i]>>4];out[2*i+1]=digits[hash[i]&15];}
    out[64]=0;
}
bool parsePlan(const char* json,size_t length,const MosaicoOta::VerifiedImageDescriptor& base,
               const MosaicoOta::VerifiedImageDescriptor& target,Plan& out)
{
    out={};
    if(!json||!length||length>2048||!base.size||!target.size||
       std::memchr(json,0,length)||!JsonNestingValid(json,length)||std::strstr(json,"\\u0000"))return false;
    const char* end=nullptr;
    cJSON* root=cJSON_ParseWithLengthOpts(json,length+1,&end,true);
    char baseSha[65],targetSha[65];hashHex(base.hash,baseSha);hashHex(target.hash,targetSha);
    uint32_t baseSize=0,targetSize=0,patchSize=0;
    const auto* sha=root?cJSON_GetObjectItemCaseSensitive(root,"sha256"):nullptr;
    Plan plan{};
    const bool ok=uniqueKeys(root)&&text(root,"mode","delta")&&
        text(root,"algorithm","detools-heatshrink-v1")&&text(root,"base_sha256",baseSha)&&
        text(root,"target_sha256",targetSha)&&integer(root,"base_size",baseSize)&&baseSize==base.size&&
        integer(root,"target_size",targetSize)&&targetSize==target.size&&integer(root,"size",patchSize)&&
        patchSize<=MaxPatchSize&&uint64_t(patchSize)*10<uint64_t(targetSize)*9&&
        cJSON_IsString(sha)&&digest(sha->valuestring,plan.hash);
    if(ok){plan.size=patchSize;std::memcpy(plan.sha256,sha->valuestring,65);out=plan;}
    cJSON_Delete(root);return ok;
}
bool decodeChunk(const char* json,size_t length,uint32_t offset,uint32_t total,
                 uint8_t* destination,size_t capacity,size_t& count)
{
    count=0;
    if(!json||!destination||capacity!=4096||offset>=total||!length||length>8192||
       std::memchr(json,0,length)||!JsonNestingValid(json,length)||std::strstr(json,"\\u0000"))return false;
    const char* end=nullptr;
    cJSON* root=cJSON_ParseWithLengthOpts(json,length+1,&end,true);
    const auto* position=root?cJSON_GetObjectItemCaseSensitive(root,"offset"):nullptr;
    const auto* data=root?cJSON_GetObjectItemCaseSensitive(root,"data"):nullptr;
    const bool ok=uniqueKeys(root)&&cJSON_IsNumber(position)&&position->valuedouble==offset&&
        cJSON_IsString(data)&&data->valuestring&&
        mbedtls_base64_decode(destination,capacity,&count,
            reinterpret_cast<const unsigned char*>(data->valuestring),std::strlen(data->valuestring))==0&&
        count==std::min<uint32_t>(4096,total-offset);
    cJSON_Delete(root);return ok;
}
bool patchHashMatches(const uint8_t* patch,const Plan& plan)
{
    if(!patch||!plan.size||plan.size>MaxPatchSize)return false;
    uint8_t digest[32];size_t length=0;
    return psa_hash_compute(PSA_ALG_SHA_256,patch,plan.size,digest,sizeof(digest),&length)==PSA_SUCCESS&&
        length==32&&!std::memcmp(digest,plan.hash,32);
}
bool reconstruct(const uint8_t* patch,const Plan& plan,
                 const MosaicoOta::VerifiedImageDescriptor& base,
                 const MosaicoOta::VerifiedImageDescriptor& target,
                 uint8_t* internalChunk,size_t capacity,int64_t deadlineUs)
{
    if(!internalChunk||capacity!=4096||!base.size||!target.size||!patchHashMatches(patch,plan)) {
        ESP_LOGW("MosaicoDelta", "reconstruct_failed reason=input_or_patch_hash offset=0 prod=0 written=0");
        return false;
    }
    Decoder d{internalChunk,base.size,target.size};d.deadline=deadlineUs;
    if(psa_hash_setup(&d.hash,PSA_ALG_SHA_256)!=PSA_SUCCESS) {
        ESP_LOGW("MosaicoDelta", "reconstruct_failed reason=hash_setup offset=0 prod=0 written=0");
        return false;
    }
    esp_delta_ota_cfg_t cfg{};cfg.user_data=&d;
    cfg.read_cb_with_user_data=Decoder::read;cfg.write_cb_with_user_data=Decoder::write;
    const auto decoder=esp_delta_ota_init(&cfg);
    bool ok=decoder!=nullptr;
    const char* reason="decoder_init";
    uint32_t failedOffset=0;
    MosaicoOta::recordTransferProgress(true,plan.size,plan.size,true);
    for(uint32_t offset=0;ok&&offset<plan.size;) {
        failedOffset=offset;
        const uint32_t count=std::min<uint32_t>(256,plan.size-offset);
        {
            DisplayFlashWindow window(d);
            if(!window){ok=false;reason="feed_display_window";}
            else if(esp_timer_get_time()>=deadlineUs){ok=false;reason="feed_deadline";}
            else {reason="feed_codec";ok=esp_delta_ota_feed_patch(decoder,patch+offset,count)==ESP_OK;}
        }
        offset+=count;vTaskDelay(pdMS_TO_TICKS(1));
    }
    if(ok)failedOffset=plan.size;
    if(!ok)d.cancelled=true;
    if(decoder) {
        DisplayFlashWindow window(d);
        if(!window){if(ok)reason="finalize_display_window";ok=false;d.cancelled=true;}
        // Finalize must destroy the official reader even on lock/feed failure.
        const bool finalized=esp_delta_ota_finalize(decoder)==ESP_OK;
        if(ok) {
            reason="finalize_or_tail";
            ok=finalized&&d.produced==target.size&&d.flush()&&d.written==target.size;
        }
    }
    if(ok)MosaicoOta::recordWorkPhase(MosaicoOta::WorkPhase::Verifying);
    uint8_t digest[32];size_t length=0;
    if(ok) {
        reason="target_hash";
        ok=psa_hash_finish(&d.hash,digest,sizeof(digest),&length)==PSA_SUCCESS&&length==32&&
            !std::memcmp(digest,target.hash,32);
    }
    if(!ok)ESP_LOGW("MosaicoDelta", "reconstruct_failed reason=%s offset=%u prod=%u written=%u",
                   reason,static_cast<unsigned>(failedOffset),static_cast<unsigned>(d.produced),
                   static_cast<unsigned>(d.written));
    psa_hash_abort(&d.hash);
    if(decoder)esp_delta_ota_deinit(decoder);
    return ok;
}
}

