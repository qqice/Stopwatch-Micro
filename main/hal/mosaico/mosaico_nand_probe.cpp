/* SPDX-License-Identifier: MIT */
#include "mosaico_nand_probe.h"
#include <driver/gpio.h>
#include <driver/spi_master.h>
#include <mutex>
#include <esp_timer.h>

namespace MosaicoNandProbe {
namespace {
std::mutex mutex;
bool cleanupFailed=false;
constexpr gpio_num_t Hold=GPIO_NUM_24,Wp=GPIO_NUM_25;
// Board evidence: pinned esp-mosaico-claw/setup_nand_flash.c SIO bootstrap.
// Protocol: GigaDevice DS-GD5F1GM7xExxG Rev1.3 pp17,18,24,41,45,47.
// https://www.mouser.com/datasheet/2/870/DS_00860_GD5F1GM7RE_Rev1_3-3436091.pdf
// Exact UE device is C8:91. This family has no BUF/continuous-read feature bit.
constexpr uint8_t ReadId=0x9f,GetFeature=0x0f,ConfigAddress=0xb0;
esp_err_t releaseControl(gpio_num_t pin){
    esp_err_t error=gpio_set_level(pin,1); // Preload inactive level before enabling output.
    if(error!=ESP_OK)return error;
    gpio_config_t config{};config.pin_bit_mask=uint64_t(1)<<pin;
    config.mode=GPIO_MODE_OUTPUT;config.pull_up_en=GPIO_PULLUP_ENABLE;
    config.pull_down_en=GPIO_PULLDOWN_DISABLE;config.intr_type=GPIO_INTR_DISABLE;
    error=gpio_config(&config);if(error!=ESP_OK)return error;
    return gpio_set_level(pin,1);
}
esp_err_t read(spi_device_handle_t device,uint8_t command,uint8_t address,uint8_t* out,unsigned count){
    // Hard whitelist: never let a caller inject arbitrary NAND commands/addresses.
    if(!out || !((command==ReadId && address==0 && count==2) ||
                 (command==GetFeature && (address==0xa0 || address==ConfigAddress || address==0xc0) && count==1)))return ESP_ERR_INVALID_ARG;
    spi_transaction_ext_t transaction{};
    transaction.base.flags=SPI_TRANS_VARIABLE_ADDR|SPI_TRANS_VARIABLE_CMD|SPI_TRANS_VARIABLE_DUMMY|SPI_TRANS_USE_RXDATA;
    transaction.base.rxlength=count*8;transaction.base.cmd=command;transaction.base.addr=address;
    transaction.command_bits=8;transaction.address_bits=8;transaction.dummy_bits=0;
    // Private, newly owned bus; no queued work or other device can contend. Each
    // polling transfer clocks at most32 bits/32us, independent of chip readiness.
    const esp_err_t error=spi_device_polling_transmit(device,&transaction.base);
    if(error==ESP_OK)for(unsigned i=0;i<count;++i)out[i]=transaction.base.rx_data[i];
    return error;
}
Result run(uint32_t page,uint16_t column,uint8_t* output,size_t length){
    Result result;
    std::unique_lock<std::mutex> lock(mutex,std::try_to_lock);
    if(!lock || cleanupFailed){result.error=ESP_ERR_INVALID_STATE;return result;}
    spi_bus_config_t bus{};bus.mosi_io_num=21;bus.miso_io_num=22;bus.sclk_io_num=20;
    bus.quadwp_io_num=-1;bus.quadhd_io_num=-1;bus.max_transfer_sz=64;
    result.stage=Stage::Bus;
    result.error=spi_bus_initialize(SPI3_HOST,&bus,SPI_DMA_DISABLED);
    // Do not touch control pins or release a bus belonging to another owner.
    if(result.error!=ESP_OK)return result;
    spi_device_handle_t device=nullptr;
    uint32_t touched=(1U<<20)|(1U<<21)|(1U<<22);
    auto finish=[&](){
        if(device)result.cleanupError=spi_bus_remove_device(device);
        if(result.cleanupError==ESP_OK)result.cleanupError=spi_bus_free(SPI3_HOST);
        if(result.cleanupError==ESP_OK){
            // reset_pin disables input/output and enables pull-up; CS/HOLD/WP stay inactive.
            for(int pin=20;pin<=25;++pin){
                if(!(touched&(1U<<pin)))continue;
                const esp_err_t error=gpio_reset_pin(static_cast<gpio_num_t>(pin));
                if(result.cleanupError==ESP_OK && error!=ESP_OK)result.cleanupError=error;
            }
        }
        if(result.cleanupError!=ESP_OK)cleanupFailed=true;
        return result;
    };
    touched|=1U<<Hold;result.stage=Stage::Hold;result.error=releaseControl(Hold);if(result.error!=ESP_OK)return finish();
    touched|=1U<<Wp;result.stage=Stage::Wp;result.error=releaseControl(Wp);if(result.error!=ESP_OK)return finish();
    spi_device_interface_config_t config{};config.clock_speed_hz=1000000;config.mode=0;
    config.spics_io_num=23;config.queue_size=1;config.flags=SPI_DEVICE_HALFDUPLEX;
    touched|=1U<<23;result.stage=Stage::Device;result.error=spi_bus_add_device(SPI3_HOST,&config,&device);
    if(result.error!=ESP_OK)return finish();
    result.stage=Stage::Id;result.error=read(device,ReadId,0,result.id,2);
    if(result.error!=ESP_OK)return finish();
    result.stage=Stage::RepeatId;result.error=read(device,ReadId,0,result.repeatId,2);
    if(result.error!=ESP_OK)return finish();
    result.consistent=true;bool allZero=true,allOnes=true;
    for(unsigned i=0;i<2;++i){
        if(result.id[i]!=result.repeatId[i])result.consistent=false;
        if(result.id[i]!=0)allZero=false;
        if(result.id[i]!=255)allOnes=false;
    }
    result.stage=Stage::Identity;
    if(!result.consistent || allZero || allOnes){result.error=ESP_ERR_INVALID_RESPONSE;return finish();}
    result.exactModelVerified=result.id[0]==0xc8 && result.id[1]==0x91;
    if(!result.exactModelVerified){result.error=ESP_ERR_NOT_SUPPORTED;return finish();}
    result.stage=Stage::Config;result.error=read(device,GetFeature,0xa0,&result.protectionA0,1);
    if(result.error!=ESP_OK)return finish();
    result.error=read(device,GetFeature,ConfigAddress,&result.configB0,1);
    if(result.error!=ESP_OK)return finish();
    result.configRead=true;result.stage=Stage::Status;
    result.error=read(device,GetFeature,0xc0,&result.statusC0,1);
    if(result.error!=ESP_OK)return finish();
    if(result.statusC0&1){result.error=ESP_ERR_INVALID_STATE;return finish();}
    if(length){
        // Never change feature settings to make a read possible. Reject OTP,
        // disabled ECC and unexpected reserved feature bits instead.
        if((result.configB0&0x10)==0 || (result.configB0&0x66)!=0){
            result.stage=Stage::Config;result.error=ESP_ERR_NOT_SUPPORTED;return finish();
        }
        spi_transaction_ext_t transaction{};
        transaction.base.flags=SPI_TRANS_VARIABLE_ADDR|SPI_TRANS_VARIABLE_CMD|SPI_TRANS_VARIABLE_DUMMY;
        transaction.base.cmd=0x13;transaction.base.addr=page;
        transaction.command_bits=8;transaction.address_bits=24;transaction.dummy_bits=0;
        result.stage=Stage::Page;result.error=spi_device_polling_transmit(device,&transaction.base);
        if(result.error!=ESP_OK)return finish();
        result.stage=Stage::Wait;
        const int64_t started=esp_timer_get_time();
        bool idle=false;
        for(unsigned attempt=0;attempt<512;++attempt){
            if(esp_timer_get_time()-started>=20000)break;
            result.error=read(device,GetFeature,0xc0,&result.statusC0,1);
            if(result.error!=ESP_OK)return finish();
            if(!(result.statusC0&1)){idle=true;break;}
        }
        if(!idle){result.error=ESP_ERR_TIMEOUT;return finish();}
        result.stage=Stage::Ecc;
        if((result.statusC0&0x30)==0x20){result.error=ESP_ERR_INVALID_CRC;return finish();}
        result.stage=Stage::Cache;
        // No DMA: split into FIFO-sized transfers, with no page wrap.
        while(result.bytesRead<length){
            const size_t count=length-result.bytesRead>64?64:length-result.bytesRead;
            transaction={};transaction.base.flags=SPI_TRANS_VARIABLE_ADDR|SPI_TRANS_VARIABLE_CMD|SPI_TRANS_VARIABLE_DUMMY;
            transaction.base.cmd=0x03;transaction.base.addr=column+result.bytesRead;
            transaction.base.rxlength=count*8;transaction.base.rx_buffer=output+result.bytesRead;
            transaction.command_bits=8;transaction.address_bits=16;transaction.dummy_bits=8;
            result.error=spi_device_polling_transmit(device,&transaction.base);
            if(result.error!=ESP_OK)return finish();
            result.bytesRead+=count;
        }
    }
    result.stage=Stage::Complete;return finish();
}
}
Result probe(){return run(0,0,nullptr,0);}
Result readChunk(uint32_t page,uint16_t column,uint8_t* output,size_t length){
    // Conservative ECC-on user-area bound:2048 data+64 spare, excluding ECC parity.
    if(!output || !length || length>128 || page>=65536 || column>=2112 || length>2112-column){
        Result result;result.stage=Stage::Bounds;result.error=ESP_ERR_INVALID_ARG;return result;
    }
    return run(page,column,output,length);
}
}
