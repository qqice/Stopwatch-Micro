#include "mosaico_gauge_cold_crypto.h"
#include <ota/ota_public_key.h>
#include "sdkconfig.h"
#include "psa/crypto.h"
#include "mbedtls/pk.h"
#include "mbedtls/base64.h"
#include "esp_random.h"
#include "bootloader_random.h"
#include "esp_ota_ops.h"
#include "esp_task_wdt.h"
#include "cJSON.h"
#include <algorithm>
#include <atomic>
#include <cmath>
#include <cstdio>
#include <cstring>
#include <memory>
#include <new>

namespace MosaicoColdCrypto {
namespace proof=mosaico_gauge_cold_proof;
namespace capsule=mosaico_gauge_cold_capsule;
namespace trial=mosaico_gauge_cold_trial;
namespace store=mosaico_gauge_cold_store;
namespace {
constexpr const char* Versions[2]={"0.13.95-r17-mosaico-ota","0.13.95-r18-mosaico-ota"};
constexpr const char* GctDigest="da8c79bcf2a17f01a3294394d7ca9b527d1a623a45fa1bc04c730a988cd69bdb";
// One writer publishes an immutable token. Readers never inspect token storage
// while initialization/failure is in progress; no retry can race publication.
std::atomic<uint8_t> bootState{0}; //0 Unprepared,1 Initializing,2 Ready,3 Failed
trial::BootToken bootToken{};
bool allowedVersion(const char* version){
    return version&&(!std::strncmp(version,Versions[0],32)||!std::strncmp(version,Versions[1],32));
}
bool allowedPairVersions(const MosaicoOta::VerifiedImageDescriptor (&images)[2]){
    return allowedVersion(images[0].version)&&allowedVersion(images[1].version)&&
        std::strncmp(images[0].version,images[1].version,32)!=0;
}
bool fail(const char*& reason,const char* value){reason=value;return false;}
bool bindingWire(const uint8_t* data,size_t size,Binding& out){
    out={};if(!data||size!=proof::BindingSize)return false;
    capsule::BindingBytes wire{};std::memcpy(wire.data(),data,size);
    if(capsule::get(wire,0,4)!=capsule::BindingMagic||capsule::get(wire,4,2)!=trial::protocolVersion||
       capsule::get(wire,6,2)!=proof::BindingSize)return false;
    Binding b{};capsule::readBinding(wire,8,b);capsule::BindingBytes canonical{};
    if(!proof::bindingHashInput(b,canonical)||canonical!=wire)return false;
    out=b;return true;
}
bool signature(const uint8_t* data,size_t size,const uint8_t* sig,size_t length){
    if(!proof::derShape({sig,length}))return false;
    Digest hash{};if(!sha256(data,size,hash))return false;
    mbedtls_pk_context key;mbedtls_pk_init(&key);
    int rc=mbedtls_pk_parse_public_key(&key,reinterpret_cast<const unsigned char*>(MosaicoOtaPublicKey),sizeof(MosaicoOtaPublicKey));
    if(rc==0&&(!mbedtls_pk_can_do_psa(&key,MBEDTLS_PK_ALG_ECDSA(PSA_ALG_SHA_256),PSA_KEY_USAGE_VERIFY_HASH)||mbedtls_pk_get_bitlen(&key)!=256))rc=-1;
    if(rc==0)rc=mbedtls_pk_verify(&key,MBEDTLS_MD_SHA256,hash.data(),hash.size(),sig,length);
    mbedtls_pk_free(&key);return rc==0;
}
struct JsonDelete {void operator()(cJSON* p)const{cJSON_Delete(p);}};
using Json=std::unique_ptr<cJSON,JsonDelete>;
struct PrintedDelete {void operator()(char* p)const{cJSON_free(p);}};
// cJSON retains duplicate members. Check their decoded names recursively before
// any field lookup/printing. Reject non-finite numbers and excessive nesting.
bool strictTree(const cJSON* node,unsigned depth=0){
    if(!node||depth>8)return false;
    if(cJSON_IsNumber(node)&&!std::isfinite(node->valuedouble))return false;
    for(const cJSON* a=node->child;a;a=a->next){
        if(cJSON_IsObject(node)){
            if(!a->string)return false;
            for(const cJSON* b=a->next;b;b=b->next)if(b->string&&!std::strcmp(a->string,b->string))return false;
        }
        if(!strictTree(a,depth+1))return false;
    }
    return true;
}
bool fields(const cJSON* object,const char* const* names,size_t count){
    if(!cJSON_IsObject(object))return false;
    size_t found=0;
    for(const cJSON* item=object->child;item;item=item->next){
        bool known=false;for(size_t i=0;i<count;++i)if(item->string&&!std::strcmp(item->string,names[i])){known=true;break;}
        if(!known)return false;
        ++found;
    }
    return found==count;
}
// Raw NUL is rejected by proof::decode. Also reject JSON's escaped NUL before
// cJSON can truncate a decoded key/value. Other escapes are decoded by cJSON.
bool noEscapedNul(const char* text,size_t size){
    for(size_t i=0;i<size;++i)if(text[i]=='\\'){
        if(i+1>=size)return false;
        if(text[i+1]=='u'&&i+5<size&&text[i+2]=='0'&&text[i+3]=='0'&&text[i+4]=='0'&&text[i+5]=='0')return false;
        ++i;
    }
    return true;
}
// Bound recursion BEFORE the SDK cJSON parser (whose global nesting limit is
// intentionally unchanged). Brackets inside JSON strings/escapes are inert.
// This is only a lexical depth/balance gate, not a replacement JSON parser.
bool pairLexicalDepth(const uint8_t* data,size_t size){
    if(!data||!size||size>proof::PairRawMax)return false;
    char brackets[8]{};size_t depth=0;bool quoted=false,escaped=false;
    for(size_t i=0;i<size;++i){
        const uint8_t ch=data[i];if(!ch)return false;
        if(quoted){
            if(ch<0x20)return false;
            if(escaped){escaped=false;continue;}
            if(ch=='\\'){escaped=true;continue;}
            if(ch=='"')quoted=false;
            continue;
        }
        if(ch=='"'){quoted=true;continue;}
        if(ch=='['||ch=='{'){
            if(depth==sizeof(brackets))return false;
            brackets[depth++]=char(ch);
        }else if(ch==']'||ch=='}'){
            if(!depth||brackets[depth-1]!=(ch==']'?'[':'{'))return false;
            --depth;
        }
    }
    return !quoted&&!escaped&&!depth;
}
bool detached(const cJSON* value,const MosaicoOta::VerifiedImageDescriptor& image){
    if(!cJSON_IsString(value)||!value->valuestring)return false;
    const size_t length=std::strlen(value->valuestring);if(!length||length>104)return false;
    uint8_t sig[72]{};size_t used=0;
    if(mbedtls_base64_decode(sig,sizeof(sig),&used,reinterpret_cast<const uint8_t*>(value->valuestring),length))return false;
    char hex[65]{};for(size_t i=0;i<32;++i)std::snprintf(hex+i*2,3,"%02x",image.hash[i]);
    char message[256]{};const int n=std::snprintf(message,sizeof(message),"MOSAICO-GAUGE-CONTRAST-CAP-v1\n%s\n%s\n%lu\n%s\n",GctDigest,image.version,static_cast<unsigned long>(image.size),hex);
    return n>0&&size_t(n)<sizeof(message)&&signature(reinterpret_cast<const uint8_t*>(message),size_t(n),sig,used);
}
bool pair(const proof::Bytes& raw,Authenticated& candidate){
    if(!pairLexicalDepth(raw.data,raw.size))return false;
    std::unique_ptr<char[]> text(new(std::nothrow) char[raw.size+1]);if(!text)return false;
    std::memcpy(text.get(),raw.data,raw.size);text[raw.size]=0;
    if(!noEscapedNul(text.get(),raw.size))return false;
    const char* end=nullptr;Json root(cJSON_ParseWithLengthOpts(text.get(),raw.size+1,&end,true));
    if(!root||end!=text.get()+raw.size||!strictTree(root.get())||!cJSON_IsArray(root.get())||cJSON_GetArraySize(root.get())!=2)return false;
    static constexpr const char* PairFields[]={"image","signature"};
    static constexpr const char* ImageFields[]={"schema","board","chip","project","layout","version","size","sha256","signature"};
    for(unsigned i=0;i<2;++i){
        const auto* entry=cJSON_GetArrayItem(root.get(),int(i));
        const auto* image=cJSON_GetObjectItemCaseSensitive(entry,"image");
        if(!fields(entry,PairFields,2)||!fields(image,ImageFields,9))return false;
        const auto* schema=cJSON_GetObjectItemCaseSensitive(image,"schema");
        if(!cJSON_IsNumber(schema)||schema->valuedouble!=2)return false;
        std::unique_ptr<char,PrintedDelete> encoded(cJSON_PrintUnformatted(image));
        if(!encoded||!MosaicoOta::verifySignedImageDescriptor(encoded.get(),candidate.images[i])||
           !allowedVersion(candidate.images[i].version)||
           std::memcmp(candidate.images[i].hash,candidate.binding.slotHashes[i].data(),32)||
           !detached(cJSON_GetObjectItemCaseSensitive(entry,"signature"),candidate.images[i]))return false;
    }
    return allowedPairVersions(candidate.images);
}
bool actual(unsigned slot,const MosaicoOta::VerifiedImageDescriptor& image){
    if(slot>=2||!allowedVersion(image.version))return false;
    const auto* part=esp_partition_find_first(ESP_PARTITION_TYPE_APP,slot?ESP_PARTITION_SUBTYPE_APP_OTA_1:ESP_PARTITION_SUBTYPE_APP_OTA_0,nullptr);
    esp_ota_img_states_t state{};esp_app_desc_t desc{};
    if(!part||!image.size||image.size>part->size||esp_ota_get_state_partition(part,&state)!=ESP_OK||state!=ESP_OTA_IMG_VALID||
       esp_ota_get_partition_description(part,&desc)!=ESP_OK||std::strncmp(desc.version,image.version,sizeof(desc.version))||
       std::strncmp(desc.project_name,"Stopwatch-Mosaico",sizeof(desc.project_name)))return false;
    bool nonzero=false;for(auto byte:desc.app_elf_sha256)nonzero|=byte!=0;if(!nonzero||psa_crypto_init()!=PSA_SUCCESS)return false;
    psa_hash_operation_t operation=PSA_HASH_OPERATION_INIT;
    bool ok=psa_hash_setup(&operation,PSA_ALG_SHA_256)==PSA_SUCCESS;
    uint8_t buffer[256]{};
    for(size_t offset=0;ok&&offset<image.size;){
        const size_t count=std::min<size_t>(sizeof(buffer),image.size-offset);
        // Only reset this task's WDT subscription; no subscription or global
        // watchdog changes. A non-subscribed caller needs no WDT feeding.
        if(esp_task_wdt_status(nullptr)==ESP_OK)(void)esp_task_wdt_reset();
        ok=esp_partition_read(part,offset,buffer,count)==ESP_OK&&psa_hash_update(&operation,buffer,count)==PSA_SUCCESS;offset+=count;
    }
    Digest hash{};size_t length=0;if(ok)ok=psa_hash_finish(&operation,hash.data(),hash.size(),&length)==PSA_SUCCESS&&length==hash.size();
    (void)psa_hash_abort(&operation);return ok&&!std::memcmp(hash.data(),image.hash,32);
}
} // namespace

bool sha256(const uint8_t* data,size_t size,Digest& out){
    out={};if(!data||!size||psa_crypto_init()!=PSA_SUCCESS)return false;
    Digest hash{};size_t used=0;
    if(psa_hash_compute(PSA_ALG_SHA_256,data,size,hash.data(),hash.size(),&used)!=PSA_SUCCESS||used!=hash.size())return false;
    out=hash;return true;
}
void prepareBootIdentity(){
    uint8_t expected=0;
    if(!bootState.compare_exchange_strong(expected,1,std::memory_order_acq_rel,std::memory_order_acquire))return;
    trial::BootToken local{};
#if defined(CONFIG_IDF_TARGET_ESP32S31) && CONFIG_IDF_TARGET_ESP32S31 && !CONFIG_ESP_BRINGUP_BYPASS_RANDOM_SETTING
    bootloader_random_enable();esp_fill_random(local.nonce.data(),local.nonce.size());bootloader_random_disable();
    bool zero=true,bypass=true;for(auto byte:local.nonce){zero&=byte==0;bypass&=byte==0x5a;}
    if(!zero&&!bypass)local.verified=true;
#endif
    if(!local.verified){bootState.store(3,std::memory_order_release);return;}
    bootToken=local;bootState.store(2,std::memory_order_release);
}
trial::BootToken bootIdentity(){
    return bootState.load(std::memory_order_acquire)==2?bootToken:trial::BootToken{};
}
bool extractBinding(const Record& record,Binding& candidate){
    candidate={};if(!store::shape(record)||record.sequence!=1||record.phase!=1||record.size<proof::HeaderSize+proof::BindingSize)return false;
    Binding binding{};if(!bindingWire(record.payload.data()+proof::HeaderSize,proof::BindingSize,binding)||
       !proof::decode(record.payload,record.size,binding).canonical()||record.nonce!=binding.nonce)return false;
    candidate=binding;return true;
}
bool authenticate(const Record& record,const std::array<uint8_t,6>& boardMac,const mosaico_gauge_contrast::Raw& durableSource,Authenticated& out,const char*& reason){
    out={};reason="cold_auth_unverified";
    if(!MosaicoOta::currentTaskOwnsStorageLease())return fail(reason,"cold_auth_ota_lease_required");
    Authenticated candidate{};
    if(!extractBinding(record,candidate.binding)||candidate.binding.mac!=boardMac)return fail(reason,"cold_auth_shape_or_mac");
    if(durableSource!=mosaico_gauge_contrast::sourceFixture)return fail(reason,"cold_auth_complete_source_mismatch");
    proof::SourceReceiptInput source{};Digest receipt{};
    if(!proof::sourceReceiptHashInput(candidate.binding,durableSource,source)||!sha256(source.data(),source.size(),receipt)||receipt!=candidate.binding.source131ReceiptDigest)return fail(reason,"cold_auth_source_receipt");
    proof::BindingInput wire{};if(!proof::bindingHashInput(candidate.binding,wire)||!sha256(wire.data(),wire.size(),candidate.bindingHash)||candidate.bindingHash!=record.binding)return fail(reason,"cold_auth_binding_sha");
    const auto decoded=proof::decode(record.payload,record.size,candidate.binding);const auto& view=decoded.view;
    if(!sha256(view.pairRaw.data,view.pairRaw.size,candidate.pairHash)||!sha256(record.payload.data(),record.size,candidate.packetHash))return fail(reason,"cold_auth_pair_or_packet_sha");
    proof::SessionInput message{};if(!proof::sessionMessage(candidate.bindingHash,candidate.pairHash,message)||!signature(message.data(),message.size(),view.signature.data,view.signature.size))return fail(reason,"cold_auth_session_signature");
    // Authenticate the fixed binary session BEFORE any attacker-controlled
    // JSON reaches cJSON. A valid session still requires strict pair parsing.
    if(!pair(view.pairRaw,candidate))return fail(reason,"cold_auth_pair_signatures");
    candidate.sessionVerified=true;candidate.actualImagesVerified=false;out=candidate;reason="cold_auth_session_only_not_actual_images";return true;
}
bool verifyActualImages(Authenticated& value,const char*& reason){
    Authenticated candidate=value;value={};reason="cold_actual_unverified";
    candidate.actualImagesVerified=false;
    if(!MosaicoOta::currentTaskOwnsStorageLease())return fail(reason,"cold_actual_ota_lease_required");
    if(!candidate.sessionVerified||!allowedPairVersions(candidate.images)||!proof::productionBinding(candidate.binding)||!trial::nonzero(candidate.bindingHash)||!trial::nonzero(candidate.pairHash)||!trial::nonzero(candidate.packetHash))return fail(reason,"cold_actual_session_required");
    proof::BindingInput wire{};Digest bindingHash{};
    if(!proof::bindingHashInput(candidate.binding,wire)||!sha256(wire.data(),wire.size(),bindingHash)||bindingHash!=candidate.bindingHash)return fail(reason,"cold_actual_binding_changed");
    for(unsigned i=0;i<2;++i)if(std::memcmp(candidate.images[i].hash,candidate.binding.slotHashes[i].data(),32)||!actual(i,candidate.images[i]))return fail(reason,"cold_actual_sdk_or_full_image_mismatch");
    candidate.actualImagesVerified=true;value=candidate;reason="cold_actual_both_sdk_valid_full_signed_images";return true;
}
bool buildFromFragment(const uint8_t* fragment,size_t size,const char* exactPair,Record& out,const char*& reason){
    out={};reason="cold_fragment_invalid";
    if(!fragment||size<4+proof::BindingSize+2+8||size>266||std::memcmp(fragment,"GCS1",4)||!exactPair)return false;
    Binding binding{};if(!bindingWire(fragment+4,proof::BindingSize,binding))return false;
    const size_t sigSize=size_t(fragment[192])|(size_t(fragment[193])<<8);
    if(sigSize<8||sigSize>72||size!=194+sigSize)return false;
    const size_t pairSize=strnlen(exactPair,proof::PairRawMax+1);if(!pairSize||pairSize>proof::PairRawMax)return false;
    size_t used=0;if(proof::encode(binding,{reinterpret_cast<const uint8_t*>(exactPair),pairSize},{fragment+194,sigSize},out.payload,used)!=proof::Error::None){out={};return false;}
    out.size=uint16_t(used);out.phase=1;out.sequence=1;out.nonce=binding.nonce;
    proof::BindingInput wire{};if(!proof::bindingHashInput(binding,wire)||!sha256(wire.data(),wire.size(),out.binding)){out={};return fail(reason,"cold_fragment_sha_failed");}
    reason="cold_fragment_structural_only_authenticate_required";return true;
}
} // namespace MosaicoColdCrypto
