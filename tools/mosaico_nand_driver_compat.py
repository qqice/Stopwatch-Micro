"""Generate three compatibility TUs from exact, unmodified spi_nand_flash1.4.1.
Never edits SDK/managed sources. A digest mismatch refuses generation.
"""
from pathlib import Path
import argparse
import hashlib

HASHES={
 'nand.c':'3a64fb9efcf44986c4bd763b0afdd85bbfb0c5254f6b7147b33496a4652ed911',
 'nand_impl.c':'0389a9ec90b859a3feea8bd7c54fa134854579df808ef1fae20dad099b8e4544',
 'dhara_glue.c':'d6e232313009b40094e6f36cc9684daa2c7865d64bed27e8b866e4d39f36db75',
}
# Unmodified TU: PSRAM user buffers depend on these exact RX/TX bounce helpers.
DEPENDENCY_HASHES={'spi_nand_oper.c':'19b6145d14a639ed2c41c1a17ad84f9d638587c4d1723c469f9f30ff72aed642'}

def replace_once(source,old,new):
    if source.count(old)!=1:raise ValueError('unexpected upstream anchor: '+old[:80])
    return source.replace(old,new,1)

def patched(name,source):
    if name=='nand_impl.c':
        # CPU/Dhara buffers are never submitted directly as DMA: spi_nand_oper.c
        # RX/TX preparation bounces non-DMA user buffers through temp_buffer.
        # Keep aligned DMA temp_buffer (and all SPI flags) exactly upstream.
        for member in ('work_buffer','read_buffer'):
            old=f'    (*handle)->{member} = heap_caps_aligned_alloc(dma_alignment, (*handle)->chip.page_size, MALLOC_CAP_DMA | MALLOC_CAP_8BIT);'
            source=replace_once(source,old,old.replace('MALLOC_CAP_DMA','MALLOC_CAP_SPIRAM'))
        for old in (
            '        uint8_t *copy_buf = heap_caps_malloc(handle->chip.page_size, MALLOC_CAP_DMA | MALLOC_CAP_8BIT);',
            '    temp_buf = heap_caps_malloc(handle->chip.page_size, MALLOC_CAP_DMA | MALLOC_CAP_8BIT);',
            '    temp_buf = heap_caps_malloc(length, MALLOC_CAP_DMA | MALLOC_CAP_8BIT);',
        ):
            source=replace_once(source,old,old.replace('MALLOC_CAP_DMA','MALLOC_CAP_SPIRAM'))
        source=replace_once(source,'#define PACK_2BITS_STATUS(status, bit1, bit0)         ((((status) & (bit1)) << 1) | ((status) & (bit0)))', '#define PACK_2BITS_STATUS(status, bit1, bit0)         ((!!((status) & (bit1)) << 1) | !!((status) & (bit0)))')
        source=replace_once(source,'#define PACK_3BITS_STATUS(status, bit2, bit1, bit0)   ((((status) & (bit2)) << 2) | (((status) & (bit1)) << 1) | ((status) & (bit0)))', '#define PACK_3BITS_STATUS(status, bit2, bit1, bit0)   ((!!((status) & (bit2)) << 2) | (!!((status) & (bit1)) << 1) | !!((status) & (bit0)))')
        source=replace_once(source,'#include <string.h>','#include <string.h>\n#include "esp_timer.h"\n\n__attribute__((weak)) void mosaico_nand_compat_progress(uint32_t done, uint32_t total) { (void)done; (void)total; }')
        source=replace_once(source,'    *handle = heap_caps_calloc(1, sizeof(spi_nand_flash_device_t), MALLOC_CAP_DEFAULT);','    *handle = NULL;\n    *handle = heap_caps_calloc(1, sizeof(spi_nand_flash_device_t), MALLOC_CAP_DEFAULT);')
        source=replace_once(source,'    free(*handle);\n    return ret;','    free(*handle);\n    *handle = NULL;\n    return ret;')
        source=replace_once(source,'    while (true) {\n        uint8_t status;','    const int64_t started = esp_timer_get_time();\n    const int64_t timeout_us = expected_operation_time_us >= ROM_WAIT_THRESHOLD_US ? 1000000 : 200000;\n    while (true) {\n        if (esp_timer_get_time() - started >= timeout_us) return ESP_ERR_TIMEOUT;\n        uint8_t status;')
        source=replace_once(source,'        if (expected_operation_time_us >= ROM_WAIT_THRESHOLD_US) {\n            vTaskDelay(1);\n        }','        vTaskDelay(1); // Yield on every busy poll, including read/program.')
        source=replace_once(source,'        esp_err_t err = nand_erase_good_block(handle, (uint32_t)i);','        esp_err_t err = nand_erase_good_block(handle, (uint32_t)i);\n        mosaico_nand_compat_progress((uint32_t)i + 1, handle->chip.num_blocks);')
    elif name=='nand.c':
        source=replace_once(source,'static const char *TAG = "nand_api";', 'static const char *TAG = "nand_api";\nuint32_t mosaico_nand_compat_version(void) { return 0x01040103U; }')
        source=replace_once(source,'{\n#ifdef CONFIG_NAND_FLASH_ENABLE_BDL\n    ESP_LOGE(TAG, "spi_nand_flash_init_device()', '{\n    if (config == NULL || handle == NULL) return ESP_ERR_INVALID_ARG;\n    *handle = NULL;\n#ifdef CONFIG_NAND_FLASH_ENABLE_BDL\n    ESP_LOGE(TAG, "spi_nand_flash_init_device()')
        source=replace_once(source,'        ESP_LOGE(TAG, "Failed to attach wear-leveling operations");','        ESP_LOGE(TAG, "Failed to attach wear-leveling operations");\n        goto fail;')
        source=replace_once(source,'        ret = ESP_FAIL;\n        return ret;','        ret = ESP_FAIL;\n        goto fail;')
        source=replace_once(source,'    (*handle)->ops->init(*handle, NULL);\n\n    return ESP_OK;', '    ret = (*handle)->ops->init(*handle, NULL);\n    if (ret != ESP_OK) goto fail;\n    return ESP_OK;\nfail:\n    spi_nand_flash_deinit_device(*handle);\n    *handle = NULL;\n    return ret;')
    elif name=='dhara_glue.c':
        source=replace_once(source,'typedef struct {','// Only explicit format sessions may initialize an empty map before erasing.\n__attribute__((weak)) bool mosaico_nand_compat_format_authorized(void) { return false; }\n\ntypedef struct {')
        source=replace_once(source,'    dhara_error_t ignored;\n    dhara_map_resume(&dhara_priv_data->dhara_map, &ignored);', '''    if (!mosaico_nand_compat_format_authorized()) {
        dhara_error_t resume_error = DHARA_E_NONE;
        if (dhara_map_resume(&dhara_priv_data->dhara_map, &resume_error) < 0) {
            free(dhara_priv_data);
            handle->ops_priv_data = NULL;
            return resume_error == DHARA_E_NONE ? ESP_FAIL : ESP_ERR_FLASH_BASE + resume_error;
        }
    }''')
    else:raise ValueError(name)
    return source

def generate(component,output):
    component=Path(component).resolve();output=Path(output).resolve()
    if output==component or component in output.parents:raise ValueError('output must not modify upstream component')
    sources={}
    for name,expected in HASHES.items():
        raw=(component/'src'/name).read_bytes()
        if hashlib.sha256(raw).hexdigest()!=expected:raise ValueError('not exact spi_nand_flash1.4.1: '+name)
        sources[name]=patched(name,raw.decode())
    for name,expected in DEPENDENCY_HASHES.items():
        raw=(component/'src'/name).read_bytes()
        if hashlib.sha256(raw).hexdigest()!=expected:raise ValueError('not exact spi_nand_flash1.4.1 bounce dependency: '+name)
    output.mkdir(parents=True,exist_ok=True)
    for name,text in sources.items():(output/name).write_text(text,encoding='utf-8',newline='\n')
    return sources

if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--component',required=True);parser.add_argument('--output',required=True)
    args=parser.parse_args();generate(args.component,args.output)
