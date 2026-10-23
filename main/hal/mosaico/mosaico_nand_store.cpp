/* SPDX-License-Identifier: MIT */
#include "mosaico_nand_store.h"
#include "mosaico_nand_dma_reserve_model.h"
#include "../../ota/mosaico_ota.h"
#include <spi_nand_flash.h>
#include <driver/gpio.h>
#include <driver/spi_master.h>
#include <esp_heap_caps.h>
#include <esp_memory_utils.h>
#include <cstdio>
#include <atomic>
#include <mutex>
#include <new>

#if defined(CONFIG_NAND_FLASH_ENABLE_BDL)
#error "MosaicoNandStore requires pinned1.4.1 legacy Dhara API: disable NAND_FLASH_ENABLE_BDL"
#endif
extern "C" uint32_t mosaico_nand_compat_version();
namespace MosaicoNandStore {
namespace {
std::mutex mutex;
MosaicoNandDma::Reservation<> dmaReservation;
struct DmaAllocator {
    uint8_t* allocate(size_t alignment,size_t size){
        return static_cast<uint8_t*>(heap_caps_aligned_alloc(alignment,size,MALLOC_CAP_INTERNAL|MALLOC_CAP_8BIT|MALLOC_CAP_DMA));
    }
    bool valid(const uint8_t* pointer,size_t size,size_t alignment){
        if(!pointer||!size||reinterpret_cast<uintptr_t>(pointer)%alignment)return false;
        // Check every byte, not just an address that might straddle heap regions.
        for(size_t i=0;i<size;++i)if(!esp_ptr_internal(pointer+i)||!esp_ptr_dma_capable(pointer+i)||!esp_ptr_byte_accessible(pointer+i))return false;
        return true;
    }
    void release(uint8_t* pointer){heap_caps_free(pointer);} // Unpublished prepare failure only.
} dmaAllocator;
struct StorageLease {
    bool held=false,acquired=false;
    explicit StorageLease(bool inherit=false){
        if(inherit)held=MosaicoOta::currentTaskOwnsStorageLease()&&!MosaicoOta::busy();
        else acquired=held=MosaicoOta::tryStorageLease();
    }
    ~StorageLease(){if(acquired)MosaicoOta::releaseStorageLease();}
    explicit operator bool()const{return held;}
};
bool cleanupFailed=false;
std::atomic_bool formatSession{false};
Progress progressCallback=nullptr;
void* progressContext=nullptr;
struct Workspace {Model::Scratch scratch{};Payload snapshot{};LogPage logPage{};LogState logState{};std::array<uint32_t,8> traceCrc{};};
namespace cold=mosaico_gauge_cold_store;
struct ColdWorkspace {cold::Scratch scratch{};ColdRecord expected{};std::array<uint32_t,18> protectedCrc{};bool publish=false;};
enum class Operation { Load, Save, Format, LogLoad, LogAppend, LogRead, ColdLoad, ColdSave };
struct IO {
    spi_nand_flash_device_t* handle;
    esp_err_t lastError=ESP_OK;
    bool read(uint32_t page,Model::Page& data){lastError=spi_nand_flash_read_page(handle,data.data(),page);return lastError==ESP_OK;}
    bool write(uint32_t page,const Model::Page& data){lastError=spi_nand_flash_write_page(handle,data.data(),page);return lastError==ESP_OK;}
    bool sync(){lastError=spi_nand_flash_sync(handle);return lastError==ESP_OK;}
};
// The pure store only sees its canonical pair8/9. Remap to the selected
// whitelist pair, and deny ALL other writes (including format markers).
bool coldPageBase(ColdArea area,uint32_t& base){
    if(area==ColdArea::Journal){base=8;return true;}
    if(area==ColdArea::Proof){base=12;return true;}
    return false;
}
struct ColdIO {
    IO& io;uint32_t base;
    bool read(uint32_t page,cold::Page& out){
        if(page<2)return io.read(page,out);
        if(page>=cold::PageBase&&page<cold::PageBase+2)return io.read(base+page-cold::PageBase,out);
        io.lastError=ESP_ERR_INVALID_ARG;return false;
    }
    bool write(uint32_t page,const cold::Page& in){
        if(page>=cold::PageBase&&page<cold::PageBase+2)return io.write(base+page-cold::PageBase,in);
        io.lastError=ESP_ERR_INVALID_ARG;return false;
    }
    bool sync(){return io.sync();}
};
bool sameColdRecord(const ColdRecord& a,const ColdRecord& b){
    return a.sequence==b.sequence&&a.phase==b.phase&&a.nonce==b.nonce&&a.binding==b.binding&&a.size==b.size&&a.payload==b.payload;
}
esp_err_t recordError(Model::Code code,esp_err_t ioError){
    switch(code){
    case Model::Code::Ok:return ESP_OK;
    case Model::Code::NotFormatted:case Model::Code::Empty:return ESP_ERR_NOT_FOUND;
    case Model::Code::Io:return ioError==ESP_OK?ESP_FAIL:ioError;
    case Model::Code::GenerationExhausted:return ESP_ERR_INVALID_STATE;
    default:return ESP_ERR_INVALID_CRC;
    }
}
esp_err_t releaseControl(gpio_num_t pin){
    esp_err_t error=gpio_set_level(pin,1);if(error!=ESP_OK)return error;
    gpio_config_t config{};config.pin_bit_mask=uint64_t(1)<<pin;config.mode=GPIO_MODE_OUTPUT;
    config.pull_up_en=GPIO_PULLUP_ENABLE;config.pull_down_en=GPIO_PULLDOWN_DISABLE;config.intr_type=GPIO_INTR_DISABLE;
    error=gpio_config(&config);if(error!=ESP_OK)return error;return gpio_set_level(pin,1);
}
esp_err_t identity(spi_device_handle_t device){
    // Check exact chip/config before the official initializer changes protection.
    uint8_t id[2]{},again[2]{},config=0,status=0;
    for(unsigned i=0;i<4;++i){
        spi_transaction_ext_t t{};t.base.flags=SPI_TRANS_VARIABLE_ADDR|SPI_TRANS_VARIABLE_CMD|SPI_TRANS_VARIABLE_DUMMY|SPI_TRANS_USE_RXDATA;
        t.command_bits=8;t.address_bits=8;t.dummy_bits=0;t.base.cmd=i<2?0x9f:0x0f;t.base.addr=i<2?0:i==2?0xb0:0xc0;
        t.base.rxlength=i<2?16:8;
        const esp_err_t error=spi_device_polling_transmit(device,&t.base);if(error!=ESP_OK)return error;
        if(i<2){auto* out=i?again:id;out[0]=t.base.rx_data[0];out[1]=t.base.rx_data[1];}
        else if(i==2)config=t.base.rx_data[0];else status=t.base.rx_data[0];
    }
    if(id[0]!=0xc8||id[1]!=0x91||id[0]!=again[0]||id[1]!=again[1])return ESP_ERR_NOT_SUPPORTED;
    if((config&0x10)==0||(config&0x66)!=0)return ESP_ERR_NOT_SUPPORTED;
    return status&1?ESP_ERR_INVALID_STATE:ESP_OK;
}
Result transaction(Operation operation,const Payload* input,Payload* output,bool authorized,Progress progress,void* context,LogPage* logPage=nullptr,LogState* logState=nullptr,uint64_t logSequence=0,ColdArea coldArea=ColdArea::Journal,const ColdRecord* coldInput=nullptr,ColdRecord* coldOutput=nullptr,const ColdPolicy* coldPolicy=nullptr,bool inheritStorageLease=false){
    const bool journal=operation==Operation::LogLoad||operation==Operation::LogAppend||operation==Operation::LogRead;
    const bool coldOperation=operation==Operation::ColdLoad||operation==Operation::ColdSave;
    uint32_t coldBase=0;
    Result result;
    if(coldOperation&&(!coldPageBase(coldArea,coldBase)||!coldPolicy||!coldPolicy->ready()||(operation==Operation::ColdSave&&!coldInput)||(operation==Operation::ColdLoad&&!coldOutput))){result.error=ESP_ERR_INVALID_ARG;result.coldCode=cold::Code::Rejected;return result;}
    if(!authorized){result.error=ESP_ERR_INVALID_STATE;return result;}
    StorageLease storageLease(inheritStorageLease);
    if(!storageLease){result.error=ESP_ERR_INVALID_STATE;return result;}
    if(mosaico_nand_compat_version()!=0x01040104U){result.error=ESP_ERR_NOT_SUPPORTED;return result;}
    std::unique_lock<std::mutex> lock(mutex,std::try_to_lock);
    if(!lock||cleanupFailed){result.error=ESP_ERR_INVALID_STATE;return result;}
    result.stage=Stage::Allocation;
    if(!dmaReservation.isReady()){result.error=ESP_ERR_NO_MEM;return result;}
    // Cold scratch is separately allocated only for cold calls; old load/log
    // transactions retain EXACTLY their previous workspace allocation size.
    void* memory=heap_caps_calloc(1,coldOperation?sizeof(ColdWorkspace):sizeof(Workspace),MALLOC_CAP_SPIRAM|MALLOC_CAP_8BIT);
    if(!memory){result.error=ESP_ERR_NO_MEM;return result;}
    Workspace* workspace=coldOperation?nullptr:new(memory) Workspace;
    ColdWorkspace* coldWorkspace=coldOperation?new(memory) ColdWorkspace:nullptr;
    if(input)workspace->snapshot=*input;
    if(logPage&&operation==Operation::LogAppend)workspace->logPage=*logPage;
    if(coldInput)coldWorkspace->expected=*coldInput;
    spi_device_handle_t device=nullptr;spi_nand_flash_device_t* nand=nullptr;
    bool busOwned=false;uint32_t touched=0,sleepBefore=0,sleepChanged=0;
    auto finish=[&](){
        formatSession.store(false,std::memory_order_release);progressCallback=nullptr;progressContext=nullptr;
        auto keep=[&](esp_err_t error){if(result.cleanupError==ESP_OK&&error!=ESP_OK)result.cleanupError=error;};
        if(nand){keep(spi_nand_flash_deinit_device(nand));nand=nullptr;}
        if(device&&result.cleanupError==ESP_OK)keep(spi_bus_remove_device(device));
        bool busReleased=false;
        if(busOwned&&result.cleanupError==ESP_OK){
            const esp_err_t error=spi_bus_free(SPI3_HOST);keep(error);busReleased=error==ESP_OK;
        }
        if(busReleased){
            for(int pin=20;pin<=25;++pin)if(touched&(1U<<pin))keep(gpio_reset_pin(static_cast<gpio_num_t>(pin)));
            // Restore every changed sleep selector even if one GPIO reset/restore
            // fails. Never restore sleep routing while an owned bus is retained.
            for(int pin=20;pin<=25;++pin)if(sleepChanged&(1U<<pin)){
                const auto gpio=static_cast<gpio_num_t>(pin);const bool selected=(sleepBefore&(1U<<pin))!=0;
                keep(selected?gpio_sleep_sel_en(gpio):gpio_sleep_sel_dis(gpio));
                gpio_io_config_t io{};const esp_err_t error=gpio_get_io_config(gpio,&io);keep(error);
                if(error==ESP_OK&&bool(io.slp_sel)!=selected)keep(ESP_ERR_INVALID_STATE);
            }
        }
        if(result.cleanupError!=ESP_OK)cleanupFailed=true;
        if(result.error==ESP_OK&&result.cleanupError==ESP_OK){
            if(output)*output=workspace->snapshot;
            if(logPage)*logPage=workspace->logPage;
            if(logState)*logState=workspace->logState;
            if(coldOutput&&coldWorkspace->publish)*coldOutput=coldWorkspace->expected;
        }
        if(workspace)workspace->~Workspace();
        if(coldWorkspace)coldWorkspace->~ColdWorkspace();
        heap_caps_free(memory);return result;
    };
    result.stage=Stage::Bus;
    spi_bus_config_t bus{};bus.mosi_io_num=21;bus.miso_io_num=22;bus.sclk_io_num=20;
    bus.quadwp_io_num=-1;bus.quadhd_io_num=-1;bus.max_transfer_sz=4096;
    result.error=spi_bus_initialize(SPI3_HOST,&bus,SPI_DMA_CH_AUTO);
    if(result.error!=ESP_OK)return finish();
    busOwned=true;touched=(1U<<20)|(1U<<21)|(1U<<22);
    result.stage=Stage::Pins;
    // SPI PM locks cover only individual transfers, not Dhara's yielding waits.
    // Snapshot all selectors first, then pin the active pad routing for the whole
    // scoped session so automatic light sleep cannot substitute ROM pad states.
    for(int pin=20;pin<=25;++pin){
        gpio_io_config_t io{};result.error=gpio_get_io_config(static_cast<gpio_num_t>(pin),&io);
        if(result.error!=ESP_OK)return finish();
        if(io.slp_sel)sleepBefore|=1U<<pin;
    }
    for(int pin=20;pin<=25;++pin){
        const auto gpio=static_cast<gpio_num_t>(pin);sleepChanged|=1U<<pin;
        result.error=gpio_sleep_sel_dis(gpio);if(result.error!=ESP_OK)return finish();
        gpio_io_config_t io{};result.error=gpio_get_io_config(gpio,&io);
        if(result.error!=ESP_OK)return finish();
        if(io.slp_sel){result.error=ESP_ERR_INVALID_STATE;return finish();}
    }
    touched|=1U<<24;result.error=releaseControl(GPIO_NUM_24);if(result.error!=ESP_OK)return finish();
    touched|=1U<<25;result.error=releaseControl(GPIO_NUM_25);if(result.error!=ESP_OK)return finish();
    result.stage=Stage::Device;
    spi_device_interface_config_t deviceConfig{};deviceConfig.clock_speed_hz=1000000;deviceConfig.mode=0;
    deviceConfig.spics_io_num=23;deviceConfig.queue_size=1;deviceConfig.flags=SPI_DEVICE_HALFDUPLEX;touched|=1U<<23;
    result.error=spi_bus_add_device(SPI3_HOST,&deviceConfig,&device);if(result.error!=ESP_OK)return finish();
    result.stage=Stage::Identity;result.error=identity(device);if(result.error!=ESP_OK)return finish();
    spi_nand_flash_config_t config{};config.device_handle=device;config.gc_factor=0;config.io_mode=SPI_NAND_IO_MODE_SIO;config.flags=SPI_DEVICE_HALFDUPLEX;
    result.stage=Stage::Init;
    formatSession.store(operation==Operation::Format,std::memory_order_release);
    result.error=spi_nand_flash_init_device(&config,&nand);
    if(result.error!=ESP_OK){nand=nullptr;return finish();} // Compat init fully frees/nulls on error.
    uint32_t pageSize=0,blocks=0;
    result.stage=Stage::Geometry;result.error=spi_nand_flash_get_page_size(nand,&pageSize);
    if(result.error==ESP_OK)result.error=spi_nand_flash_get_block_num(nand,&blocks);
    if(result.error==ESP_OK)result.error=spi_nand_flash_get_page_count(nand,&result.logicalPages);
    if(result.error!=ESP_OK)return finish();
    if(pageSize!=Model::PageSize||blocks!=1024||result.logicalPages<Model::RequiredPages){result.error=ESP_ERR_NOT_SUPPORTED;return finish();}
    IO io{nand};
    if(coldOperation){
        if(result.logicalPages<coldBase+2){result.error=ESP_ERR_NOT_SUPPORTED;return finish();}
        result.stage=Stage::Record;ColdIO mapped{io,coldBase};
        auto checkCold=[&](cold::Code code){
            result.coldCode=code;
            switch(code){
            case cold::Code::Ready:case cold::Code::Empty:result.error=ESP_OK;break;
            case cold::Code::NotFormatted:result.error=ESP_ERR_NOT_FOUND;break;
            case cold::Code::Io:result.error=io.lastError==ESP_OK?ESP_FAIL:io.lastError;break;
            case cold::Code::Rejected:result.error=ESP_ERR_INVALID_STATE;break;
            default:result.error=ESP_ERR_INVALID_CRC;break;
            }
        };
        if(operation==Operation::ColdSave){
            // Preserve all neighbouring protocol pages and both legacy trace/
            // log indexes. Data64+ cannot be written by the whitelist adapter.
            if(result.logicalPages<18){result.error=ESP_ERR_NOT_SUPPORTED;return finish();}
            for(unsigned i=0;i<18;++i)if(i<coldBase||i>=coldBase+2){
                if(!io.read(i,coldWorkspace->scratch.page)){result.error=io.lastError;return finish();}
                coldWorkspace->protectedCrc[i]=Model::crc(coldWorkspace->scratch.page,Model::PageSize);
            }
            checkCold(cold::save(mapped,coldWorkspace->scratch,coldWorkspace->expected,*coldPolicy,true));
            if(result.error!=ESP_OK)return finish();
            result.stage=Stage::Reopen;
            result.error=spi_nand_flash_deinit_device(nand);nand=nullptr;
            if(result.error!=ESP_OK){result.cleanupError=result.error;return finish();} // Unknown teardown: quarantine, no bus removal/retry.
            result.error=spi_nand_flash_init_device(&config,&nand);if(result.error!=ESP_OK){nand=nullptr;return finish();}
            io.handle=nand;
            const auto loaded=cold::load(mapped,coldWorkspace->scratch,*coldPolicy);checkCold(loaded.code);
            if(result.error!=ESP_OK)return finish();
            if(loaded.code!=cold::Code::Ready||loaded.selected<0||!sameColdRecord(coldWorkspace->expected,coldWorkspace->scratch.records[unsigned(loaded.selected)])){
                result.coldCode=cold::Code::Verify;result.error=ESP_ERR_INVALID_CRC;return finish();
            }
            for(unsigned i=0;i<18;++i)if(i<coldBase||i>=coldBase+2){
                if(!io.read(i,coldWorkspace->scratch.page)){result.error=io.lastError;return finish();}
                if(coldWorkspace->protectedCrc[i]!=Model::crc(coldWorkspace->scratch.page,Model::PageSize)){
                    result.coldCode=cold::Code::Verify;result.error=ESP_ERR_INVALID_CRC;return finish();
                }
            }
        }else{
            const auto loaded=cold::load(mapped,coldWorkspace->scratch,*coldPolicy);checkCold(loaded.code);
            if(result.error!=ESP_OK)return finish();
            if(loaded.code==cold::Code::Ready){
                coldWorkspace->expected=coldWorkspace->scratch.records[unsigned(loaded.selected)];coldWorkspace->publish=true;
            }
        }
        result.formatted=true;result.stage=Stage::Complete;return finish();
    }
    if(journal){
        if(result.logicalPages<LogModel::RequiredPages){result.error=ESP_ERR_NOT_SUPPORTED;return finish();}
        result.stage=Stage::Record;
        // Preserve a digest of EVERY old logical page, including inactive trace slot.
        for(unsigned i=0;i<8;++i){
            if(!io.read(i,workspace->scratch.page)){result.error=io.lastError;return finish();}
            workspace->traceCrc[i]=Model::crc(workspace->scratch.page,Model::PageSize);
        }
        auto checkLog=[&](LogModel::Code code){
            result.logCode=code;
            switch(code){
            case LogModel::Code::Ok:case LogModel::Code::Empty:result.error=ESP_OK;break;
            case LogModel::Code::NotFormatted:result.error=ESP_ERR_NOT_FOUND;break;
            case LogModel::Code::Io:result.error=io.lastError==ESP_OK?ESP_FAIL:io.lastError;break;
            case LogModel::Code::Overflow:result.error=ESP_ERR_INVALID_STATE;break;
            default:result.error=ESP_ERR_INVALID_CRC;break;
            }
        };
        checkLog(LogModel::load(io,workspace->scratch,workspace->logState));
        if(result.error!=ESP_OK)return finish();
        result.formatted=true;
        if(operation==Operation::LogAppend){
            checkLog(LogModel::append(io,workspace->scratch,workspace->logPage,workspace->logState));
        }else if(operation==Operation::LogRead){
            if(!logSequence||logSequence<workspace->logState.oldestSeq||logSequence>workspace->logState.latestSeq){result.error=ESP_ERR_NOT_FOUND;return finish();}
            checkLog(LogModel::read(io,workspace->scratch,logSequence,workspace->logPage));
        }
        if(result.error!=ESP_OK)return finish();
        if(operation==Operation::LogAppend){
            result.stage=Stage::Reopen;
            const auto expected=workspace->logState;
            result.error=spi_nand_flash_deinit_device(nand);nand=nullptr;if(result.error!=ESP_OK)return finish();
            result.error=spi_nand_flash_init_device(&config,&nand);if(result.error!=ESP_OK){nand=nullptr;return finish();}
            io.handle=nand;
            checkLog(LogModel::load(io,workspace->scratch,workspace->logState));
            if(result.error!=ESP_OK)return finish();
            if(expected.latestSeq!=workspace->logState.latestSeq||expected.oldestSeq!=workspace->logState.oldestSeq){result.error=ESP_ERR_INVALID_CRC;return finish();}
            workspace->logState.adopted=expected.adopted;
            checkLog(LogModel::read(io,workspace->scratch,expected.latestSeq,workspace->scratch.verify));
            if(result.error!=ESP_OK)return finish();
            if(workspace->scratch.verify!=workspace->logPage){result.error=ESP_ERR_INVALID_CRC;return finish();}
            for(unsigned i=0;i<8;++i){
                if(!io.read(i,workspace->scratch.page)){result.error=io.lastError;return finish();}
                if(workspace->traceCrc[i]!=Model::crc(workspace->scratch.page,Model::PageSize)){result.error=ESP_ERR_INVALID_CRC;return finish();}
            }
        }
        result.stage=Stage::Complete;return finish();
    }
    if(operation==Operation::Format){
        progressCallback=progress;progressContext=context;if(progress)progress(0,blocks,context);
        result.stage=Stage::Erase;result.error=spi_nand_erase_chip(nand);
        if(result.error!=ESP_OK)return finish();
        result.stage=Stage::Record;result.record=Model::writeFormatMarkers(io,workspace->scratch);
    }else{
        result.stage=Stage::Record;
        result.record=operation==Operation::Load?Model::load(io,workspace->scratch,workspace->snapshot):Model::save(io,workspace->scratch,workspace->snapshot);
    }
    result.formatted=result.record.markerCopies>0;
    result.error=recordError(result.record.code,io.lastError);if(result.error!=ESP_OK)return finish();
    if(operation!=Operation::Load){
        // Validate after fresh Dhara resume, not just a live in-RAM mapping.
        result.stage=Stage::Reopen;result.error=spi_nand_flash_deinit_device(nand);nand=nullptr;
        if(result.error!=ESP_OK)return finish();
        formatSession.store(false,std::memory_order_release);
        result.error=spi_nand_flash_init_device(&config,&nand);
        if(result.error!=ESP_OK){nand=nullptr;return finish();}
        io.handle=nand;
        if(operation==Operation::Format){
            result.record=Model::scan(io,workspace->scratch);
            if(result.record.code!=Model::Code::Empty||result.record.markerCopies!=2){result.error=result.record.code==Model::Code::Io?io.lastError:ESP_ERR_INVALID_CRC;return finish();}
        }else{
            const uint64_t expectedGeneration=result.record.generation;
            result.record=Model::load(io,workspace->scratch,workspace->scratch.payload);
            result.error=recordError(result.record.code,io.lastError);if(result.error!=ESP_OK)return finish();
            if(result.record.generation!=expectedGeneration||workspace->scratch.payload!=workspace->snapshot){result.error=ESP_ERR_INVALID_CRC;return finish();}
        }
        result.formatted=true;
    }
    result.stage=Stage::Complete;return finish();
}
}
bool prepareDmaBuffer(){return dmaReservation.prepare(dmaAllocator);}
size_t startupInternalReservation(){return dmaReservation.isReady()?MosaicoNandDma::Capacity:0;}
void dmaStatus(char* output,size_t length){
    if(!output||!length)return;
    std::snprintf(output,length,"prepared=%u ready=%u leased=%u faulted=%u capacity=%u alignment=%u error=%s",
        unsigned(dmaReservation.wasAttempted()),unsigned(dmaReservation.isReady()),
        unsigned(dmaReservation.isLeased()),unsigned(dmaReservation.isFaulted()),
        unsigned(MosaicoNandDma::Capacity),unsigned(MosaicoNandDma::Alignment),MosaicoNandDma::errorName(dmaReservation.error()));
}
Result load(Payload& output,bool authorized){return transaction(Operation::Load,nullptr,&output,authorized,nullptr,nullptr);}
Result save(const Payload& input,bool authorized){return transaction(Operation::Save,&input,nullptr,authorized,nullptr,nullptr);}
Result logLoad(LogState& output,bool authorized){return transaction(Operation::LogLoad,nullptr,nullptr,authorized,nullptr,nullptr,nullptr,&output);}
Result logAppend(LogPage& page,LogState& output,bool authorized){return transaction(Operation::LogAppend,nullptr,nullptr,authorized,nullptr,nullptr,&page,&output);}
Result logRead(uint64_t sequence,LogPage& output,bool authorized){return transaction(Operation::LogRead,nullptr,nullptr,authorized,nullptr,nullptr,&output,nullptr,sequence);}
Result coldLoad(ColdArea area,ColdRecord& output,const ColdPolicy& policy,bool authorized,bool inheritStorageLease){
    return transaction(Operation::ColdLoad,nullptr,nullptr,authorized,nullptr,nullptr,nullptr,nullptr,0,area,nullptr,&output,&policy,inheritStorageLease);
}
Result coldSave(ColdArea area,const ColdRecord& input,const ColdPolicy& policy,bool authorized,bool inheritStorageLease){
    return transaction(Operation::ColdSave,nullptr,nullptr,authorized,nullptr,nullptr,nullptr,nullptr,0,area,&input,nullptr,&policy,inheritStorageLease);
}
Result formatConfirm(bool authorized,Progress progress,void* context){return transaction(Operation::Format,nullptr,nullptr,authorized,progress,context);}
}
extern "C" void* mosaico_nand_dma_acquire(size_t size,size_t alignment){
    return MosaicoNandStore::dmaReservation.acquire(size,alignment,MosaicoNandStore::dmaAllocator);
}
extern "C" void mosaico_nand_dma_release(void* pointer){
    MosaicoNandStore::dmaReservation.release(pointer);
}
// The SHA-guarded generated1.4.1 compatibility TUs call these narrow hooks.
extern "C" bool mosaico_nand_compat_format_authorized(){return MosaicoNandStore::formatSession.load(std::memory_order_acquire);}
extern "C" void mosaico_nand_compat_progress(uint32_t done,uint32_t total){
    if(MosaicoNandStore::formatSession.load(std::memory_order_acquire)&&MosaicoNandStore::progressCallback)
        MosaicoNandStore::progressCallback(done,total,MosaicoNandStore::progressContext);
}
