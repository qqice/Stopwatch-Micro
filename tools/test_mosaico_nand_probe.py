"""Read-only NAND probe host mock/static checks; no SDK build or device access."""
from pathlib import Path
import os
import re
import shutil
import subprocess
import tempfile
import unittest
ROOT=Path(__file__).resolve().parents[1]
CPP=(ROOT/'main/hal/mosaico/mosaico_nand_probe.cpp').read_text()
HEADER=(ROOT/'main/hal/mosaico/mosaico_nand_probe.h').read_text()
COMPILER=os.environ.get('CXX') or shutil.which('g++') or shutil.which('clang++') or 'C:/Espressif/tools/riscv32-esp-elf/esp-15.2.0_20251204/riscv32-esp-elf/bin/riscv32-esp-elf-g++.exe'

def fixture():
    header=re.sub(r'^#.*$', '', HEADER, flags=re.M)
    source=re.sub(r'^#.*$', '', CPP, flags=re.M)
    source=source.split('namespace MosaicoNandProbe {\nnamespace {',1)[1]
    source=source.replace('}\nResult probe()', 'Result probe()').rsplit('}',1)[0]
    source=source.replace('std::mutex', 'MockMutex').replace('std::unique_lock<MockMutex>', 'MockLock').replace('std::try_to_lock','0')
    source=source.replace('constexpr gpio_num_t', 'static constexpr gpio_num_t').replace('constexpr uint8_t','static constexpr uint8_t')
    for sig in ('esp_err_t releaseControl(', 'esp_err_t read(', 'Result run(', 'Result probe(', 'Result readChunk('):source=source.replace(sig,'constexpr '+sig)
    return r'''
#include <cstdint>
#include <cstddef>
using esp_err_t=int;using gpio_num_t=int;using spi_device_handle_t=int*;
constexpr int ESP_OK=0,ESP_FAIL=-1,ESP_ERR_INVALID_ARG=1,ESP_ERR_INVALID_STATE=2,ESP_ERR_INVALID_RESPONSE=3,ESP_ERR_NOT_SUPPORTED=4,ESP_ERR_TIMEOUT=5,ESP_ERR_INVALID_CRC=6;
constexpr int GPIO_NUM_24=24,GPIO_NUM_25=25,GPIO_MODE_OUTPUT=1,GPIO_PULLUP_ENABLE=1,GPIO_PULLDOWN_DISABLE=0,GPIO_INTR_DISABLE=0;
constexpr int SPI3_HOST=3,SPI_DMA_DISABLED=0,SPI_DEVICE_HALFDUPLEX=1;
constexpr int SPI_TRANS_VARIABLE_ADDR=1,SPI_TRANS_VARIABLE_CMD=2,SPI_TRANS_VARIABLE_DUMMY=4,SPI_TRANS_USE_RXDATA=8;
struct gpio_config_t {uint64_t pin_bit_mask=0;int mode=0,pull_up_en=0,pull_down_en=0,intr_type=0;};
struct spi_bus_config_t {int mosi_io_num=0,miso_io_num=0,sclk_io_num=0,quadwp_io_num=0,quadhd_io_num=0,max_transfer_sz=0;};
struct spi_device_interface_config_t {int clock_speed_hz=0,mode=0,spics_io_num=0,queue_size=0,flags=0;};
struct spi_transaction_t {int flags=0;size_t rxlength=0;unsigned cmd=0;uint64_t addr=0;uint8_t rx_data[4]{};uint8_t* rx_buffer=nullptr;};
struct spi_transaction_ext_t {spi_transaction_t base{};unsigned command_bits=0,address_bits=0,dummy_bits=0;};
struct MockMutex {bool available=true;};
struct MockLock {bool held;constexpr MockLock(MockMutex& mutex,int):held(mutex.available){} constexpr explicit operator bool()const{return held;}};
''' + header + r'''
using namespace MosaicoNandProbe;
struct Fixture {
 int token=0,calls=0,failAt=0;bool occupied=false,owned=false,added=false,wrongConfig=false,wrongCommand=false;
 unsigned resets=0,removes=0,frees=0,commands=0,idReads=0,pages=0,cacheReads=0,statusReads=0;
 uint32_t resetMask=0,drivenMask=0,preloaded=0;
 uint8_t manufacturer=0xc8,deviceId=0x91,config=0x10,initialStatus=0,eccStatus=0;
 bool inconsistent=false,foreverBusy=false;unsigned busyReads=0;int64_t time=0;
 constexpr int step(){++calls;return calls==failAt?ESP_FAIL:ESP_OK;}
 constexpr int gpio_set_level(int pin,int level){const int e=step();if(!e){if(level!=1)wrongConfig=true;preloaded|=1U<<pin;}return e;}
 constexpr int gpio_config(const gpio_config_t* c){const int e=step();if(!e){if((preloaded&c->pin_bit_mask)!=c->pin_bit_mask)wrongConfig=true;drivenMask|=unsigned(c->pin_bit_mask);}return e;}
 constexpr int gpio_reset_pin(int pin){++resets;resetMask|=1U<<pin;return step();}
 constexpr int spi_bus_initialize(int host,const spi_bus_config_t* c,int dma){const int e=step();if(e)return e;if(occupied)return ESP_ERR_INVALID_STATE;
  if(host!=SPI3_HOST||dma!=0||c->mosi_io_num!=21||c->miso_io_num!=22||c->sclk_io_num!=20||c->quadwp_io_num!=-1||c->quadhd_io_num!=-1||c->max_transfer_sz!=64)wrongConfig=true;owned=true;return ESP_OK;}
 constexpr int spi_bus_add_device(int host,const spi_device_interface_config_t* c,spi_device_handle_t* out){const int e=step();if(e)return e;
  if(host!=SPI3_HOST||!owned||c->clock_speed_hz!=1000000||c->mode!=0||c->spics_io_num!=23||c->queue_size!=1||c->flags!=SPI_DEVICE_HALFDUPLEX)wrongConfig=true;
  added=true;*out=&token;return ESP_OK;}
 constexpr int spi_bus_remove_device(spi_device_handle_t){++removes;const int e=step();if(!e)added=false;return e;}
 constexpr int spi_bus_free(int){++frees;const int e=step();if(!e)owned=false;return e;}
 constexpr int64_t esp_timer_get_time(){time+=1000;return time;}
 constexpr int spi_device_polling_transmit(spi_device_handle_t,spi_transaction_t* t){
  ++commands;const int e=step();if(e)return e;
  if(!owned||!added)wrongConfig=true;
  if(t->cmd==0x9f && t->addr==0 && t->rxlength==16){
   ++idReads;t->rx_data[0]=manufacturer;t->rx_data[1]=deviceId;
   if(inconsistent&&idReads==2)t->rx_data[1]^=1;
  }else if(t->cmd==0x0f && t->rxlength==8){
   if(t->addr==0xa0)t->rx_data[0]=0x38;
   else if(t->addr==0xb0)t->rx_data[0]=config;
   else if(t->addr==0xc0){++statusReads;t->rx_data[0]=pages?(foreverBusy||busyReads-- >0?1:eccStatus):initialStatus;}
   else wrongCommand=true;
  }else if(t->cmd==0x13 && t->addr<65536 && !t->rxlength){++pages;}
  else if(t->cmd==0x03 && t->addr<2112 && t->rxlength>0 && t->rxlength<=512 && t->rx_buffer){
   ++cacheReads;for(size_t i=0;i<t->rxlength/8;++i)t->rx_buffer[i]=uint8_t(t->addr+i);
  }else wrongCommand=true;
  return ESP_OK;
 }
''' + source + r'''
};
constexpr bool rawProbe(){
 Fixture f;const auto r=f.probe();
 if(r.error||r.cleanupError||r.stage!=Stage::Complete||!r.consistent||!r.configRead||!r.exactModelVerified||r.id[0]!=0xc8||r.id[1]!=0x91||r.configB0!=0x10||r.protectionA0!=0x38)return false;
 if(f.wrongConfig||f.wrongCommand||f.owned||f.added||f.pages||f.cacheReads||f.commands!=5||f.resets!=6)return false;
 Fixture occupied;occupied.occupied=true;auto o=occupied.probe();if(o.stage!=Stage::Bus||o.error!=ESP_ERR_INVALID_STATE||occupied.drivenMask||occupied.resets||occupied.frees)return false;
 Fixture locked;locked.mutex.available=false;if(locked.probe().error!=ESP_ERR_INVALID_STATE||locked.calls)return false;
 Fixture unstable;unstable.inconsistent=true;auto u=unstable.probe();if(u.error!=ESP_ERR_INVALID_RESPONSE||u.consistent||unstable.commands!=2)return false;
 Fixture other;other.deviceId=0x81;auto x=other.probe();return x.error==ESP_ERR_NOT_SUPPORTED&&!x.exactModelVerified&&other.commands==2;
}
constexpr bool faults(){
 Fixture count;count.probe();const int total=count.calls;
 for(int point=1;point<=total;++point){
  Fixture f;f.failAt=point;const auto r=f.probe();
  if(r.error==ESP_OK && r.cleanupError==ESP_OK)return false;
  if(f.wrongCommand||f.pages||f.cacheReads)return false;
  if(point==1 && (f.frees||f.resets||f.removes||f.drivenMask))return false;
  if(r.cleanupError){const auto calls=f.calls;auto repeat=f.probe();if(repeat.error!=ESP_ERR_INVALID_STATE||f.calls!=calls)return false;}
  else if(f.owned||f.added)return false;
 }
 return true;
}
constexpr bool readGates(){
 uint8_t data[128]{};
 Fixture f;f.busyReads=2;auto r=f.readChunk(65535,0,data,128);
 if(r.error||r.cleanupError||r.bytesRead!=128||f.pages!=1||f.cacheReads!=2||f.wrongCommand||f.wrongConfig)return false;
 for(unsigned i=0;i<128;++i)if(data[i]!=uint8_t(i))return false;
 for(int gate=0;gate<5;++gate){
  Fixture bad;if(gate==0)bad.config=0;else if(gate==1)bad.config=0x50;else if(gate==2)bad.config=0x12;else if(gate==3)bad.initialStatus=1;else bad.deviceId=0x81;
  auto result=bad.readChunk(0,0,data,1);if(!result.error||bad.pages||bad.cacheReads||result.bytesRead)return false;
 }
 Fixture corrupt;corrupt.eccStatus=0x20;auto c=corrupt.readChunk(0,0,data,1);if(c.error!=ESP_ERR_INVALID_CRC||corrupt.cacheReads)return false;
 Fixture corrected;corrected.eccStatus=0x30;auto ok=corrected.readChunk(0,0,data,1);if(ok.error||ok.bytesRead!=1)return false;
 Fixture timeout;timeout.foreverBusy=true;auto t=timeout.readChunk(0,0,data,1);if(t.error!=ESP_ERR_TIMEOUT||timeout.cacheReads||timeout.statusReads>21||timeout.time>21000)return false;
 // Sweep SDK failures through page-read/cache transfers as well as cleanup.
 Fixture count;count.readChunk(0,0,data,128);const int total=count.calls;
 for(int point=1;point<=total;++point){
  Fixture failure;failure.failAt=point;auto result=failure.readChunk(0,0,data,128);
  if(result.error==ESP_OK&&result.cleanupError==ESP_OK)return false;
  if(failure.wrongCommand||failure.wrongConfig)return false;
  if(!result.cleanupError&&(failure.owned||failure.added))return false;
 }
 Fixture spare;auto firstSpare=spare.readChunk(65535,2048,data,64);
 if(firstSpare.error||firstSpare.cleanupError||firstSpare.bytesRead!=64||spare.cacheReads!=1)return false;
 for(unsigned i=0;i<64;++i)if(data[i]!=uint8_t(2048+i))return false;
 Fixture edge;auto lastSpare=edge.readChunk(65535,2111,data,1);
 if(lastSpare.error||lastSpare.cleanupError||lastSpare.bytesRead!=1||data[0]!=uint8_t(2111))return false;
 Fixture bounds;
 if(bounds.readChunk(0,2111,data,2).error!=ESP_ERR_INVALID_ARG||bounds.readChunk(0,2112,data,1).error!=ESP_ERR_INVALID_ARG||bounds.readChunk(0,2048,data,128).error!=ESP_ERR_INVALID_ARG||bounds.calls)return false;
 if(bounds.readChunk(65536,0,data,1).error!=ESP_ERR_INVALID_ARG||bounds.readChunk(0,2175,data,2).error!=ESP_ERR_INVALID_ARG||bounds.readChunk(0,0,data,129).error!=ESP_ERR_INVALID_ARG||bounds.readChunk(0,0,nullptr,1).error!=ESP_ERR_INVALID_ARG||bounds.readChunk(0,0,data,0).error!=ESP_ERR_INVALID_ARG||bounds.calls)return false;
 return true;
}
static_assert(rawProbe(),"actual probe: rawID consistency features exclusive SIO configuration no array cleanup");
static_assert(faults(),"every SDK fault stops and cleans only own resources cleanup failures fence later calls");
static_assert(readGates(),"exact-ID ECC OTP OIP bounds 20ms ECC-failure gates and 128byte FIFO-split spare read");
'''

class ProbeTests(unittest.TestCase):
    def test_whitelist_and_no_implicit_initialization(self):
        for forbidden in ('nvs_', 'spi_nand_flash_init','littlefs','esp_restart','xTaskCreate','SET_FEATURE','SPI_TRANS_USE_TXDATA'):
            self.assertNotIn(forbidden,CPP)
        self.assertIn('SPI_DMA_DISABLED',CPP)
        self.assertIn('std::try_to_lock',CPP)
        self.assertIn('command==ReadId',CPP)
        self.assertIn('command==GetFeature',CPP)
        self.assertIn('transaction.base.cmd=0x13',CPP)
        self.assertIn('transaction.base.cmd=0x03',CPP)
        self.assertIn('transaction.address_bits=24;transaction.dummy_bits=0',CPP)
        self.assertIn('transaction.address_bits=16;transaction.dummy_bits=8',CPP)
        self.assertNotIn('portMAX_DELAY',CPP)
        self.assertNotIn('malloc(',CPP)
        self.assertNotIn('printf(',CPP)
        self.assertIn('DS-GD5F1GM7xExxG Rev1.3',CPP)

    def test_actual_production_source_with_sdk_fault_mocks(self):
        with tempfile.TemporaryDirectory() as td:
            source=Path(td)/'nand-probe.cpp';source.write_text(fixture())
            result=subprocess.run([COMPILER,'-std=c++20','-fconstexpr-ops-limit=200000000','-fsyntax-only',str(source)],capture_output=True,text=True)
            log=ROOT/'.artifacts/mosaico/nand-probe-host-mock.log';log.parent.mkdir(parents=True,exist_ok=True);log.write_text(result.stdout+result.stderr)
            self.assertEqual(result.returncode,0,str(log))

if __name__=='__main__':unittest.main()
