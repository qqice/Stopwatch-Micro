#include "mosaico_system_log.h"
#include "mosaico_system_log_model.h"
#ifdef MOSAICO_NAND_STORE
#include <hal/hal.h>
#include <hal/mosaico/mosaico_nand_store.h>
#include <host/network_quota.h>
#include <host/tailscale_transport.h>
#include <host/system_clock.h>
#include <ota/mosaico_ota.h>
#include <ota/panic_capture.h>
#include <esp_app_desc.h>
#include <esp_system.h>
#include <esp_heap_caps.h>
#include <esp_timer.h>
#include <esp_ota_ops.h>
#include <esp_log.h>
#include <esp_memory_utils.h>
#include <esp_private/cache_utils.h>
#include <nvs.h>
#include <freertos/FreeRTOS.h>
#include <freertos/task.h>
#include <freertos/idf_additions.h>
#include <atomic>
#include <cstdarg>
#include <cstdio>
#include <cstring>
#include <new>
extern "C" const char _rodata_start[],_rodata_end[];
namespace MosaicoSystemLog {
namespace {
using namespace Model;
namespace NL=MosaicoNandStore::LogModel;
struct Storage {Queue queue;MosaicoNandStore::LogPage page{},readPage{};MosaicoNandStore::LogState state{};Record power{},ota{},network{},tcp{};MosaicoOta::UiSnapshot otaUi{};};
Storage* storage=nullptr;
std::atomic_flag queueLock=ATOMIC_FLAG_INIT,readLock=ATOMIC_FLAG_INIT,hookLock=ATOMIC_FLAG_INIT;
std::atomic<TaskHandle_t> workerHandle{nullptr};
std::atomic<bool> initialized{false},urgent{false},flushRequested{false},allocationError{false},taskError{false},journalReady{false};
std::atomic<ReadStatus> reader{ReadStatus::Idle};
std::atomic<uint32_t> queued{0},drops{0},hookDrops{0},failures{0},lastCode{0},lastError{0},cleanupError{0},readError{0},readCode{0},policy{0},gate{0};
std::atomic<uint32_t> workerStackRtc{0},dmaLargest{0};
std::atomic<uint64_t> latest{0},oldest{0},readSeq{0},nextAttempt{0};
uint32_t bootId=0;uint64_t lastKick=0,lastSample=0,lastFlush=0,lastSlow=0,sampleInterval=60000;unsigned backoff=0;BootCapture bootCapture,engineBootCapture;size_t pendingConsume=0;
vprintf_like_t originalPrint=nullptr;
uint64_t nowMs(){return uint64_t(esp_timer_get_time())/1000;}
Record record(Type type,uint64_t sampled=UINT64_MAX){Record r;r.type=type;r.monotonicMs=sampled==UINT64_MAX?nowMs():sampled;const auto c=MosaicoClock::snapshot();r.epoch=c.valid?uint64_t(c.epoch):0;r.flags=c.valid?1:0;return r;}
bool enqueue(const Record& r,bool critical=false){if(!storage||queueLock.test_and_set(std::memory_order_acquire)){++drops;return false;}bool ok=storage->queue.push(r);queued=storage->queue.count;if(!ok)++drops;queueLock.clear(std::memory_order_release);if(critical)urgent=true;return ok;}
// Never inspect variadic arguments: they may contain credentials, tags or bodies.
// A global try guard handles recursion AND concurrent callers without blocking.
int logHook(const char* fmt,va_list ap){
 if(hookLock.test_and_set(std::memory_order_acquire)){++hookDrops;if(!originalPrint)return 0;va_list copy;va_copy(copy,ap);const int result=originalPrint(fmt,copy);va_end(copy);return result;}
 // S31 PSRAM-XIP literals live in ELF rodata at 0x50..., not hardware DROM.
 // A linker-defined static range rejects heap/stack formats without reading args.
 if(!xPortInIsrContext()&&spi_flash_cache_enabled()&&staticFormatAddress(reinterpret_cast<uintptr_t>(fmt),reinterpret_cast<uintptr_t>(_rodata_start),reinterpret_cast<uintptr_t>(_rodata_end))){
  unsigned severity=0;for(size_t i=0;fmt&&fmt[i]&&i<20;++i)if((fmt[i]=='W'||fmt[i]=='E')&&fmt[i+1]==' '&&fmt[i+2]=='('){severity=fmt[i]=='W'?1:2;break;}
  if(severity){Record r;r.type=Type::Warning;r.monotonicMs=nowMs();r.args[0]=severity;r.args[1]=hash(fmt);r.args[2]=0;r.args[3]=hookDrops.load();enqueue(r,false);}
 }
 int result=0;if(originalPrint){va_list copy;va_copy(copy,ap);result=originalPrint(fmt,copy);va_end(copy);}hookLock.clear(std::memory_order_release);return result;
}
struct StorageLease {bool held=MosaicoOta::tryStorageLease();~StorageLease(){if(held)MosaicoOta::releaseStorageLease();}explicit operator bool()const{return held;}};
bool otaIdle(){return !MosaicoOta::busy()&&!MosaicoOta::healthPending();}
bool admitted(){
 if(!otaIdle()){gate=1;return false;}
 const auto* running=esp_ota_get_running_partition();esp_ota_img_states_t state=ESP_OTA_IMG_UNDEFINED;
 if(!running||esp_ota_get_state_partition(running,&state)!=ESP_OK||state!=ESP_OTA_IMG_VALID){gate=2;return false;}
 uint8_t p=0;nvs_handle_t h;const auto e=nvs_open("gauge_trace",NVS_READONLY,&h);if(e==ESP_OK){nvs_get_u8(h,"nand_v1",&p);nvs_close(h);}policy=p;
 if(p!=2){gate=3;return false;}
 Hal::BatteryTelemetry t{};if(!GetHAL().gaugeTraceTrySample(t)||!powerGate(otaIdle(),true,true,t.valid,t.temperatureValid,t.voltageMv,t.temperatureDeciK)){gate=4;return false;}
 gate=0;return true;
}
void failed(const MosaicoNandStore::Result& r,uint64_t now){lastError=uint32_t(r.error!=ESP_OK?r.error:r.cleanupError);cleanupError=uint32_t(r.cleanupError);lastCode=uint32_t(r.logCode);++failures;const uint64_t delay=60000ULL<<(backoff<6?backoff++:6);nextAttempt=now+delay;}
bool gauge(const Hal::GaugeDiagnosticSnapshot& s){auto r=record(s.phase==mosaico_gauge_status::Explicit?Type::Gauge:Type::GaugeBoot,s.sampledUs/1000);r.args={s.validMask,uint32_t(s.phase)|(uint32_t(s.outcome)<<8)|(uint32_t(s.mode)<<16),s.rawSoc,s.remainingMah,s.fullMah,s.designMah,s.voltageMv,uint32_t(uint16_t(s.currentMa))|(uint32_t(uint16_t(s.averageCurrentMa))<<16),uint32_t(s.rawCoulombMah)|(uint32_t(s.cycleCount)<<16),uint32_t(s.operationStatus)|(uint32_t(s.batteryStatus)<<16),uint32_t(s.gaugingStatus)|(uint32_t(s.rawEcho16)<<16)};if(s.phase!=mosaico_gauge_status::Explicit){r.epoch=0;r.flags&=~uint16_t(1);}return enqueue(r,s.phase!=mosaico_gauge_status::Explicit);}
bool engine(const Hal::GaugeEngineSnapshot& s){
 auto r=record(Type::GaugeEngine,s.sampledUs/1000);
 r.args[0]=uint32_t(s.validMask)|(uint32_t(s.phase)<<16)|(uint32_t(s.outcome)<<24);
 for(size_t i=0;i<40;++i){const uint8_t byte=i<23?s.rawCedv[i]:s.rawSmooth[i-23];r.args[1+i/4]|=uint32_t(byte)<<((i%4)*8);}
 if(s.phase!=mosaico_gauge_status::Explicit){r.epoch=0;r.flags&=~uint16_t(1);}
 return enqueue(r,s.phase!=mosaico_gauge_status::Explicit);
}
void changed(Record& prior,const Record& next,bool critical=false){if(prior.args!=next.args){enqueue(next,critical);prior=next;}}
void collect(uint64_t now){
 if(!bootCapture.complete){Hal::GaugeDiagnosticSnapshot before{},after{};GetHAL().gaugeBootDiagnostic(before,after);const auto info=GetHAL().gaugeBootReloadInfo();bootCapture.update(before,after,info.attempted,[](const Hal::GaugeDiagnosticSnapshot& s){return gauge(s);},[](uint8_t outcome,bool attempted){auto r=record(Type::Outcome);r.args[0]=outcome;r.args[1]=attempted;return enqueue(r,true);});}
 if(!engineBootCapture.complete){Hal::GaugeEngineSnapshot before{},after{};GetHAL().gaugeBootEngine(before,after);engineBootCapture.update(before,after,false,[](const Hal::GaugeEngineSnapshot& s){return engine(s);},[](uint8_t,bool){return true;});}
 const auto p=GetNetworkQuota().powerStats();auto r=record(Type::Power);r.args={p.locked,p.wifiRunning,p.profile,p.phase,p.cpuMHz,p.cycles,uint32_t(p.offMs),uint32_t(p.offMs>>32),uint32_t(p.clockError),0,0};if(storage->power.args[0]!=r.args[0]||storage->power.args[1]!=r.args[1]||storage->power.args[2]!=r.args[2]||storage->power.args[3]!=r.args[3]||storage->power.args[4]!=r.args[4]||storage->power.args[5]!=r.args[5]||storage->power.args[8]!=r.args[8])changed(storage->power,r);
 if(MosaicoOta::copyUiSnapshot(storage->otaUi)){const auto& o=storage->otaUi;r=record(Type::Ota);r.args={uint32_t(o.stage),o.received,o.size,o.progressBasisPoints,o.revision,uint32_t(o.currentSlot),uint32_t(o.targetSlot),MosaicoOta::busy(),MosaicoOta::healthPending(),0,0};changed(storage->ota,r,storage->ota.args[0]!=r.args[0]);}
 r=record(Type::Network);r.args={GetNetworkQuota().accepted(),GetNetworkQuota().failures(),GetNetworkQuota().historyAccepted(),GetNetworkQuota().historyFailures(),GetNetworkQuota().wifiConnectAttempts(),GetNetworkQuota().wifiBudgetClosures(),0,0,0,0,0};changed(storage->network,r);
 const auto t=GetTailnetQuota().tcpConnectDiagnostics();r=record(Type::Tcp);r.args={t.request_id,t.stage,uint32_t(t.error),uint32_t(t.first_error),uint32_t(t.retry_error),t.wg_wait_ms,t.elapsed_ms,t.wg_ready_before,t.wg_ready_after,0,0};changed(storage->tcp,r);
 // Pending OTA means RAM only: no gauge/NAND/NVS work at all.
 const uint64_t interval=sampleInterval;
 if(otaIdle()&&(!lastSample||now-lastSample>=interval)){StorageLease lease;if(lease&&otaIdle()){Hal::GaugeDiagnosticSnapshot s{};GetHAL().gaugeDiagnosticSample(s);gauge(s);Hal::GaugeEngineSnapshot e{};GetHAL().gaugeEngineSample(e);engine(e);sampleInterval=(((s.validMask&mosaico_gauge_status::Soc)&&s.rawSoc<=15)||((s.validMask&mosaico_gauge_status::Voltage)&&s.voltageMv<=3500))?10000:60000;lastSample=now;}}
 if(!lastSlow||now-lastSlow>=60000){r=record(Type::Heap);r.args={uint32_t(heap_caps_get_free_size(MALLOC_CAP_INTERNAL|MALLOC_CAP_8BIT)),uint32_t(heap_caps_get_largest_free_block(MALLOC_CAP_INTERNAL|MALLOC_CAP_8BIT)),uint32_t(heap_caps_get_free_size(MALLOC_CAP_SPIRAM)),uint32_t(heap_caps_get_largest_free_block(MALLOC_CAP_SPIRAM)),uint32_t(heap_caps_get_minimum_free_size(MALLOC_CAP_INTERNAL|MALLOC_CAP_8BIT)),0,0,0,0,0,0};enqueue(r);r=record(Type::Loss);r.args[0]=drops;r.args[1]=hookDrops;r.args[2]=queued;r.args[3]=failures;enqueue(r);lastSlow=now;}
}
void processRead(uint64_t now){
 if(reader!=ReadStatus::Pending||now<nextAttempt)return;
 StorageLease lease;if(!lease){gate=5;nextAttempt=now+60000;return;}if(!admitted()||!admitted()){nextAttempt=now+60000;return;}auto result=MosaicoNandStore::logRead(readSeq,storage->readPage,true);readError=uint32_t(result.error!=ESP_OK?result.error:result.cleanupError);cleanupError=uint32_t(result.cleanupError);readCode=uint32_t(result.logCode);lastError=readError.load();lastCode=readCode.load();
 if(result.error==ESP_OK&&result.cleanupError==ESP_OK&&result.logCode==NL::Code::Ok)reader=ReadStatus::Ready;else{failed(result,now);reader=ReadStatus::Failed;}
}
bool consumeDurable(){if(!pendingConsume)return true;if(queueLock.test_and_set(std::memory_order_acquire))return false;storage->queue.consume(pendingConsume);queued=storage->queue.count;pendingConsume=0;queueLock.clear(std::memory_order_release);return true;}
void flush(uint64_t now){
 if(!consumeDurable())return;
 if(!queued||now<nextAttempt||(!urgent&&!flushRequested&&now-lastFlush<60000))return;
 // Load is logically read-only (ECC refresh remains possible); same safety gate.
 MosaicoNandStore::Result result;{StorageLease lease;if(!lease){gate=5;nextAttempt=now+60000;return;}if(!admitted()||!admitted()){nextAttempt=now+60000;return;}result=MosaicoNandStore::logLoad(storage->state,true);}
 if(result.error!=ESP_OK||result.cleanupError!=ESP_OK||(result.logCode!=NL::Code::Ok&&result.logCode!=NL::Code::Empty)){failed(result,now);return;}
 journalReady=true;latest=storage->state.latestSeq;oldest=storage->state.oldestSeq;
 storage->page={};size_t count=0;if(queueLock.test_and_set(std::memory_order_acquire))return;count=storage->queue.copy(storage->page.data()+NL::HeaderSize,NL::RecordsPerPage);queueLock.clear(std::memory_order_release);
 if(!count)return;
 NL::put(storage->page,16,bootId,4);NL::put(storage->page,20,count,2);
 {StorageLease lease;if(!lease){gate=5;nextAttempt=now+60000;return;}if(!admitted()||!admitted()){nextAttempt=now+60000;return;}result=MosaicoNandStore::logAppend(storage->page,storage->state,true);}
 if(result.error!=ESP_OK||result.cleanupError!=ESP_OK||result.logCode!=NL::Code::Ok){failed(result,now);return;}
 // Producer only appends; the immutable prefix remains valid while NAND runs.
 pendingConsume=count;
 journalReady=true;latest=storage->state.latestSeq;oldest=storage->state.oldestSeq;lastError=0;lastCode=uint32_t(result.logCode);backoff=0;nextAttempt=0;lastFlush=now;urgent=false;flushRequested=false;consumeDurable();
}
void worker(void*){unsigned marker=0;workerStackRtc=esp_ptr_in_rtc_dram_fast(&marker);for(;;){ulTaskNotifyTake(pdTRUE,portMAX_DELAY);const auto now=nowMs();dmaLargest=heap_caps_get_largest_free_block(MALLOC_CAP_INTERNAL|MALLOC_CAP_DMA|MALLOC_CAP_8BIT);if(!consumeDurable())continue;collect(now);processRead(now);flush(now);}}
}
void begin(uint32_t id){
 if(initialized.exchange(true))return;
 bootId=id;
 void* memory=heap_caps_calloc(1,sizeof(Storage),MALLOC_CAP_SPIRAM|MALLOC_CAP_8BIT);if(!memory){allocationError=true;return;}storage=new(memory) Storage;
 auto r=record(Type::Boot);const auto* app=esp_app_get_description();r.args[0]=id;r.args[1]=esp_reset_reason();r.args[2]=hash(app->version);for(unsigned i=0;i<8;++i){uint32_t v=0;for(unsigned j=0;j<4;++j)v|=uint32_t(app->app_elf_sha256[i*4+j])<<(j*8);r.args[i+3]=v;}enqueue(r,true);
#if CONFIG_IDF_TARGET_ESP32S31 && CONFIG_IDF_TARGET_ARCH_RISCV
 r=record(Type::Panic);if(MosaicoPanicNumeric(r.args.data(),r.args.size()))enqueue(r,true);
#endif
 originalPrint=esp_log_set_vprintf(logHook);
}
void service(){if(!storage)return;const auto now=nowMs();if(lastKick&&now-lastKick<1000)return;lastKick=now;auto task=workerHandle.load();if(!task){if(!otaIdle()){gate=1;return;}TaskHandle_t created=nullptr;if(xTaskCreateWithCaps(worker,"system_log",8192,nullptr,1,&created,MALLOC_CAP_RTCRAM|MALLOC_CAP_INTERNAL|MALLOC_CAP_8BIT)!=pdPASS){taskError=true;return;}workerHandle=created;task=created;taskError=false;}xTaskNotifyGive(task);}
size_t startupInternalReservation(){return workerHandle.load()&&workerStackRtc.load()?8192:0;}
bool requestFlush(){if(!storage)return false;nextAttempt=0;flushRequested=true;return true;}
void probe(){ESP_LOGW("SystemLog","operator fingerprint probe");}
bool requestRead(uint64_t seq){if(!storage||!seq||readLock.test_and_set(std::memory_order_acquire))return false;if((reader==ReadStatus::Pending||reader==ReadStatus::Ready)&&readSeq==seq){readLock.clear(std::memory_order_release);return true;}if(reader==ReadStatus::Pending){readLock.clear(std::memory_order_release);return false;}readSeq=seq;readError=0;readCode=0;nextAttempt=0;reader=ReadStatus::Pending;readLock.clear(std::memory_order_release);return true;}
ReadStatus readStatus(){return reader.load();}
bool readChunk(uint64_t seq,uint16_t index,uint8_t* out,size_t capacity,ChunkMetadata& m){
 m={};m.seq=seq;m.index=index;m.error=int32_t(readError.load());m.logCode=readCode.load();if(!out||!chunkBounds(index,capacity)||readLock.test_and_set(std::memory_order_acquire))return false;
 bool ok=reader==ReadStatus::Ready&&readSeq==seq;if(ok){std::memcpy(out,storage->readPage.data()+index*ChunkBytes,ChunkBytes);m.seq=seq;m.index=index;m.bytes=ChunkBytes;m.totalBytes=2048;m.chunkCrc=crc(out,ChunkBytes);m.pageCrc=uint32_t(NL::get(storage->readPage,2044,4));m.error=0;m.logCode=uint32_t(NL::Code::Ok);}readLock.clear(std::memory_order_release);return ok;
}
void status(char* out,size_t len){if(out&&len)std::snprintf(out,len,"ready=%u task_ready=%u alloc_err=%u task_err=%u queued=%lu dropped=%lu hook_dropped=%lu write_failures=%lu last_error=%ld cleanup_error=%ld log_code=%lu policy=%lu gate=%lu latest_seq=%llu oldest_seq=%llu read_state=%u retention_pages=8191 format_requested=0 full_text_captured=0 coverage=structured+fmt_hash stack_rtc=%u dma_max=%lu",unsigned(journalReady.load()),unsigned(workerHandle.load()!=nullptr),unsigned(allocationError.load()),unsigned(taskError.load()),(unsigned long)queued.load(),(unsigned long)drops.load(),(unsigned long)hookDrops.load(),(unsigned long)failures.load(),(long)int32_t(lastError.load()),(long)int32_t(cleanupError.load()),(unsigned long)lastCode.load(),(unsigned long)policy.load(),(unsigned long)gate.load(),(unsigned long long)latest.load(),(unsigned long long)oldest.load(),unsigned(reader.load()),unsigned(workerStackRtc.load()),(unsigned long)dmaLargest.load());}
}
#else
namespace MosaicoSystemLog {
void begin(uint32_t){} void service(){} size_t startupInternalReservation(){return 0;} bool requestFlush(){return false;} void probe(){} void status(char* out,size_t n){if(out&&n)out[0]=0;} bool requestRead(uint64_t){return false;} ReadStatus readStatus(){return ReadStatus::Idle;} bool readChunk(uint64_t,uint16_t,uint8_t*,size_t,ChunkMetadata&){return false;}
}
#endif





