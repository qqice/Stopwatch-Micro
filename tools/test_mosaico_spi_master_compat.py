"""Run unmodified extracted SDK C statements on RV32 with mocked HAL/allocator.

No device, RTOS concurrency, cache hardware, DMA or target-build claim.
Reuses the project's installed private Unicorn/clang runner (no downloads).
"""
from pathlib import Path
import hashlib
import difflib
import os
import tempfile
import unittest
from unittest.mock import patch
import mosaico_spi_master_compat as compat
import test_microlink_peer_probe as rv32

ROOT = Path(__file__).resolve().parents[1]
IDF = Path('C:/esp/v6.1/esp-idf')
RUN = ROOT / '.artifacts/private/mosaico/spi-master-compat-tests-001'
COMMON_SHA = 'd9b005e4beaf323553254970e55eb47b36065bcfb8ea11d3c1b49f4101ca1a80'

PREFIX = r'''
typedef unsigned int uint32_t; typedef unsigned short uint16_t;
typedef unsigned char uint8_t; typedef unsigned int size_t;
typedef int esp_err_t; typedef int spi_host_device_t; typedef int BaseType_t; typedef unsigned TickType_t;
#define NULL nullptr
#define SPI_MASTER_ISR_ATTR
#define SPI_MASTER_ATTR
#define SPI_COMMON_ISR_ATTR
#define SOC_IS(x) 0
#define SPI_TAG "test"
#define ESP_OK 0
#define ESP_ERR_NO_MEM 101
#define ESP_ERR_INVALID_STATE 102
#define ESP_ERR_INVALID_ARG 103
#define ESP_ERR_TIMEOUT 104
#define ESP_ERR_NOT_SUPPORTED 105
#define SPI_TRANS_USE_RXDATA 1
#define SPI_TRANS_USE_TXDATA 2
#define SPI_TRANS_DMA_USE_PSRAM 4
#define SPI_TRANS_DMA_BUFFER_ALIGN_MANUAL 8
#define SPI_TRANS_CS_KEEP_ACTIVE 16
#define SPI_TRANS_DMA_RX_FAIL 32
#define SPI_TRANS_DMA_TX_FAIL 64
#define SPI_DEVICE_NO_RETURN_RESULT 1
#define MALLOC_CAP_DMA 1
#define MALLOC_CAP_SPIRAM 2
#define MALLOC_CAP_INTERNAL 4
#define ESP_CACHE_MSYNC_FLAG_DIR_C2M 1
#define ESP_CACHE_MSYNC_FLAG_UNALIGNED 2
#define ESP_CACHE_MSYNC_FLAG_DIR_M2C 4
#define portMAX_DELAY 0xffffffffu
#define MAX(a,b) ((a)>(b)?(a):(b))
#define ESP_LOGE(...) ((void)0)
#define ESP_LOGV(...) ((void)0)
#define ESP_EARLY_LOGV(tag,fmt,h,dir,buf,len,...) do {if((dir)[0]=='R') observed_rx_len=(len);} while(0)
#define assert(x) do {if(!(x)) bad++;} while(0)
#define SPI_CHECK(x,msg,e) do {if(!(x)) return e;} while(0)
#define ESP_RETURN_ON_FALSE_ISR(x,e,...) SPI_CHECK(x,"",e)
struct spi_transaction_t {uint32_t flags,length,rxlength;const void* tx_buffer;void* rx_buffer;uint32_t tx_data[1],rx_data[1];};
struct spi_trans_priv_t {spi_transaction_t* trans;const uint32_t* buffer_to_send;uint32_t* buffer_to_rcv;};
struct spi_bus_attr_t {bool dma_enabled;unsigned cache_align_int,cache_align_ext;};
struct spi_dma_ctx_t {unsigned dma_align_tx_ext,dma_align_tx_int,dma_align_rx_ext,dma_align_rx_int;};
struct spi_device_t;
struct spi_host_t {spi_bus_attr_t* bus_attr;int id;spi_device_t* device_acquiring_lock;bool polling;spi_trans_priv_t cur_trans_buf;int cur_cs,hal;};
struct spi_device_t {spi_host_t* host;int trans_queue,ret_queue,dev_lock,id;struct {unsigned flags;} cfg;};
typedef spi_device_t* spi_device_handle_t;
static int bad,allocs,frees,live,fail_alloc,fail_sync,syncs,copies,null_copies,queue_fail,lock_error;
static unsigned copied_bytes;
static unsigned observed_rx_len;
static bool external,inplace;
static uint32_t tx[4],rx[4],pool[4][8];static bool leased[4];
static spi_bus_attr_t attr;static spi_dma_ctx_t dma;
static spi_host_t host;static spi_device_t dev;static spi_trans_priv_t queued;
extern "C" void* memcpy(void* d,const void* s,size_t n) {++copies;copied_bytes+=n;if(!d||!s){++null_copies;return d;}for(unsigned i=0;i<n;++i)((uint8_t*)d)[i]=((const uint8_t*)s)[i];return d;}
extern "C" void* memset(void* d,int v,size_t n) {for(unsigned i=0;i<n;++i)((uint8_t*)d)[i]=v;return d;}
extern "C" void free(void* p){if(!p)return;for(int i=0;i<4;++i)if(p==pool[i]){if(!leased[i])++bad;else{leased[i]=false;++frees;--live;}return;}++bad;}
static uint32_t* heap_caps_aligned_alloc(unsigned,unsigned,unsigned){++allocs;if(allocs==fail_alloc)return NULL;for(int i=0;i<4;++i)if(!leased[i]){leased[i]=true;++live;for(auto &v:pool[i])v=0xa5a5a5a5;return pool[i];}++bad;return NULL;}
static spi_bus_attr_t* spi_bus_get_attr(int){return &attr;}
static spi_dma_ctx_t* spi_bus_get_dma_ctx(int){return &dma;}
static bool esp_ptr_external_ram(const void* p){return external&&(p==tx||p==rx);}
static bool esp_ptr_dma_ext_capable(const void*){return inplace;}
static bool esp_ptr_dma_capable(const void*){return inplace;}
static int esp_cache_msync(void*,unsigned,unsigned){return ++syncs==fail_sync?ESP_ERR_INVALID_ARG:ESP_OK;}
static int check_trans_valid(spi_device_handle_t,spi_transaction_t*){return ESP_OK;}
static bool spi_bus_device_is_polling(spi_device_handle_t){return host.polling;}
static int xQueueSend(int,void* p,unsigned){if(queue_fail)return 0;queued=*(spi_trans_priv_t*)p;return 1;}
static int xQueueReceive(int,void* p,unsigned){*(spi_trans_priv_t*)p=queued;return 1;}
static int spi_bus_lock_bg_request(int){return ESP_OK;}
static int spi_bus_lock_acquire_start(int,unsigned){return lock_error;}
static int spi_bus_lock_wait_bg_done(int,unsigned){return lock_error;}
static void spi_new_trans(spi_device_handle_t,spi_trans_priv_t*){}
static spi_device_t* get_acquiring_dev(spi_host_t*){return &dev;}
static TickType_t xTaskGetTickCount(){return 0;}
static bool spi_hal_usr_is_done(int*){return true;}
static void spicommon_dma_rx_mb(int,void*){}
static void spi_trans_dma_error_check(spi_host_t*){}
static void spi_post_trans(spi_host_t*){}
static void spi_bus_lock_acquire_end(int){}
'''

CASES = r'''
static void reset(){bad=allocs=frees=live=fail_alloc=fail_sync=syncs=copies=null_copies=queue_fail=lock_error=0;copied_bytes=0;external=inplace=false;
 observed_rx_len=0;for(int i=0;i<4;++i){leased[i]=false;tx[i]=0x12345678;rx[i]=0xcccccccc;}
 attr={true,1,32};dma={4,4,4,4};host={&attr,1,NULL,false,{},0,0};dev={&host,1,2,3,0,{0}};queued={};}
static spi_transaction_t transaction(int n){return {0,8,(unsigned)n*8,tx,rx,{0},{0}};}
static int failure(int mode,int n,bool ext){reset();external=ext;auto t=transaction(n);const unsigned sentinel=rx[0];
 if(mode==0)fail_alloc=1; // TX allocation failure, ret_buffer left NULL.
 if(mode==1)fail_alloc=2; // RX allocation failure, TX bounce already owned.
 if(mode==2)fail_sync=1;  // TX cache sync failure after allocation and ret_buffer assignment.
 if(mode==3)fail_sync=2;  // RX cache sync failure after both bounce allocations.
 if(mode==4)queue_fail=1;
 if(mode==5)lock_error=177;
 int expected=mode<2?ESP_ERR_NO_MEM:mode<4?ESP_ERR_INVALID_ARG:mode==4?ESP_ERR_TIMEOUT:177;
 int got=mode==5?spi_device_polling_start(&dev,&t,portMAX_DELAY):spi_device_queue_trans(&dev,&t,0);
 int expected_free=mode==0?0:mode==1||mode==2?1:2;
 if(got!=expected)return 1;if(rx[0]!=sentinel)return 2;if(null_copies||bad||live||frees!=expected_free)return 3;
 // Only the TX bounce preparation may copy; cleanup must never touch RX.
 if(copies!=(mode==0?0:1)||copied_bytes!=(mode==0?0u:1u))return 4;
 return 0;}
static int success(int mode,int n){reset();auto t=transaction(n);
 if(mode==1)inplace=true;
 if(mode==2)t.rx_buffer=NULL;
 if(mode==3)attr.dma_enabled=false;
 if(mode==4){t.flags=SPI_TRANS_USE_RXDATA|SPI_TRANS_USE_TXDATA;t.tx_data[0]=123;}
 if(mode==5)t.flags=SPI_TRANS_DMA_USE_PSRAM,external=true;
 int got=mode==6?spi_device_polling_start(&dev,&t,portMAX_DELAY):spi_device_queue_trans(&dev,&t,0);if(got)return 1;
 if(mode==7)t.flags|=SPI_TRANS_DMA_RX_FAIL; // Mock completed hardware DMA error report.
 auto* buffer=mode==6?host.cur_trans_buf.buffer_to_rcv:queued.buffer_to_rcv;
 if(buffer){auto* bytes=(uint8_t*)buffer;for(int i=0;i<n;++i)bytes[i]=0x40+i;}
 spi_transaction_t* result=NULL;got=mode==6?spi_device_polling_end(&dev,0):spi_device_get_trans_result(&dev,&result,0);
 if(got!=(mode==7?ESP_ERR_INVALID_STATE:ESP_OK)||bad||null_copies||live||frees!=allocs)return 2;
 if(mode!=6&&result!=&t)return 3;
 if(mode!=2){auto* original=(uint8_t*)(mode==4?t.rx_data:rx);for(int i=0;i<n;++i)if(original[i]!=0x40+i)return 4;
 if(original[n]!=(mode==4?0:0xcc))return 5;}
 if((mode==1||mode==3)&&allocs)return 6;
 if(mode==2&&(frees!=1||copies!=1))return 7;
 return 0;}
static int run_case(int id){if(id<24){int mode=id/4;return failure(mode,id%2+1,(id/2)%2);}if(id<40)return success((id-24)/2,id%2+1);
 if(id==40){reset();auto t=transaction(1);spi_trans_priv_t p={&t,NULL,NULL};uninstall_priv_desc(&p,true);return bad||null_copies||copies||live?1:0;}
 reset();int n=(id-41)%2+1;external=id>=43;attr.cache_align_int=64;
 auto t=transaction(n);t.flags=SPI_TRANS_DMA_BUFFER_ALIGN_MANUAL;t.tx_buffer=NULL;t.length=0;
 int got=spi_device_queue_trans(&dev,&t,0);
 if(got!=ESP_ERR_INVALID_STATE||bad||null_copies||copies||allocs||frees||live)return 1;
 if(t.rxlength!=unsigned(n*8)||observed_rx_len!=unsigned(n)||rx[0]!=0xcccccccc)return 2;
 return 0;}
extern "C" {volatile int test_results[45]={};}
extern "C" __attribute__((noinline,used)) void test_done(){for(;;){asm volatile("");}}
extern "C" void _start(){for(int i=0;i<45;++i)test_results[i]=run_case(i);test_done();}
'''


def fixture(source):
    common_raw = (IDF / 'components/esp_driver_spi/src/gpspi/spi_common.c').read_bytes()
    assert hashlib.sha256(common_raw).hexdigest() == COMMON_SHA
    common = common_raw.decode().replace('\r\n', '\n')
    body = rv32.function(common, 'esp_err_t SPI_COMMON_ISR_ATTR spicommon_dma_setup_priv_buffer(')
    # The SDK C caller casts its const TX output to void*. C++ needs an
    # overload for C's implicit void* conversion; all extracted bodies stay exact.
    body += '''static esp_err_t spicommon_dma_setup_priv_buffer(spi_host_device_t h, uint32_t* b, uint32_t n, bool tx, bool psram, bool automatic, void* out) {
 return spicommon_dma_setup_priv_buffer(h,b,n,tx,psram,automatic,(uint32_t**)out);
}\n'''
    for signature in (
        'static SPI_MASTER_ISR_ATTR void uninstall_priv_desc(',
        'static SPI_MASTER_ISR_ATTR esp_err_t setup_priv_desc(',
        'esp_err_t SPI_MASTER_ATTR spi_device_queue_trans(',
        'esp_err_t SPI_MASTER_ATTR spi_device_get_trans_result(',
        'esp_err_t SPI_MASTER_ISR_ATTR spi_device_polling_start(',
        'esp_err_t SPI_MASTER_ISR_ATTR spi_device_polling_end(',
    ):
        body += rv32.function(source, signature)
    return PREFIX + body + CASES


class CompatTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.original = (IDF / compat.SOURCE_REL).read_bytes()
        cls.source = cls.original.decode().replace('\r\n', '\n')

    def test_actual_c_fault_and_success_matrix(self):
        original_fixture, original_run = rv32.fixture, rv32.RUN
        try:
            rv32.fixture, rv32.RUN = fixture, RUN
            result = rv32.execute(compat.patch_source(self.source), 'cleanup', 45)
            (RUN / 'cleanup-execution.log').write_text(
                'Actual extracted spi_common allocator + spi_master setup/cleanup/queue/get/polling C statements; '
                'mock RTOS/HAL/cache/heap; no hardware/concurrency claim.\n'
                f'spi_master SHA256={compat.SOURCE_SHA256}\nspi_common SHA256={COMMON_SHA}\n'
                f'harness SHA256={hashlib.sha256((RUN / "cleanup.cpp").read_bytes()).hexdigest()}\n'
                f'ELF SHA256={hashlib.sha256((RUN / "cleanup.elf").read_bytes()).hexdigest()}\n'
                f'RV32 results={result}\n')
            self.assertEqual(result, (0,) * 45)
        finally:
            rv32.fixture, rv32.RUN = original_fixture, original_run

    def test_unpatched_negative_control(self):
        global CASES
        original_fixture, original_run, original_cases = rv32.fixture, rv32.RUN, CASES
        try:
            # Original cleanup has no boolean parameter; no production body edits.
            CASES = CASES.replace('uninstall_priv_desc(&p,true);', 'uninstall_priv_desc(&p);')
            rv32.fixture, rv32.RUN = fixture, RUN
            result = rv32.execute(self.source, 'unpatched-control', 45)
            self.assertTrue(all(value != 0 for value in result[:24]))
            self.assertEqual(result[24:40], (0,) * 16)
            self.assertTrue(all(value != 0 for value in result[40:]))
            (RUN / 'unpatched-control-execution.log').write_text(
                'Unpatched exact SDK functions, same fault/success harness.\n'
                'Every failure path and NULL RX memcpy guard fails; normal paths pass.\n'
                f'harness SHA256={hashlib.sha256((RUN / "unpatched-control.cpp").read_bytes()).hexdigest()}\n'
                f'ELF SHA256={hashlib.sha256((RUN / "unpatched-control.elf").read_bytes()).hexdigest()}\n'
                f'RV32 results={result}\n')
        finally:
            CASES = original_cases
            rv32.fixture, rv32.RUN = original_fixture, original_run

    def test_pinned_generation_and_original_unchanged(self):
        RUN.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(dir=RUN) as d:
            generated = compat.generate(IDF, Path(d) / 'out')
            self.assertEqual(generated.read_text(encoding='utf-8'), compat.patch_source(self.source))
        self.assertEqual((IDF / compat.SOURCE_REL).read_bytes(), self.original)
        self.assertEqual(hashlib.sha256(self.original).hexdigest(), compat.SOURCE_SHA256)
        (RUN / 'upstream-to-generated.diff').write_text(''.join(difflib.unified_diff(
            self.source.splitlines(True), compat.patch_source(self.source).splitlines(True),
            fromfile='SDK/spi_master.c', tofile='generated/spi_master.c')), encoding='utf-8')

    def test_sha_drift_and_output_boundaries(self):
        RUN.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(dir=RUN) as d:
            fake = Path(d) / 'idf'
            original = fake / compat.SOURCE_REL
            original.parent.mkdir(parents=True)
            original.write_bytes(self.original + b'\n')
            with self.assertRaisesRegex(ValueError, 'SHA256'):
                compat.generate(fake, Path(d) / 'out')
            self.assertFalse((Path(d) / 'out').exists())
            for output in (fake, fake / 'generated', fake / 'components/../generated'):
                with self.assertRaisesRegex(ValueError, 'inside the SDK'):
                    compat.generate(fake, output)

    def test_output_hardlink_guard_injected_metadata(self):
        RUN.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(dir=RUN) as d:
            fake, output = Path(d) / 'idf', Path(d) / 'out'
            original = fake / compat.SOURCE_REL
            original.parent.mkdir(parents=True)
            original.write_bytes(self.original)
            output.mkdir()
            destination = output / 'spi_master.c'
            destination.write_bytes(b'output sentinel')
            # Sandbox disallows os.link; independently inject the two guard
            # conditions without creating links to any SDK file.
            with patch.object(compat.os.path, 'samefile', return_value=True):
                with self.assertRaisesRegex(ValueError, 'hardlink'):
                    compat.generate(fake, output)
            real_stat = Path.stat
            def linked_stat(path, *args, **kwargs):
                result = real_stat(path, *args, **kwargs)
                if path == destination:
                    fields = list(result)
                    fields[3] = 2  # st_nlink
                    return os.stat_result(fields)
                return result
            with patch.object(Path, 'stat', linked_stat), patch.object(compat.os.path, 'samefile', return_value=False):
                with self.assertRaisesRegex(ValueError, 'hardlink'):
                    compat.generate(fake, output)
            self.assertEqual(destination.read_bytes(), b'output sentinel')
            self.assertEqual(original.read_bytes(), self.original)

    def test_anchor_and_call_count_drift(self):
        for bad in (
            self.source.replace('uninstall_priv_desc(priv_desc);', ''),
            self.source + '\nuninstall_priv_desc(extra);',
            self.source.replace('uninstall_priv_desc(priv_desc);', 'uninstall_priv_desc(other);'),
            self.source.replace('    if (trans_buf->buffer_to_rcv != orig_rx_buffer) {', 'CHANGED'),
            self.source + '\n    if (trans_buf->buffer_to_rcv != orig_rx_buffer) {\n'
            '        memcpy(orig_rx_buffer, trans_buf->buffer_to_rcv, (trans_desc->rxlength + 7) / 8);',
        ):
            with self.assertRaises(ValueError):
                compat.patch_source(bad)


if __name__ == '__main__':
    unittest.main()
