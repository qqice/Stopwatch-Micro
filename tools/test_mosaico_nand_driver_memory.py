"""Pinned NAND CPU-vs-DMA allocation and unchanged bounce-path host checks."""
from pathlib import Path
import hashlib
import importlib.util
import re
import subprocess
import tempfile
import unittest

ROOT=Path(__file__).resolve().parents[1]
spec=importlib.util.spec_from_file_location('trace',ROOT/'tools/test_mosaico_nand_store.py')
trace=importlib.util.module_from_spec(spec);spec.loader.exec_module(trace)
compat=trace.compat
# spi_nand_oper is not generated/modified; pin the audited bounce implementation.
OPER_SHA='19b6145d14a639ed2c41c1a17ad84f9d638587c4d1723c469f9f30ff72aed642'


def allocation_fixture(source):
    init=trace.extract_function(source,'esp_err_t nand_init_device(')
    init=init.replace('memcpy(&(*handle)->config, config, sizeof(spi_nand_flash_config_t));','(*handle)->config = *config;')
    init=init.replace('heap_caps_calloc(1, sizeof(spi_nand_flash_device_t), MALLOC_CAP_DEFAULT)','allocateDevice()')
    init=init.replace('heap_caps_aligned_alloc(', 'allocateBuffer(')
    before,cleanup=init.split('\nfail:\n',1);cleanup=cleanup.rsplit('}',1)[0]
    init=(before+'\n}').replace('goto fail;', '{'+cleanup+'}').replace('esp_err_t nand_init_device(', 'constexpr esp_err_t nand_init_device(',1)
    return r'''
#include <array>
#include <cstdint>
#include <cstddef>
using esp_err_t=int;
constexpr int ESP_OK=0,ESP_ERR_NO_MEM=1,ESP_ERR_INVALID_ARG=2;
constexpr int MALLOC_CAP_DMA=1,MALLOC_CAP_8BIT=2,MALLOC_CAP_SPIRAM=4;
constexpr int SPI_NAND_IO_MODE_QOUT=1,SPI_NAND_IO_MODE_QIO=2;
#define ESP_RETURN_ON_FALSE(expr,err,...) do{if(!(expr))return err;}while(0)
#define ESP_GOTO_ON_FALSE(expr,err,...) do{if(!(expr)){ret=err;return cleanup(handle,ret);}}while(0)
#define ESP_GOTO_ON_ERROR(expr,...) do{ret=(expr);if(ret)return cleanup(handle,ret);}while(0)
struct Ecc {unsigned ecc_status_reg_len_in_bits=0,ecc_data_refresh_threshold=0;};
struct Chip {Ecc ecc_data;unsigned log2_ppb=0,log2_page_size=0,num_planes=0,flags=0,page_size=0,block_size=0;bool has_quad_enable_bit=false;};
struct spi_nand_flash_config_t {void* device_handle=nullptr;unsigned io_mode=0;};
struct spi_nand_flash_device_t {spi_nand_flash_config_t config;Chip chip;uint8_t* work_buffer=nullptr;uint8_t* read_buffer=nullptr;uint8_t* temp_buffer=nullptr;int mutex=0;};
struct Fixture {
 spi_nand_flash_device_t device{};std::array<std::array<uint8_t,2080>,3> buffers{};
 unsigned allocations=0,freed=0,live=0;int failAt=0;bool deviceFailure=false,mutexFailure=false;
 std::array<size_t,3> caps{},sizes{},alignments{};
 constexpr spi_nand_flash_device_t* allocateDevice(){if(deviceFailure)return nullptr;++live;return &device;}
 constexpr uint8_t* allocateBuffer(size_t alignment,size_t size,int cap){const auto i=allocations++;caps[i]=cap;sizes[i]=size;alignments[i]=alignment;if(int(allocations)==failAt)return nullptr;++live;return buffers[i].data();}
 constexpr void free(uint8_t* p){if(p){++freed;--live;}}
 constexpr void free(spi_nand_flash_device_t* p){if(p){++freed;--live;}}
 constexpr int xSemaphoreCreateMutex(){if(mutexFailure)return 0;++live;return 1;}
 constexpr void vSemaphoreDelete(int){--live;}
 constexpr int detect_chip(spi_nand_flash_device_t*){return ESP_OK;}
 constexpr int unprotect_chip(spi_nand_flash_device_t*){return ESP_OK;}
 constexpr int enable_quad_io_mode(spi_nand_flash_device_t*){return ESP_OK;}
 constexpr size_t spi_nand_get_dma_alignment(){return 32;}
''' + 'constexpr int cleanup(spi_nand_flash_device_t** handle,int ret){'+cleanup+'}\n' + init + r'''
};
constexpr bool initRouting(){
 Fixture f;spi_nand_flash_config_t config{&f.device,0};spi_nand_flash_device_t* handle=nullptr;
 if(f.nand_init_device(&config,&handle)||handle!=&f.device||f.allocations!=3||f.live!=5)return false;
 return f.caps[0]==(MALLOC_CAP_SPIRAM|MALLOC_CAP_8BIT)&&f.caps[1]==f.caps[0]&&f.caps[2]==(MALLOC_CAP_DMA|MALLOC_CAP_8BIT)&&f.sizes[0]==2048&&f.sizes[1]==2048&&f.sizes[2]==2080&&f.alignments[0]==32&&f.alignments[1]==32&&f.alignments[2]==32;
}
constexpr bool allocationFailures(){
 for(int point=1;point<=3;++point){Fixture f;f.failAt=point;spi_nand_flash_config_t config{&f.device,0};spi_nand_flash_device_t* handle=&f.device;
  if(f.nand_init_device(&config,&handle)!=ESP_ERR_NO_MEM||handle||f.live||f.freed!=unsigned(point))return false;
 }
 Fixture mutex;mutex.mutexFailure=true;spi_nand_flash_config_t config{&mutex.device,0};spi_nand_flash_device_t* h=&mutex.device;
 if(mutex.nand_init_device(&config,&h)!=ESP_ERR_NO_MEM||h||mutex.live||mutex.freed!=4)return false;
 Fixture device;device.deviceFailure=true;config.device_handle=&device.device;h=&device.device;
 return device.nand_init_device(&config,&h)==ESP_ERR_NO_MEM&&h==nullptr&&device.live==0;
}
static_assert(initRouting(),"actual init allocates two PSRAM pages and exactly one aligned DMA bounce");
static_assert(allocationFailures(),"every buffer/mutex allocation failure frees prior allocations and nulls handle");
'''


def bounce_fixture(source):
    functions=[]
    for signature in ('static uint16_t check_length_alignment(', 'static bool spi_nand_buf_dma_aligned(', 'static void spi_nand_tx_prepare_write_buffers(', 'static void spi_nand_rx_prepare_read_buffers(', 'static esp_err_t spi_nand_fast_read(', 'esp_err_t spi_nand_program_load('):
        f=trace.extract_function(source,signature)
        f=re.sub(r'#if ESP_IDF_VERSION >= ESP_IDF_VERSION_VAL\(5, 2, 0\)\n|#endif[^\n]*','',f)
        f=f.replace('static ', 'constexpr ',1) if f.startswith('static ') else f.replace('esp_err_t ', 'constexpr esp_err_t ',1)
        f=f.replace('memcpy(', 'copyBytes(').replace('goto fail;', 'return ret;').replace('fail:', '')
        functions.append(f)
    return r'''
#include <array>
#include <cstdint>
#include <cstddef>
using esp_err_t=int;
constexpr int ESP_OK=0,SPI_DEVICE_HALFDUPLEX=1,SPI_NAND_IO_MODE_QOUT=1,SPI_NAND_IO_MODE_QIO=2;
constexpr int SPI_TRANS_DMA_BUFFER_ALIGN_MANUAL=4,SPI_TRANS_MODE_QIO=8;
constexpr int CMD_READ_FAST=11,CMD_PROGRAM_LOAD=12,CMD_PROGRAM_LOAD_X4=13;
#define assert(expr) ((void)0)
struct spi_nand_flash_device_t {struct {unsigned flags=SPI_DEVICE_HALFDUPLEX;unsigned io_mode=0;} config;uint8_t temp_buffer[2080]{};};
struct spi_nand_transaction_t {int command=0,address_bytes=0;unsigned address=0;uint16_t miso_len=0;uint8_t* miso_data=nullptr;unsigned dummy_bits=0;uint16_t mosi_len=0;const uint8_t* mosi_data=nullptr;unsigned flags=0;};
struct Fixture {
 spi_nand_flash_device_t dev;uint8_t media[2048]{};unsigned reads=0,writes=0;bool wrongDma=false;
 constexpr size_t spi_nand_get_dma_alignment(){return 32;}
 // Tested user buffers represent PSRAM: short-circuit rejects them before pointer alignment.
 constexpr bool esp_ptr_dma_capable(const void*){return false;}
 constexpr void copyBytes(uint8_t* dst,const uint8_t* src,size_t n){for(size_t i=0;i<n;++i)dst[i]=src[i];}
 constexpr int spi_nand_execute_transaction(spi_nand_flash_device_t* h,spi_nand_transaction_t* t){
  if(t->miso_len){++reads;if(t->miso_data!=h->temp_buffer||!(t->flags&SPI_TRANS_DMA_BUFFER_ALIGN_MANUAL))wrongDma=true;for(unsigned i=0;i<t->miso_len;++i)t->miso_data[i]=media[i%2048];}
  if(t->mosi_len){++writes;if(t->mosi_data!=h->temp_buffer||!(t->flags&SPI_TRANS_DMA_BUFFER_ALIGN_MANUAL))wrongDma=true;for(unsigned i=0;i<t->mosi_len;++i)media[i]=t->mosi_data[i];}
  return ESP_OK;
 }
'''+'\n'.join(functions)+r'''
};
constexpr bool psramBounce(){
 Fixture f;std::array<uint8_t,2048> psram{};
 for(size_t i=0;i<psram.size();++i)psram[i]=uint8_t(i*13+7);
 if(f.spi_nand_program_load(&f.dev,psram.data(),0,2048)||f.writes!=1||f.wrongDma)return false;
 psram={};if(f.spi_nand_fast_read(&f.dev,psram.data(),0,2048)||f.reads!=1||f.wrongDma)return false;
 for(size_t i=0;i<psram.size();++i)if(psram[i]!=uint8_t(i*13+7))return false;
 // Copy scratch follows the same read-to-CPU then program-from-CPU route.
 std::array<uint8_t,2048> copy{};if(f.spi_nand_fast_read(&f.dev,copy.data(),0,2048)||f.spi_nand_program_load(&f.dev,copy.data(),0,2048)||f.wrongDma)return false;
 if(copy!=psram)return false;
 uint8_t markers[4]{};uint8_t* data=nullptr;uint16_t length=0;
 f.spi_nand_rx_prepare_read_buffers(&f.dev,markers,4,&data,&length);
 if(data!=f.dev.temp_buffer||length!=32)return false;
 const uint8_t* tx=nullptr;bool manual=true;
 f.spi_nand_tx_prepare_write_buffers(&f.dev,markers,4,&tx,&manual);
 return tx==markers&&!manual; // Nonaligned length remains upstream SPI-driver handled.
}
static_assert(psramBounce(),"actual prepare/fast-read/program-load bounce CPU pages and copy scratch through retained DMA temp");
'''


class MemoryTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if not (trace.COMPONENT/'src/nand_impl.c').exists():raise unittest.SkipTest('pinned component absent')
        cls.upstream={name:(trace.COMPONENT/'src'/name).read_text(encoding='utf-8') for name in compat.HASHES}
        cls.generated={name:compat.patched(name,source) for name,source in cls.upstream.items()}
        cls.oper=(trace.COMPONENT/'src/spi_nand_oper.c').read_text(encoding='utf-8')
    def compile(self,source,name):
        with tempfile.TemporaryDirectory() as td:
            path=Path(td)/'test.cpp';path.write_text(source)
            result=subprocess.run([trace.COMPILER,'-std=c++20','-fconstexpr-ops-limit=500000000','-fsyntax-only','-I',str(ROOT),str(path)],capture_output=True,text=True)
            log=ROOT/'.artifacts/mosaico'/('nand-memory-'+name+'.log');log.parent.mkdir(parents=True,exist_ok=True);log.write_text(result.stdout+result.stderr)
            self.assertEqual(result.returncode,0,str(log))
    def test_dependency_guard_and_sources_unchanged(self):
        original={name:(trace.COMPONENT/'src'/name).read_bytes() for name in (*compat.HASHES,*compat.DEPENDENCY_HASHES)}
        with tempfile.TemporaryDirectory() as td:
            base=Path(td);output=base/'generated'
            generated=compat.generate(trace.COMPONENT,output)
            self.assertEqual(set(generated),set(compat.HASHES))
            self.assertFalse((output/'spi_nand_oper.c').exists())
            fake=base/'modified';(fake/'src').mkdir(parents=True)
            for name,raw in original.items():(fake/'src'/name).write_bytes(raw)
            with (fake/'src/spi_nand_oper.c').open('ab') as f:f.write(b'\n')
            with self.assertRaisesRegex(ValueError,'bounce dependency'):
                compat.generate(fake,base/'rejected')
            self.assertFalse((base/'rejected').exists())
        for name,raw in original.items():self.assertEqual((trace.COMPONENT/'src'/name).read_bytes(),raw)
    def test_exact_allocation_routing(self):
        src=self.generated['nand_impl.c']
        self.assertEqual(src.count('MALLOC_CAP_SPIRAM | MALLOC_CAP_8BIT'),5)
        self.assertEqual(src.count('MALLOC_CAP_DMA | MALLOC_CAP_8BIT'),1)
        dma='(*handle)->temp_buffer = heap_caps_aligned_alloc(dma_alignment, (*handle)->chip.page_size + dma_alignment, MALLOC_CAP_DMA | MALLOC_CAP_8BIT);'
        self.assertIn(dma,src);self.assertIn(dma,self.upstream['nand_impl.c'])
        original=trace.extract_function(self.upstream['nand_impl.c'],'esp_err_t nand_init_device(')
        patched=trace.extract_function(src,'esp_err_t nand_init_device(')
        cleanup=original.split('fail:\n',1)[1].replace('    free(*handle);','    free(*handle);\n    *handle = NULL;')
        self.assertEqual(patched.split('fail:\n',1)[1],cleanup)
        for name in ('nand_copy','s_verify_write'):
            signature=('esp_err_t ' if name=='nand_copy' else 'static esp_err_t ')+name+'('
            old=trace.extract_function(self.upstream['nand_impl.c'],signature)
            new=trace.extract_function(src,signature)
            self.assertEqual(new,old.replace('MALLOC_CAP_DMA','MALLOC_CAP_SPIRAM'))
        self.assertIn('0x01040103U',self.generated['nand.c'])
        config=(ROOT/'.artifacts/mosaico/nand-storage-sdkconfig').read_text(encoding='utf-8')
        self.assertIn('# CONFIG_NAND_FLASH_VERIFY_WRITE is not set',config)
    def test_bounce_call_paths_pinned(self):
        raw=(trace.COMPONENT/'src/spi_nand_oper.c').read_bytes()
        self.assertEqual(hashlib.sha256(raw).hexdigest(),OPER_SHA)
        impl=self.generated['nand_impl.c'];dhara=self.generated['dhara_glue.c'];oper=self.oper
        paths={
          'esp_err_t nand_read(':('spi_nand_read(handle, data,',),
          'esp_err_t nand_prog(':('spi_nand_program_load(handle, data,',),
          'esp_err_t nand_copy(':('spi_nand_read(handle, copy_buf,','spi_nand_program_load(handle, copy_buf,','spi_nand_read(handle, temp_buf,'),
          'static esp_err_t s_verify_write(':('spi_nand_read(handle, temp_buf,',),
        }
        for signature,anchors in paths.items():
            body=trace.extract_function(impl,signature)
            for anchor in anchors:self.assertIn(anchor,body)
        self.assertIn('dhara_map_init(&dhara_priv_data->dhara_map, &dhara_priv_data->dhara_nand, handle->work_buffer,',dhara)
        self.assertIn('dhara_map_read(&dhara_priv_data->dhara_map, sector_id, handle->read_buffer,',dhara)
        for name in ('spi_nand_quad_read','spi_nand_dual_read','spi_nand_fast_read'):
            body=trace.extract_function(oper,'static esp_err_t '+name+'(')
            self.assertIn('spi_nand_rx_prepare_read_buffers(handle, data, length,',body)
            self.assertIn('memcpy(data, data_read, length)',body)
        self.assertIn('spi_nand_tx_prepare_write_buffers(handle, data, length,',trace.extract_function(oper,'esp_err_t spi_nand_program_load('))
    def test_actual_allocation_failures(self):self.compile(allocation_fixture(self.generated['nand_impl.c']),'allocation')
    def test_actual_bounce_helpers(self):self.compile(bounce_fixture(self.oper),'bounce')

if __name__=='__main__':unittest.main()
