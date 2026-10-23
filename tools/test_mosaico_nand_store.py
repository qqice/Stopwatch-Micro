"""Two-slot NAND transaction and pinned-driver compatibility checks; no hardware."""
from pathlib import Path
import hashlib
import importlib.util
import os
import shutil
import subprocess
import tempfile
import unittest
ROOT=Path(__file__).resolve().parents[1]
COMPONENT=ROOT/'.artifacts/private/mosaico/nand-dependencies/spi_nand_flash-1.4.1'
if not COMPONENT.exists():COMPONENT=ROOT/'managed_components/espressif__spi_nand_flash'
COMPILER=os.environ.get('CXX') or shutil.which('g++') or shutil.which('clang++') or 'C:/Espressif/tools/riscv32-esp-elf/esp-15.2.0_20251204/riscv32-esp-elf/bin/riscv32-esp-elf-g++.exe'
spec=importlib.util.spec_from_file_location('compat',ROOT/'tools/mosaico_nand_driver_compat.py');compat=importlib.util.module_from_spec(spec);spec.loader.exec_module(compat)

MODEL=r'''
#include "main/hal/mosaico/mosaico_nand_store_model.h"
using namespace MosaicoNandStore::Model;
struct FTL {
 std::array<Page,RequiredPages> durable{},current{};std::array<bool,RequiredPages> dirty{};
 int calls=0,failAt=0;bool failAfter=false;unsigned writes=0,syncs=0;
 constexpr FTL(){for(auto& page:durable)for(auto& v:page)v=255;current=durable;}
 constexpr bool before(){++calls;return calls!=failAt||failAfter;}
 constexpr bool after()const{return calls!=failAt;}
 constexpr bool read(uint32_t page,Page& output){if(!before())return false;output=current[page];return after();}
 constexpr bool write(uint32_t page,const Page& input){++writes;if(!before())return false;current[page]=input;dirty[page]=true;return after();}
 constexpr bool sync(){++syncs;if(!before())return false;for(unsigned i=0;i<RequiredPages;++i)if(dirty[i]){durable[i]=current[i];dirty[i]=false;}return after();}
 constexpr void powercut(){current=durable;dirty={};failAt=0;calls=0;}
};
constexpr Payload data(uint8_t seed){Payload p;for(size_t i=0;i<PayloadSize;++i)p[i]=uint8_t(seed+i*13);return p;}
constexpr bool roundtrip(){
 FTL f;Scratch s;Payload out=data(8);const Payload sentinel=out;
 if(load(f,s,out).code!=Code::NotFormatted||out!=sentinel||f.writes)return false;
 if(save(f,s,data(1)).code!=Code::NotFormatted||f.writes)return false;
 if(writeFormatMarkers(f,s).code!=Code::Ok)return false;f.powercut();
 if(load(f,s,out).code!=Code::Empty||out!=sentinel)return false;
 auto first=save(f,s,data(1));if(first.code!=Code::Ok||first.generation!=1||first.slot!=0)return false;
 const auto old0=f.durable[2],old1=f.durable[3],oldCommit=f.durable[4];
 auto second=save(f,s,data(2));if(second.code!=Code::Ok||second.generation!=2||second.slot!=1)return false;
 if(f.durable[2]!=old0||f.durable[3]!=old1||f.durable[4]!=oldCommit)return false;
 f.powercut();if(load(f,s,out).code!=Code::Ok||out!=data(2))return false;
 auto third=save(f,s,data(3));if(third.code!=Code::Ok||third.generation!=3||third.slot!=0)return false;
 f.powercut();return load(f,s,out).code==Code::Ok&&out==data(3);
}
constexpr bool crashMatrix(){
 FTL initial;Scratch s;if(writeFormatMarkers(initial,s).code!=Code::Ok||save(initial,s,data(1)).code!=Code::Ok)return false;
 FTL count=initial;count.calls=0;if(save(count,s,data(2)).code!=Code::Ok)return false;const int operations=count.calls;
 for(int point=1;point<=operations;++point)for(int after=0;after<2;++after){
  FTL f=initial;f.calls=0;f.failAt=point;f.failAfter=after;const auto write=save(f,s,data(2));
  if(write.code==Code::Ok)return false;
  // Active old slot untouched even if an acknowledgement is lost after sync.
  for(unsigned page=2;page<=4;++page)if(f.durable[page]!=initial.durable[page])return false;
  f.powercut();Payload recovered;auto read=load(f,s,recovered);
  if(read.code!=Code::Ok||(recovered!=data(1)&&recovered!=data(2)))return false;
 }
 return true;
}
constexpr bool corruption(){
 FTL f;Scratch s;Payload out=data(9);const auto sentinel=out;
 if(writeFormatMarkers(f,s).code!=Code::Ok||save(f,s,data(1)).code!=Code::Ok||save(f,s,data(2)).code!=Code::Ok)return false;
 f.durable[5][2]^=1;f.powercut();auto recovered=load(f,s,out);
 if(recovered.code!=Code::Ok||out!=data(1)||recovered.generation!=1)return false;
 f.durable[0][10]^=1;f.powercut();if(load(f,s,out).code!=Code::Ok)return false;
 f.durable[1][10]^=1;f.powercut();out=sentinel;
 const auto writes=f.writes;if(load(f,s,out).code!=Code::Corrupt||out!=sentinel||save(f,s,data(3)).code!=Code::Corrupt||f.writes!=writes)return false;
 // Valid unknown format version remains refused even after its outer CRC is repaired.
 marker(f.durable[0]);put(f.durable[0],4,2,2);put(f.durable[0],PageSize-4,crc(f.durable[0],PageSize-4),4);f.powercut();
 return load(f,s,out).code==Code::Corrupt;
}
constexpr bool saturation(){
 FTL f;Scratch s;if(writeFormatMarkers(f,s).code!=Code::Ok||save(f,s,data(1)).code!=Code::Ok)return false;
 commit(f.durable[4],0,UINT64_MAX,crc(data(1),PayloadSize));f.powercut();const auto before=f.durable;
 if(save(f,s,data(2)).code!=Code::GenerationExhausted||f.durable!=before)return false;
 // Two independently valid commits with equal generations are ambiguous.
 f.durable[5]=f.durable[2];f.durable[6]=f.durable[3];commit(f.durable[7],1,UINT64_MAX,crc(data(1),PayloadSize));f.powercut();
 Payload out;return load(f,s,out).code==Code::Corrupt;
}
static_assert(roundtrip(),"durable format duplicate markers rotating slots preserved source3108 after reboot");
static_assert(crashMatrix(),"every IO fault before/after side effect yields old or new committed record after powercut");
static_assert(corruption(),"CRC padding/version corruption fail closed single marker recovery no autoformat");
static_assert(saturation(),"no generation wrap or ambiguous equal-generation selection");
'''

def extract_function(source,signature):
    start=source.index(signature);opening=source.index('{',start);depth=1;end=opening+1
    while depth:
        depth+=(source[end]=='{')-(source[end]=='}');end+=1
    return source[start:end]

def compat_fixture(sources):
    import re
    wait=extract_function(sources['nand_impl.c'],'static esp_err_t wait_for_ready(').replace('static esp_err_t','constexpr esp_err_t',1)
    init=extract_function(sources['nand.c'],'esp_err_t spi_nand_flash_init_device(')
    init=re.sub(r'#ifdef CONFIG_NAND_FLASH_ENABLE_BDL.*?#else','',init,flags=re.S).replace('#endif // CONFIG_NAND_FLASH_ENABLE_BDL','')
    before,cleanup=init.split('\nfail:\n',1);cleanup=cleanup.rsplit('}',1)[0]
    init=(before+'\n}').replace('goto fail;', '{'+cleanup+'}').replace('esp_err_t spi_nand_flash_init_device(', 'constexpr esp_err_t spi_nand_flash_init_device(',1)
    dhara=extract_function(sources['dhara_glue.c'],'static esp_err_t dhara_init(')
    dhara=re.sub(r'#ifdef CONFIG_NAND_FLASH_ENABLE_BDL.*?#endif','',dhara,flags=re.S)
    dhara=dhara.replace('heap_caps_calloc(1, sizeof(spi_nand_flash_dhara_priv_data_t), MALLOC_CAP_DEFAULT)','allocate()').replace('static esp_err_t','constexpr esp_err_t',1)
    return r'''#include <cstdint>
#include <cstddef>
using esp_err_t=int;
constexpr int ESP_OK=0,ESP_FAIL=-1,ESP_ERR_INVALID_ARG=1,ESP_ERR_NO_MEM=2,ESP_ERR_TIMEOUT=3,ESP_ERR_FLASH_BASE=0x6000;
constexpr int ROM_WAIT_THRESHOLD_US=1000,STAT_BUSY=1,REG_STATUS=0xc0,DHARA_E_NONE=0;
using dhara_error_t=int;
#define ESP_LOGE(...) ((void)0)
#define ESP_RETURN_ON_ERROR(expr,...) do{const int e=(expr);if(e)return e;}while(0)
struct spi_nand_flash_device_t;
struct Ops {int(*init)(spi_nand_flash_device_t*,void*);};
struct spi_nand_flash_config_t {int gc_factor=0;};
struct Chip {int log2_page_size=11,log2_ppb=6,num_blocks=1024;};
struct spi_nand_flash_device_t {Ops* ops=nullptr;void* ops_priv_data=nullptr;int initError=0;Chip chip;uint8_t work_buffer[1]{};spi_nand_flash_config_t config;};
constexpr int initializeOps(spi_nand_flash_device_t* h,void*){return h->initError;}
struct spi_nand_flash_dhara_priv_data_t {spi_nand_flash_device_t* parent_handle=nullptr;Chip dhara_nand;int dhara_map=0;};
struct Fixture {
 int64_t now=0;unsigned yields=0,delays=0,reads=0;int readError=0;unsigned busy=0;bool stuck=false;
 int lowError=0,attachError=0;bool noInit=false;unsigned deinitCalls=0;Ops ops{initializeOps};spi_nand_flash_device_t dev;
 bool format=false,allocationFail=false;int resumeError=0,resumeCode=0;unsigned resumeCalls=0,frees=0,mapInits=0;spi_nand_flash_dhara_priv_data_t priv;
 constexpr int64_t esp_timer_get_time(){now+=1000;return now;}
 constexpr void esp_rom_delay_us(unsigned){++delays;}
 constexpr void vTaskDelay(int){++yields;}
 constexpr int spi_nand_read_register(spi_nand_flash_device_t*,int,uint8_t* out){++reads;if(readError)return readError;*out=(stuck||busy>0)?1:0;if(busy)--busy;return ESP_OK;}
 constexpr int nand_init_device(spi_nand_flash_config_t*,spi_nand_flash_device_t** out){if(lowError){*out=nullptr;return lowError;}*out=&dev;return ESP_OK;}
 constexpr int nand_wl_attach_ops(spi_nand_flash_device_t* h){if(attachError)return attachError;if(noInit)ops.init=nullptr;h->ops=&ops;return ESP_OK;}
 constexpr int spi_nand_flash_deinit_device(spi_nand_flash_device_t* h){++deinitCalls;h->ops=nullptr;h->ops_priv_data=nullptr;return ESP_OK;}
 constexpr spi_nand_flash_dhara_priv_data_t* allocate(){return allocationFail?nullptr:&priv;}
 constexpr void free(spi_nand_flash_dhara_priv_data_t*){++frees;}
 constexpr bool mosaico_nand_compat_format_authorized(){return format;}
 constexpr void dhara_map_init(int*,Chip*,uint8_t*,int){++mapInits;}
 constexpr int dhara_map_resume(int*,int* error){++resumeCalls;*error=resumeError;return resumeCode;}
''' + wait + '\n' + init + '\n' + dhara + r'''
};
constexpr bool waitBounds(){
 Fixture ready;ready.busy=5;if(ready.wait_for_ready(&ready.dev,25,nullptr)!=ESP_OK||ready.yields!=5||ready.reads!=6)return false;
 Fixture error;error.readError=0x1234;if(error.wait_for_ready(&error.dev,25,nullptr)!=0x1234||error.yields)return false;
 Fixture read;read.stuck=true;if(read.wait_for_ready(&read.dev,25,nullptr)!=ESP_ERR_TIMEOUT||read.now>201000||!read.yields)return false;
 Fixture erase;erase.stuck=true;return erase.wait_for_ready(&erase.dev,3200,nullptr)==ESP_ERR_TIMEOUT&&erase.now<=1001000&&erase.yields>0;
}
constexpr bool initCleanup(){
 for(unsigned kind=0;kind<5;++kind){
  Fixture f;if(kind==0)f.lowError=-10;else if(kind==1)f.attachError=-11;else if(kind==2)f.noInit=true;else if(kind==3)f.dev.initError=-12;
  spi_nand_flash_config_t config;spi_nand_flash_device_t* h=nullptr;
  const int error=f.spi_nand_flash_init_device(&config,&h);
  if(kind<4){if(error==ESP_OK||h!=nullptr||f.deinitCalls!=(kind==0?0:1))return false;}
  else if(error!=ESP_OK||h!=&f.dev||config.gc_factor!=45||f.deinitCalls)return false;
 }
 Fixture f;spi_nand_flash_config_t c;return f.spi_nand_flash_init_device(nullptr,nullptr)==ESP_ERR_INVALID_ARG&&f.spi_nand_flash_init_device(&c,nullptr)==ESP_ERR_INVALID_ARG;
}
constexpr bool resumeGates(){
 Fixture blank;blank.resumeCode=-1;blank.resumeError=3;
 if(blank.dhara_init(&blank.dev,nullptr)!=ESP_ERR_FLASH_BASE+3||blank.dev.ops_priv_data||blank.frees!=1||blank.resumeCalls!=1)return false;
 Fixture unknown;unknown.resumeCode=-1;if(unknown.dhara_init(&unknown.dev,nullptr)!=ESP_FAIL||unknown.dev.ops_priv_data||unknown.frees!=1)return false;
 Fixture oom;oom.allocationFail=true;if(oom.dhara_init(&oom.dev,nullptr)!=ESP_ERR_NO_MEM||oom.dev.ops_priv_data||oom.resumeCalls)return false;
 Fixture explicitFormat;explicitFormat.format=true;explicitFormat.resumeCode=-1;explicitFormat.resumeError=3;
 if(explicitFormat.dhara_init(&explicitFormat.dev,nullptr)!=ESP_OK||explicitFormat.resumeCalls||explicitFormat.dev.ops_priv_data!=&explicitFormat.priv)return false;
 Fixture existing;return existing.dhara_init(&existing.dev,nullptr)==ESP_OK&&existing.resumeCalls==1&&existing.frees==0;
}
static_assert(waitBounds(),"actual patched readiness source200ms read/program1000ms erase SDK errors yields");
static_assert(initCleanup(),"actual patched initializer source propagates all init errors cleanup NULL no doublefree");
static_assert(resumeGates(),"actual patched Dhara source OOM resume errors fail closed explicitformat bypass only");
'''

def ecc_fixture(sources):
    import re
    masks='\n'.join(line for line in (COMPONENT/'priv_include/spi_nand_oper.h').read_text().splitlines() if line.startswith('#define STAT_ECC'))
    packs='\n'.join(line for line in sources['nand_impl.c'].splitlines() if line.startswith('#define PACK_'))
    check=extract_function(sources['nand_impl.c'],'static bool is_ecc_error(').replace('static bool','constexpr bool',1)
    read=extract_function(sources['nand_impl.c'],'esp_err_t nand_read(').replace('esp_err_t nand_read(','constexpr esp_err_t nand_read(',1).replace('fail:','')
    public=extract_function(sources['nand.c'],'esp_err_t spi_nand_flash_read_page(').replace('esp_err_t spi_nand_flash_read_page(','constexpr esp_err_t spi_nand_flash_read_page(',1)
    threshold=extract_function((COMPONENT/'priv_include/nand.h').read_text(),'static inline bool nand_ecc_exceeds_data_refresh_threshold(').replace('static inline bool','constexpr bool',1)
    return r'''#include <cstdint>
#include <cstddef>
using esp_err_t=int;using nand_ecc_status_t=int;
constexpr int ESP_OK=0,ESP_FAIL=-1,ESP_ERR_NOT_SUPPORTED=1,portMAX_DELAY=-1;
constexpr int NAND_ECC_OK=0,NAND_ECC_1_TO_3_BITS_CORRECTED=1,NAND_ECC_NOT_CORRECTED=2,NAND_ECC_4_TO_6_BITS_CORRECTED=3,NAND_ECC_7_8_BITS_CORRECTED=5,NAND_ECC_MAX=6;
#define ESP_LOGE(...) ((void)0)
#define ESP_LOGD(...) ((void)0)
#define ESP_LOGV(...) ((void)0)
#define assert(...) ((void)0)
#define ESP_GOTO_ON_ERROR(expr,...) do{ret=(expr);if(ret)return ret;}while(0)
struct Fixture;struct spi_nand_flash_device_t;
constexpr int readOps(spi_nand_flash_device_t*,uint8_t*,uint32_t);
constexpr int writeOps(spi_nand_flash_device_t*,uint8_t*,uint32_t);
struct Ops {int(*read)(spi_nand_flash_device_t*,uint8_t*,uint32_t)=readOps;int(*write)(spi_nand_flash_device_t*,uint8_t*,uint32_t)=writeOps;};
struct Ecc {int ecc_status_reg_len_in_bits=2,ecc_corrected_bits_status=0,ecc_data_refresh_threshold=4;};
struct Chip {int num_blocks=1024,log2_ppb=6;Ecc ecc_data;};
struct spi_nand_flash_device_t {Fixture* owner=nullptr;Ops* ops=nullptr;Chip chip;int mutex=0;};
''' + masks + '\n' + packs + '\n' + threshold + r'''
struct Fixture {
 spi_nand_flash_device_t dev;Ops ops;uint8_t status=0;unsigned cacheReads=0,refreshWrites=0;
 constexpr Fixture(){dev.owner=this;dev.ops=&ops;}
 constexpr int read_page_and_wait(spi_nand_flash_device_t*,uint32_t,uint8_t* out){*out=status;return ESP_OK;}
 constexpr uint16_t get_column_address(spi_nand_flash_device_t*,uint32_t,size_t offset){return uint16_t(offset);}
 constexpr int spi_nand_read(spi_nand_flash_device_t*,uint8_t* out,uint16_t,size_t length){++cacheReads;for(size_t i=0;i<length;++i)out[i]=0x42;return ESP_OK;}
 constexpr void xSemaphoreTake(int,int){} constexpr void xSemaphoreGive(int){}
''' + check + '\n' + read + '\n' + public + r'''
};
constexpr int readOps(spi_nand_flash_device_t* h,uint8_t* out,uint32_t page){return h->owner->nand_read(h,page,0,1,out);}
constexpr int writeOps(spi_nand_flash_device_t* h,uint8_t*,uint32_t){++h->owner->refreshWrites;return ESP_OK;}
constexpr bool eccMasks(){
 for(unsigned value=0;value<4;++value){
  Fixture f;f.status=uint8_t(value<<4);uint8_t output=0x77;
  const int result=f.spi_nand_flash_read_page(&f.dev,&output,0);
  if(f.dev.chip.ecc_data.ecc_corrected_bits_status!=int(value))return false;
  if(value==2){if(result!=ESP_FAIL||f.cacheReads||f.refreshWrites||output!=0x77)return false;}
  else if(result!=ESP_OK||f.cacheReads!=1||f.refreshWrites!=(value==3?1:0)||output!=0x42)return false;
 }
 for(unsigned value=0;value<8;++value){
  Fixture f;f.dev.chip.ecc_data.ecc_status_reg_len_in_bits=3;
  const bool bad=f.is_ecc_error(&f.dev,uint8_t(value<<4));
  if(PACK_3BITS_STATUS(uint8_t(value<<4),STAT_ECC2,STAT_ECC1,STAT_ECC0)!=int(value)||f.dev.chip.ecc_data.ecc_corrected_bits_status!=int(value)||bad!=(value==2||value==6))return false;
 }
 return true;
}
static_assert(eccMasks(),"actual normalized masks C0=20 rejects before cache/no refresh;30 corrected refresh;3bit all combinations");
'''

ADAPTER_SYNTAX_PREFIX=r'''#include "main/hal/mosaico/mosaico_nand_store_model.h"
#include "main/hal/mosaico/mosaico_nand_log_model.h"
#include "main/hal/mosaico/mosaico_gauge_cold_store_model.h"
#include "main/hal/mosaico/mosaico_nand_dma_reserve_model.h"
#include <cstdio>
#include <atomic>
#include <mutex>
#include <new>
using esp_err_t=int;using gpio_num_t=int;using spi_device_handle_t=int*;
constexpr int ESP_OK=0,ESP_FAIL=-1,ESP_ERR_INVALID_ARG=1,ESP_ERR_NO_MEM=2,ESP_ERR_NOT_FOUND=3,ESP_ERR_INVALID_STATE=4,ESP_ERR_NOT_SUPPORTED=5,ESP_ERR_INVALID_CRC=6;
constexpr int GPIO_NUM_24=24,GPIO_NUM_25=25,GPIO_MODE_OUTPUT=1,GPIO_PULLUP_ENABLE=1,GPIO_PULLDOWN_DISABLE=0,GPIO_INTR_DISABLE=0;
constexpr int SPI3_HOST=3,SPI_DMA_CH_AUTO=3,SPI_DEVICE_HALFDUPLEX=1,SPI_NAND_IO_MODE_SIO=0;
constexpr int SPI_TRANS_VARIABLE_ADDR=1,SPI_TRANS_VARIABLE_CMD=2,SPI_TRANS_VARIABLE_DUMMY=4,SPI_TRANS_USE_RXDATA=8;
constexpr int MALLOC_CAP_SPIRAM=1,MALLOC_CAP_8BIT=2,MALLOC_CAP_INTERNAL=4,MALLOC_CAP_DMA=8;
namespace MosaicoOta {bool tryStorageLease();void releaseStorageLease();bool currentTaskOwnsStorageLease();bool busy();}
struct gpio_io_config_t {bool slp_sel=false;};
struct gpio_config_t {uint64_t pin_bit_mask=0;int mode=0,pull_up_en=0,pull_down_en=0,intr_type=0;};
struct spi_bus_config_t {int mosi_io_num=0,miso_io_num=0,sclk_io_num=0,quadwp_io_num=0,quadhd_io_num=0,max_transfer_sz=0;};
struct spi_device_interface_config_t {int clock_speed_hz=0,mode=0,spics_io_num=0,queue_size=0,flags=0;};
struct spi_transaction_t {int flags=0;size_t rxlength=0;unsigned cmd=0;uint64_t addr=0;uint8_t rx_data[4]{};};
struct spi_transaction_ext_t {spi_transaction_t base{};unsigned command_bits=0,address_bits=0,dummy_bits=0;};
struct spi_nand_flash_device_t {};
struct spi_nand_flash_config_t {spi_device_handle_t device_handle=nullptr;uint8_t gc_factor=0,io_mode=0,flags=0;};
int spi_nand_flash_read_page(spi_nand_flash_device_t*,uint8_t*,uint32_t);
int spi_nand_flash_write_page(spi_nand_flash_device_t*,const uint8_t*,uint32_t);
int spi_nand_flash_sync(spi_nand_flash_device_t*);
int spi_nand_flash_init_device(spi_nand_flash_config_t*,spi_nand_flash_device_t**);
int spi_nand_flash_deinit_device(spi_nand_flash_device_t*);
int spi_nand_flash_get_page_size(spi_nand_flash_device_t*,uint32_t*);
int spi_nand_flash_get_page_count(spi_nand_flash_device_t*,uint32_t*);
int spi_nand_flash_get_block_num(spi_nand_flash_device_t*,uint32_t*);
int spi_nand_erase_chip(spi_nand_flash_device_t*);
int gpio_set_level(int,int);int gpio_config(const gpio_config_t*);int gpio_reset_pin(int);
int gpio_get_io_config(int,gpio_io_config_t*);int gpio_sleep_sel_en(int);int gpio_sleep_sel_dis(int);
int spi_bus_initialize(int,const spi_bus_config_t*,int);
int spi_bus_add_device(int,const spi_device_interface_config_t*,spi_device_handle_t*);
int spi_bus_remove_device(spi_device_handle_t);int spi_bus_free(int);
int spi_device_polling_transmit(spi_device_handle_t,spi_transaction_t*);
void* heap_caps_calloc(size_t,size_t,int);void heap_caps_free(void*);
void* heap_caps_aligned_alloc(size_t,size_t,int);
bool esp_ptr_internal(const void*);bool esp_ptr_dma_capable(const void*);bool esp_ptr_byte_accessible(const void*);
'''

def adapter_fixture():
    import re
    header=re.sub(r'^#.*$', '',(ROOT/'main/hal/mosaico/mosaico_nand_store.h').read_text(encoding='utf-8-sig'),flags=re.M)
    source=(ROOT/'main/hal/mosaico/mosaico_nand_store.cpp').read_text(encoding='utf-8-sig')
    source=source.split('namespace MosaicoNandStore {\nnamespace {',1)[1].split('\n}\nbool prepareDmaBuffer(',1)[0]
    source=re.sub(r'struct DmaAllocator \{.*?\} dmaAllocator;', '',source,flags=re.S)
    source=source.replace('MosaicoNandDma::Reservation<> dmaReservation;', 'MockReservation dmaReservation;')
    source=re.sub(r'struct StorageLease \{.*?\n\};',r'''struct StorageLease {
    Fixture& owner;bool held,acquired;
    constexpr StorageLease(Fixture& f,bool inherit=false):owner(f),held(!f.leaseDenied&&(!inherit||f.leaseDepth>0)),acquired(held&&!inherit){if(!inherit)++owner.leaseAttempts;if(acquired)++owner.leaseDepth;}
    constexpr ~StorageLease(){if(acquired){--owner.leaseDepth;++owner.leaseReleases;}}
    constexpr explicit operator bool()const{return held;}
};''',source,flags=re.S)
    source=source.replace('StorageLease storageLease(inheritStorageLease);', 'StorageLease storageLease{*this,inheritStorageLease};')
    source=source.replace('namespace cold=mosaico_gauge_cold_store;', '')
    source=source.replace('std::mutex','MockMutex').replace('std::unique_lock<MockMutex>','MockLock').replace('std::try_to_lock','0')
    source=source.replace('std::atomic_bool','MockAtomic')
    # Typed constexpr allocation adapters preserve the production choice:
    # ordinary operations allocate only Workspace; cold scratch is separate.
    source=source.replace('void* memory=heap_caps_calloc(1,coldOperation?sizeof(ColdWorkspace):sizeof(Workspace),MALLOC_CAP_SPIRAM|MALLOC_CAP_8BIT);',
        'Workspace* workspace=coldOperation?nullptr:allocateWorkspace();ColdWorkspace* coldWorkspace=coldOperation?allocateColdWorkspace():nullptr;')
    source=source.replace('if(!memory)', 'if(!workspace&&!coldWorkspace)')
    source=source.replace('Workspace* workspace=coldOperation?nullptr:new(memory) Workspace;', '')
    source=source.replace('ColdWorkspace* coldWorkspace=coldOperation?new(memory) ColdWorkspace:nullptr;', '')
    source=source.replace('if(workspace)workspace->~Workspace();', 'if(workspace)releaseWorkspace(workspace);')
    source=source.replace('if(coldWorkspace)coldWorkspace->~ColdWorkspace();', 'if(coldWorkspace)releaseColdWorkspace(coldWorkspace);')
    source=source.replace('heap_caps_free(memory);', '')
    for signature in ('bool read(', 'bool write(', 'bool sync(', 'esp_err_t recordError(', 'esp_err_t releaseControl(', 'esp_err_t identity(', 'Result transaction('):source=source.replace(signature,'constexpr '+signature)
    for name in ('spi_nand_flash_read_page','spi_nand_flash_write_page','spi_nand_flash_sync'):
        source=source.replace('lastError='+name+'(', 'lastError=handle->owner->'+name+'(')
    mocks=r'''
 Workspace memory;ColdWorkspace coldMemory;
 int token=0;spi_nand_flash_device_t nandMemory;
 std::array<Model::Page,Model::RequiredPages> durable{},current{};
 bool allocationFail=false,occupied=false,busLive=false,deviceLive=false,geometryBad=false,unsafeErase=false;
 bool failRemove=false,failDeinit=false,failFree=false;
 bool leaseDenied=false;unsigned leaseAttempts=0,leaseDepth=0,leaseReleases=0;
 unsigned allocations=0,frees=0,inits=0,deinits=0,dmaBuffers=0,removes=0,busFrees=0,pinChanges=0,reads=0,writes=0,erases=0;
 int failInitAt=0,readError=0,eraseError=0;
 bool firstInitFormat=false,secondInitFormat=false;
 uint32_t sleepMask=0x02900000,originalSleepMask=0x02900000;
 unsigned sleepReads=0,sleepWrites=0,nandCommands=0;
 int failSleepReadAt=0,failSleepWriteAt=0;bool stickyDisable=false,stickyRestore=false,sleepUnsafe=false;
 constexpr int gpio_get_io_config(int pin,gpio_io_config_t* out){++sleepReads;if(sleepReads==unsigned(failSleepReadAt))return -81;out->slp_sel=(sleepMask&(1U<<pin))!=0;return ESP_OK;}
 constexpr int gpio_sleep_sel_dis(int pin){++sleepWrites;if(sleepWrites==unsigned(failSleepWriteAt))return -82;if(!stickyDisable&&!(stickyRestore&&sleepWrites>6))sleepMask&=~(1U<<pin);return ESP_OK;}
 constexpr int gpio_sleep_sel_en(int pin){++sleepWrites;if(sleepWrites==unsigned(failSleepWriteAt))return -82;if(!stickyRestore)sleepMask|=1U<<pin;return ESP_OK;}
 constexpr uint32_t mosaico_nand_compat_version(){return 0x01040104;}
 constexpr Workspace* allocateWorkspace(){++allocations;if(allocationFail)return nullptr;memory={};return &memory;}
 constexpr void releaseWorkspace(Workspace*){++frees;}
 constexpr ColdWorkspace* allocateColdWorkspace(){++allocations;if(allocationFail)return nullptr;coldMemory={};return &coldMemory;}
 constexpr void releaseColdWorkspace(ColdWorkspace*){++frees;}
 constexpr int gpio_set_level(int,int){++pinChanges;return ESP_OK;}
 constexpr int gpio_config(const gpio_config_t*){++pinChanges;return ESP_OK;}
 constexpr int gpio_reset_pin(int){++pinChanges;return ESP_OK;}
 constexpr int spi_bus_initialize(int,const spi_bus_config_t*,int){if(occupied)return ESP_ERR_INVALID_STATE;busLive=true;return ESP_OK;}
 constexpr int spi_bus_add_device(int,const spi_device_interface_config_t*,spi_device_handle_t* out){deviceLive=true;*out=&token;return ESP_OK;}
 constexpr int spi_bus_remove_device(spi_device_handle_t){++removes;if(failRemove)return -70;deviceLive=false;return ESP_OK;}
 constexpr int spi_bus_free(int){++busFrees;if(failFree)return -71;busLive=false;return ESP_OK;}
 constexpr int spi_device_polling_transmit(spi_device_handle_t,spi_transaction_t* t){
  ++nandCommands;if(sleepMask&0x03f00000)sleepUnsafe=true;
  if(t->cmd==0x9f){t->rx_data[0]=0xc8;t->rx_data[1]=0x91;}else t->rx_data[0]=t->addr==0xb0?0x10:0;
  return ESP_OK;
 }
 constexpr int spi_nand_flash_init_device(spi_nand_flash_config_t*,spi_nand_flash_device_t** out){
  ++inits;if(sleepMask&0x03f00000)sleepUnsafe=true;if(inits==1)firstInitFormat=formatSession.load(std::memory_order_acquire);else secondInitFormat=formatSession.load(std::memory_order_acquire);
  if(inits==unsigned(failInitAt)){*out=nullptr;dmaBuffers=0;return -42;}
  nandMemory.owner=this;*out=&nandMemory;dmaBuffers=3;current=durable;return ESP_OK;
 }
 constexpr int spi_nand_flash_deinit_device(spi_nand_flash_device_t*){++deinits;dmaBuffers=0;return failDeinit?-72:ESP_OK;}
 constexpr int spi_nand_flash_get_page_size(spi_nand_flash_device_t*,uint32_t* out){*out=geometryBad?4096:2048;return ESP_OK;}
 constexpr int spi_nand_flash_get_block_num(spi_nand_flash_device_t*,uint32_t* out){*out=1024;return ESP_OK;}
 constexpr int spi_nand_flash_get_page_count(spi_nand_flash_device_t*,uint32_t* out){*out=60000;return ESP_OK;}
 constexpr int spi_nand_flash_read_page(spi_nand_flash_device_t*,uint8_t* out,uint32_t page){++reads;if(readError)return readError;for(size_t i=0;i<Model::PageSize;++i)out[i]=current[page][i];return ESP_OK;}
 constexpr int spi_nand_flash_write_page(spi_nand_flash_device_t*,const uint8_t* in,uint32_t page){++writes;for(size_t i=0;i<Model::PageSize;++i)current[page][i]=in[i];return ESP_OK;}
 constexpr int spi_nand_flash_sync(spi_nand_flash_device_t*){durable=current;return ESP_OK;}
 constexpr int spi_nand_erase_chip(spi_nand_flash_device_t*){if(sleepMask&0x03f00000)sleepUnsafe=true;++erases;if(!formatSession.load(std::memory_order_acquire))unsafeErase=true;if(eraseError)return eraseError;for(auto& p:durable)for(auto& v:p)v=255;current=durable;return ESP_OK;}
 constexpr void seed(const Payload& payload){
  for(auto& p:durable)for(auto& v:p)v=255;
  Model::marker(durable[0]);Model::marker(durable[1]);
  durable[2]={};durable[3]={};for(size_t i=0;i<Model::PayloadSize;++i)durable[2+i/Model::PageSize][i%Model::PageSize]=payload[i];
  Model::commit(durable[4],0,1,Model::crc(payload,Model::PayloadSize));current=durable;
 }
'''
    source=re.sub(r'(enum class Operation \{[^}]+\};)',lambda match:match.group(1)+mocks,source,count=1)
    prefix=ADAPTER_SYNTAX_PREFIX.split('int spi_nand_flash_read_page(',1)[0]
    prefix=prefix.replace('struct spi_nand_flash_device_t {};','struct Fixture;struct spi_nand_flash_device_t {Fixture* owner=nullptr;};')
    prefix+=r'''
struct MockMutex {bool available=true;};
struct MockLock {bool held;constexpr MockLock(MockMutex& mutex,int):held(mutex.available){}constexpr explicit operator bool()const{return held;}};
struct MockAtomic {bool value;constexpr void store(bool v,std::memory_order){value=v;}constexpr bool load(std::memory_order)const{return value;}};
struct MockReservation {bool available=true;constexpr bool isReady()const{return available;}};
'''
    return prefix+header+r'''
using namespace MosaicoNandStore;
namespace cold=mosaico_gauge_cold_store;
struct Fixture {
'''+source+r'''
};
constexpr Payload pattern(uint8_t seed){Payload out;for(size_t i=0;i<out.size();++i)out[i]=uint8_t(seed+i*13);return out;}
constexpr bool clean(const Fixture& f){return !f.leaseDepth&&!f.dmaBuffers&&f.allocations==f.frees&&!f.formatSession.load(std::memory_order_acquire)&&f.progressCallback==nullptr&&f.progressContext==nullptr;}
constexpr bool adapterLifecycle(){
 Fixture f;const auto original=pattern(1);f.seed(original);Payload output=pattern(9);
 auto loaded=f.transaction(Fixture::Operation::Load,nullptr,&output,true,nullptr,nullptr);
 if(loaded.error||loaded.cleanupError||output!=original||!clean(f)||f.busLive||f.deviceLive||f.erases||f.sleepUnsafe||f.sleepMask!=f.originalSleepMask)return false;
 const auto next=pattern(2);auto written=f.transaction(Fixture::Operation::Save,&next,nullptr,true,nullptr,nullptr);
 if(written.error||written.cleanupError||written.record.generation!=2||!clean(f)||f.busLive||f.deviceLive||f.inits!=3||f.sleepUnsafe||f.sleepMask!=f.originalSleepMask)return false;
 Fixture format;format.seed(original);auto formatted=format.transaction(Fixture::Operation::Format,nullptr,nullptr,true,nullptr,nullptr);
 if(format.sleepUnsafe||format.sleepMask!=format.originalSleepMask)return false;
 if(formatted.error||formatted.cleanupError||!formatted.formatted||format.erases!=1||format.unsafeErase||!format.firstInitFormat||format.secondInitFormat||!clean(format)||format.busLive||format.deviceLive)return false;
 return true;
}
constexpr bool adapterFailures(){
 const auto original=pattern(1),sentinel=pattern(9);
 for(unsigned kind=0;kind<9;++kind){
  Fixture f;f.seed(original);Payload output=sentinel;
  if(kind==0)f.failInitAt=1;else if(kind==1)f.geometryBad=true;else if(kind==2)f.readError=-77;else if(kind==3)f.failRemove=true;else if(kind==4)f.failFree=true;else if(kind==5)f.failDeinit=true;else if(kind==6)f.occupied=true;else if(kind==7)f.allocationFail=true;else f.mutex.available=false;
  const auto result=f.transaction(Fixture::Operation::Load,nullptr,&output,true,nullptr,nullptr);
  if(result.error==ESP_OK&&result.cleanupError==ESP_OK)return false;
  if(output!=sentinel||f.dmaBuffers||f.formatSession.load(std::memory_order_acquire)||f.progressCallback||f.progressContext||f.erases)return false;
  if(kind!=7&&f.allocations!=f.frees)return false;
  if(kind==7&&(f.allocations!=1||f.frees))return false;
  if(kind<3&&(f.busLive||f.deviceLive||f.sleepUnsafe||f.sleepMask!=f.originalSleepMask))return false;
  if(kind>=3&&kind<=5){
   if(result.cleanupError==ESP_OK||!f.cleanupFailed)return false;
   const auto count=f.allocations;auto refused=f.transaction(Fixture::Operation::Load,nullptr,&output,true,nullptr,nullptr);
   if(refused.error!=ESP_ERR_INVALID_STATE||f.allocations!=count||output!=sentinel)return false;
  }
  if(kind==6&&(f.pinChanges||f.busFrees||f.removes||f.inits))return false;
 }
 Fixture reopen;reopen.seed(original);reopen.failInitAt=2;const auto next=pattern(2);
 auto failed=reopen.transaction(Fixture::Operation::Save,&next,nullptr,true,nullptr,nullptr);
 if(failed.error!=-42||failed.stage!=Stage::Reopen||reopen.inits!=2||reopen.deinits!=1||!clean(reopen)||reopen.busLive||reopen.deviceLive)return false;
 Fixture erased;erased.seed(original);erased.eraseError=-79;
 auto eraseFailed=erased.transaction(Fixture::Operation::Format,nullptr,nullptr,true,nullptr,nullptr);
 if(eraseFailed.error!=-79||eraseFailed.stage!=Stage::Erase||erased.writes||!clean(erased)||erased.busLive||erased.deviceLive)return false;
 Fixture denied;Payload output=sentinel;auto no=denied.transaction(Fixture::Operation::Format,nullptr,&output,false,nullptr,nullptr);
 return no.error==ESP_ERR_INVALID_STATE&&denied.allocations==0&&denied.erases==0&&output==sentinel;
}
constexpr bool sleepStateFaults(){
 const auto original=pattern(1),sentinel=pattern(9);
 // Snapshot fails before any selector is changed or NAND command is issued.
 for(int point=1;point<=6;++point){
  Fixture f;f.seed(original);f.failSleepReadAt=point;Payload out=sentinel;
  auto result=f.transaction(Fixture::Operation::Load,nullptr,&out,true,nullptr,nullptr);
  if(result.error!=-81||result.cleanupError||out!=sentinel||f.nandCommands||f.inits||f.sleepWrites||f.sleepReads!=unsigned(point)||f.sleepMask!=f.originalSleepMask||!clean(f))return false;
 }
 // Disable or its readback fails: restore every attempted pin, never start I/O.
 for(int point=1;point<=6;++point)for(int readback=0;readback<2;++readback){
  Fixture f;f.seed(original);Payload out=sentinel;
  if(readback)f.failSleepReadAt=6+point;else f.failSleepWriteAt=point;
  auto result=f.transaction(Fixture::Operation::Load,nullptr,&out,true,nullptr,nullptr);
  if(result.error!=(readback?-81:-82)||result.cleanupError||out!=sentinel||f.nandCommands||f.inits||f.sleepMask!=f.originalSleepMask||!clean(f))return false;
 }
 Fixture stuck;stuck.seed(original);stuck.stickyDisable=true;Payload out=sentinel;
 auto refusal=stuck.transaction(Fixture::Operation::Load,nullptr,&out,true,nullptr,nullptr);
 if(refusal.error!=ESP_ERR_INVALID_STATE||stuck.nandCommands||stuck.inits||out!=sentinel||stuck.sleepMask!=stuck.originalSleepMask||!clean(stuck))return false;
 // Restore failure/readback mismatch fences later calls and leaves output unchanged.
 for(int point=1;point<=6;++point)for(int readback=0;readback<2;++readback){
  Fixture f;f.seed(original);out=sentinel;
  if(readback)f.failSleepReadAt=12+point;else f.failSleepWriteAt=6+point;
  auto result=f.transaction(Fixture::Operation::Load,nullptr,&out,true,nullptr,nullptr);
  if(result.error||result.cleanupError!=(readback?-81:-82)||!f.cleanupFailed||out!=sentinel||f.sleepWrites!=12||!clean(f))return false;
  const unsigned calls=f.allocations;auto fenced=f.transaction(Fixture::Operation::Load,nullptr,&out,true,nullptr,nullptr);
  if(fenced.error!=ESP_ERR_INVALID_STATE||f.allocations!=calls)return false;
 }
 Fixture mismatch;mismatch.seed(original);mismatch.stickyRestore=true;out=sentinel;
 auto restore=mismatch.transaction(Fixture::Operation::Load,nullptr,&out,true,nullptr,nullptr);
 return restore.cleanupError==ESP_ERR_INVALID_STATE&&mismatch.cleanupFailed&&out==sentinel&&clean(mismatch);
}
static_assert(sleepStateFaults(),"all6 sleep selectors snapshots disables readbacks and restores checked before IO; all failure positions fence safely");
static_assert(adapterLifecycle(),"actual transaction flow load-save-freshresume-format scoped DMA+PSRAM lifetimes");
static_assert(adapterFailures(),"actual transaction SDKfaultmatrix init/reopen/remove/free/deinit/geometry/admission outputunchanged cleanupfence and format-reset");
constexpr bool newAdmission(){
 Fixture denied;denied.leaseDenied=true;Payload out=pattern(1);
 auto result=denied.transaction(Fixture::Operation::Load,nullptr,&out,true,nullptr,nullptr);
 if(result.error!=ESP_ERR_INVALID_STATE||denied.allocations||denied.nandCommands||denied.leaseAttempts!=1||denied.leaseReleases||denied.leaseDepth)return false;
 Fixture noReserve;noReserve.dmaReservation.available=false;
 result=noReserve.transaction(Fixture::Operation::Load,nullptr,&out,true,nullptr,nullptr);
 if(result.error!=ESP_ERR_NO_MEM||noReserve.allocations||noReserve.nandCommands||noReserve.leaseReleases!=1||noReserve.leaseDepth)return false;
 Fixture nested;nested.seed(out);nested.leaseDepth=1;
 result=nested.transaction(Fixture::Operation::Load,nullptr,&out,true,nullptr,nullptr);
 return result.error==ESP_OK&&nested.leaseDepth==1&&nested.leaseReleases==1;
}
static_assert(newAdmission(),"OTA lease denial and missing boot reservation perform zero hardware; same-task outer lease survives nested transaction");
'''

class StoreTests(unittest.TestCase):
    def compile(self,code,name):
        with tempfile.TemporaryDirectory() as td:
            path=Path(td)/(name+'.cpp');path.write_text(code)
            p=subprocess.run([COMPILER,'-std=c++20','-fconstexpr-ops-limit=500000000','-fsyntax-only','-I',str(ROOT),str(path)],capture_output=True,text=True)
            log=ROOT/'.artifacts/mosaico'/('nand-store-'+name+'.log');log.parent.mkdir(parents=True,exist_ok=True);log.write_text(p.stdout+p.stderr)
            self.assertEqual(p.returncode,0,str(log))
    def test_transaction_model(self):self.compile(MODEL,'model')
    def test_format_marker_faults(self):
        code=MODEL.split('static_assert(roundtrip()',1)[0]+r'''
constexpr bool formatFaults(){
 FTL count;Scratch scratch;if(writeFormatMarkers(count,scratch).code!=Code::Ok)return false;
 const int operations=count.calls;
 for(int point=1;point<=operations;++point)for(int after=0;after<2;++after){
  FTL f;f.failAt=point;f.failAfter=after;auto result=writeFormatMarkers(f,scratch);
  if(result.code==Code::Ok)return false;
  f.powercut();const unsigned writes=f.writes,syncs=f.syncs;
  auto state=scan(f,scratch);
  if(state.code!=Code::NotFormatted&&state.code!=Code::Empty)return false;
  if(f.writes!=writes||f.syncs!=syncs)return false;
 }
 return true;
}
static_assert(formatFaults(),"format marker faults before/after every IO are never autoformatted; durable duplicate metadata only");
'''
        self.compile(code,'format-faults')
    def test_compat_exact_source_and_rejection(self):
        if not (COMPONENT/'src/nand.c').exists():self.skipTest('exact1.4.1 optional private/managed component not present')
        with tempfile.TemporaryDirectory() as td:
            out=Path(td)/'generated';sources=compat.generate(COMPONENT,out)
            for name,digest in compat.HASHES.items():
                self.assertEqual(hashlib.sha256((COMPONENT/'src'/name).read_bytes()).hexdigest(),digest)
                self.assertEqual((out/name).read_text(),sources[name])
            with self.assertRaises(ValueError):compat.generate(COMPONENT,COMPONENT/'src'/'bad-output')
            invalid=Path(td)/'wrong';(invalid/'src').mkdir(parents=True)
            for name in compat.HASHES:(invalid/'src'/name).write_bytes((COMPONENT/'src'/name).read_bytes())
            with (invalid/'src'/'nand.c').open('ab') as f:f.write(b'\n')
            with self.assertRaises(ValueError):compat.generate(invalid,Path(td)/'must-not-exist')
            self.assertFalse((Path(td)/'must-not-exist').exists())
            self.assertIn('return ESP_ERR_TIMEOUT;',sources['nand_impl.c'])
            self.assertIn('vTaskDelay(1); // Yield',sources['nand_impl.c'])
            self.assertIn('mosaico_nand_compat_progress((uint32_t)i + 1',sources['nand_impl.c'])
            self.assertIn('if (!mosaico_nand_compat_format_authorized())',sources['dhara_glue.c'])
            self.assertNotIn('dhara_error_t ignored;',sources['dhara_glue.c'])
            self.assertIn('ret = (*handle)->ops->init(*handle, NULL);',sources['nand.c'])
            self.assertIn('spi_nand_flash_deinit_device(*handle);\n    *handle = NULL;',sources['nand.c'])
    def test_compat_patched_functions(self):
        if not (COMPONENT/'src/nand.c').exists():self.skipTest('exact1.4.1 optional private/managed component not present')
        with tempfile.TemporaryDirectory() as td:
            sources=compat.generate(COMPONENT,Path(td)/'generated')
            self.compile(compat_fixture(sources),'compat-functions')
    def test_ecc_actual_masks(self):
        if not (COMPONENT/'src/nand.c').exists():self.skipTest('exact1.4.1 optional private/managed component not present')
        with tempfile.TemporaryDirectory() as td:
            self.compile(ecc_fixture(compat.generate(COMPONENT,Path(td)/'generated')),'ecc-masks')
    def test_actual_adapter_execution(self):
        self.compile(adapter_fixture(),'adapter-execution')
    def test_actual_adapter_syntax(self):
        import re
        header=(ROOT/'main/hal/mosaico/mosaico_nand_store.h').read_text(encoding='utf-8-sig')
        body=(ROOT/'main/hal/mosaico/mosaico_nand_store.cpp').read_text(encoding='utf-8-sig')
        header=re.sub(r'^#.*$', '',header,flags=re.M)
        body=re.sub(r'^#.*$', '',body,flags=re.M)
        prefix=ADAPTER_SYNTAX_PREFIX
        self.compile(prefix+header+body,'adapter-syntax')
    def test_adapter_boundaries(self):
        cpp=(ROOT/'main/hal/mosaico/mosaico_nand_store.cpp').read_text()
        for forbidden in ('nvs_','esp_restart','esp_vfs_','littlefs','SPI_NAND_IO_MODE_QIO','nand_wrap_','xTaskCreate'):
            self.assertNotIn(forbidden,cpp)
        self.assertEqual(cpp.count('MALLOC_CAP_INTERNAL|MALLOC_CAP_8BIT|MALLOC_CAP_DMA'),1)
        self.assertIn('MALLOC_CAP_SPIRAM|MALLOC_CAP_8BIT',cpp)
        self.assertEqual(cpp.count('spi_nand_erase_chip(nand)'),1)
        self.assertIn('if(operation==Operation::Format)',cpp)
        self.assertIn('if(!authorized)',cpp)
        self.assertIn('formatSession.store(false',cpp)
        self.assertIn('Model::writeFormatMarkers',cpp)
        self.assertIn('spi_nand_flash_deinit_device(nand)',cpp)
        self.assertIn('Stage::Reopen',cpp)
        self.assertIn('if(device&&result.cleanupError==ESP_OK)',cpp)
        if not (COMPONENT/'src/nand_impl.c').exists():return
        source=(COMPONENT/'src/nand_impl.c').read_text()
        good=source.split('static esp_err_t nand_erase_good_block(',1)[1].split('esp_err_t nand_erase_chip(',1)[0]
        self.assertLess(good.index('nand_is_bad'),good.index('if (is_bad)'))
        self.assertLess(good.index('if (is_bad)'),good.index('nand_erase_block(handle, block)'))

if __name__=='__main__':unittest.main()
