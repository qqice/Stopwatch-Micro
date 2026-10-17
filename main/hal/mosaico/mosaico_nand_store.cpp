/* SPDX-License-Identifier: MIT */
#include "mosaico_nand_store.h"
#include <spi_nand_flash.h>
#include <driver/gpio.h>
#include <driver/spi_master.h>
#include <esp_heap_caps.h>
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
bool cleanupFailed=false;
std::atomic_bool formatSession{false};
Progress progressCallback=nullptr;
void* progressContext=nullptr;
struct Workspace {Model::Scratch scratch{};Payload snapshot{};};
enum class Operation { Load, Save, Format };
struct IO {
    spi_nand_flash_device_t* handle;
    esp_err_t lastError=ESP_OK;
    bool read(uint32_t page,Model::Page& data){lastError=spi_nand_flash_read_page(handle,data.data(),page);return lastError==ESP_OK;}
    bool write(uint32_t page,const Model::Page& data){lastError=spi_nand_flash_write_page(handle,data.data(),page);return lastError==ESP_OK;}
    bool sync(){lastError=spi_nand_flash_sync(handle);return lastError==ESP_OK;}
};
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
Result transaction(Operation operation,const Payload* input,Payload* output,bool authorized,Progress progress,void* context){
    Result result;
    if(!authorized){result.error=ESP_ERR_INVALID_STATE;return result;}
    if(mosaico_nand_compat_version()!=0x01040102U){result.error=ESP_ERR_NOT_SUPPORTED;return result;}
    std::unique_lock<std::mutex> lock(mutex,std::try_to_lock);
    if(!lock||cleanupFailed){result.error=ESP_ERR_INVALID_STATE;return result;}
    result.stage=Stage::Allocation;
    void* memory=heap_caps_calloc(1,sizeof(Workspace),MALLOC_CAP_SPIRAM|MALLOC_CAP_8BIT);
    if(!memory){result.error=ESP_ERR_NO_MEM;return result;}
    auto* workspace=new(memory) Workspace;
    if(input)workspace->snapshot=*input;
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
        if(output&&result.error==ESP_OK&&result.cleanupError==ESP_OK)*output=workspace->snapshot;
        workspace->~Workspace();heap_caps_free(memory);return result;
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
Result load(Payload& output,bool authorized){return transaction(Operation::Load,nullptr,&output,authorized,nullptr,nullptr);}
Result save(const Payload& input,bool authorized){return transaction(Operation::Save,&input,nullptr,authorized,nullptr,nullptr);}
Result formatConfirm(bool authorized,Progress progress,void* context){return transaction(Operation::Format,nullptr,nullptr,authorized,progress,context);}
}
// The SHA-guarded generated1.4.1 compatibility TUs call these narrow hooks.
extern "C" bool mosaico_nand_compat_format_authorized(){return MosaicoNandStore::formatSession.load(std::memory_order_acquire);}
extern "C" void mosaico_nand_compat_progress(uint32_t done,uint32_t total){
    if(MosaicoNandStore::formatSession.load(std::memory_order_acquire)&&MosaicoNandStore::progressCallback)
        MosaicoNandStore::progressCallback(done,total,MosaicoNandStore::progressContext);
}
