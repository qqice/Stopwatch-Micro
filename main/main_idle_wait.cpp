/* SPDX-License-Identifier: MIT */
#include "main_idle_wait.h"
#include <sdkconfig.h>
#include <freertos/FreeRTOS.h>
#include <freertos/task.h>
#include <esp_timer.h>
#include <atomic>
#if CONFIG_MOSAICO_EVENT_IDLE_WAIT && CONFIG_IDF_TARGET_ESP32S31 && configTASK_NOTIFICATION_ARRAY_ENTRIES >= 2 && !CONFIG_UART_ISR_IN_IRAM
#define MAIN_IDLE_SUPPORTED 1
#include <driver/gpio.h>
#include <driver/uart_select.h>
#else
#define MAIN_IDLE_SUPPORTED 0
#endif
namespace MainIdleWait {
namespace {
// Even an opt-in test build boots at the unchanged 20 ms baseline.
bool enabled=false;
bool gpioReady=false, uartReady=false, handlerInstalled=false;
std::atomic<bool> serialBusy{true}, safeView{false};
std::atomic<uint32_t> gpioCount{0}, uartCount{0};
uint32_t seenGpio=0, eventWaits=0, maxWaitUs=0, requestedMs=20;
int64_t serialAwakeUntil=0;
int32_t error=0;
Cause cause=Cause::Disabled;
ButtonGrace grace;
#if MAIN_IDLE_SUPPORTED
constexpr UBaseType_t NotifyIndex=1;
TaskHandle_t mainTask=nullptr;
portMUX_TYPE taskLock=portMUX_INITIALIZER_UNLOCKED;
std::atomic<bool> gpioMasked{true};
static_assert(std::atomic<uint32_t>::is_always_lock_free && std::atomic<bool>::is_always_lock_free,"ISR uses native atomics");
// Non-IRAM GPIO service matches SerialDebug's non-IRAM UART driver. No flash
// operations/logs/mutexes in callbacks; OTA/health uses the old 20 ms cadence.
void gpioInterrupt(void*) {
    gpio_intr_disable(GPIO_NUM_7); // One notification per held LOW, never a storm.
    gpioMasked.store(true,std::memory_order_relaxed);
    gpioCount.fetch_add(1,std::memory_order_relaxed);
    BaseType_t woken=pdFALSE;
    portENTER_CRITICAL_ISR(&taskLock);
    if(mainTask)vTaskNotifyGiveIndexedFromISR(mainTask,NotifyIndex,&woken);
    portEXIT_CRITICAL_ISR(&taskLock);
    if(woken)portYIELD_FROM_ISR();
}
void uartInterrupt(uart_port_t port,uart_select_notif_t event,BaseType_t* woken) {
    if(port!=UART_NUM_0 || (event!=UART_SELECT_READ_NOTIF && event!=UART_SELECT_ERROR_NOTIF))return;
    uartCount.fetch_add(1,std::memory_order_relaxed);
    // UART driver already holds its public selectlock and performs the yield.
    portENTER_CRITICAL_ISR(&taskLock);
    if(mainTask)vTaskNotifyGiveIndexedFromISR(mainTask,NotifyIndex,woken);
    portEXIT_CRITICAL_ISR(&taskLock);
}
#endif
}
Lifetime::Lifetime() {
#if MAIN_IDLE_SUPPORTED
    mainTask=xTaskGetCurrentTaskHandle();
    // CST9217 is manual LVGL input with no registered interrupt callback. Its
    // LOW interrupt configuration must not become live when adding the first
    // global GPIO ISR service. Mask only; do not change its type or pad state.
    error=gpio_intr_disable(GPIO_NUM_6);
    if(error==ESP_OK)error=gpio_install_isr_service(0);
    // An existing service has unknown flags/ownership: fail closed, not reuse.
    if(error==ESP_OK) {
        error=gpio_intr_disable(GPIO_NUM_7);
        if(error==ESP_OK)error=gpio_set_intr_type(GPIO_NUM_7,GPIO_INTR_LOW_LEVEL);
        if(error==ESP_OK)error=gpio_isr_handler_add(GPIO_NUM_7,gpioInterrupt,nullptr);
        if(error==ESP_OK) {gpioReady=true;handlerInstalled=true;}
        else gpio_intr_disable(GPIO_NUM_7);
    }
#endif
}
Lifetime::~Lifetime() {
    uartOwner(false);
#if MAIN_IDLE_SUPPORTED
    if(handlerInstalled) {gpio_intr_disable(GPIO_NUM_7);gpio_isr_handler_remove(GPIO_NUM_7);handlerInstalled=false;}
    // Keep the installed shared ISR service; never uninstall someone else's IRQs.
    portENTER_CRITICAL(&taskLock);mainTask=nullptr;portEXIT_CRITICAL(&taskLock);
#endif
    gpioReady=false;enabled=false;
}
bool setEnabled(bool value) {
    if(value && (!MAIN_IDLE_SUPPORTED || !gpioReady || !uartReady))return false;
    enabled=value;return true;
}
void uartOwner(bool owned) {
#if MAIN_IDLE_SUPPORTED
    // No VFS UART owner is installed on this profile. Attach only immediately
    // after SerialDebug installed the driver, detach BEFORE driver deletion.
    if(owned && mainTask && uart_is_driver_installed(UART_NUM_0)) {
        portENTER_CRITICAL(uart_get_selectlock());
        uart_set_select_notif_callback(UART_NUM_0,uartInterrupt);
        portEXIT_CRITICAL(uart_get_selectlock());uartReady=true;
    } else if(uartReady) {
        portENTER_CRITICAL(uart_get_selectlock());
        uart_set_select_notif_callback(UART_NUM_0,nullptr);
        portEXIT_CRITICAL(uart_get_selectlock());uartReady=false;
    }
#else
    (void)owned;
#endif
}
void serialState(bool busy,bool activity) {
    serialBusy.store(busy,std::memory_order_relaxed);
    if(activity)serialAwakeUntil=esp_timer_get_time()+500000;
}
void viewState(bool safe) {safeView.store(safe,std::memory_order_relaxed);}
void wait(bool locked,bool ota,bool usb,bool wifi,bool ble) {
    Gates gates;gates.locked=locked;gates.enabled=enabled;gates.supported=MAIN_IDLE_SUPPORTED;
    gates.gpio=gpioReady;gates.uart=uartReady;gates.ota=ota;gates.usb=usb;gates.wifi=wifi;gates.ble=ble;
    gates.serial=serialBusy.load(std::memory_order_relaxed) || esp_timer_get_time()<serialAwakeUntil;
    gates.view=safeView.load(std::memory_order_relaxed);
#if MAIN_IDLE_SUPPORTED
    const uint32_t count=gpioCount.load(std::memory_order_relaxed);
    const bool rawLow=gpio_get_level(GPIO_NUM_7)==0;
    gates.button=grace.service(static_cast<uint32_t>(esp_timer_get_time()/1000),rawLow,count!=seenGpio);
    seenGpio=count;
    if(gpioReady && !rawLow && gpioMasked.exchange(false,std::memory_order_relaxed)) {
        const auto rc=gpio_intr_enable(GPIO_NUM_7);
        if(rc!=ESP_OK) {error=rc;gpioReady=false;gates.gpio=false;}
    }
#endif
    cause=select(gates);requestedMs=waitMs(cause);
    const int64_t start=esp_timer_get_time();
#if MAIN_IDLE_SUPPORTED
    if(cause==Cause::Event) {
        ++eventWaits;
        // Do not clear before waiting: IRQs racing the eligibility sample are
        // latched. Index 0 belongs to startup/vendor tasks, never consume it.
        ulTaskNotifyTakeIndexed(NotifyIndex,pdTRUE,pdMS_TO_TICKS(requestedMs));
    } else
#endif
    vTaskDelay(pdMS_TO_TICKS(requestedMs));
    const auto elapsed=esp_timer_get_time()-start;
    if(elapsed>0 && static_cast<uint64_t>(elapsed)>maxWaitUs)
        maxWaitUs=static_cast<uint64_t>(elapsed)>UINT32_MAX ? UINT32_MAX : static_cast<uint32_t>(elapsed);
}
Snapshot snapshot() {
    Snapshot s;s.supported=MAIN_IDLE_SUPPORTED;s.enabled=enabled;s.gpio=gpioReady;s.uart=uartReady;
    s.gpioWakes=gpioCount.load(std::memory_order_relaxed);s.uartWakes=uartCount.load(std::memory_order_relaxed);
    s.eventWaits=eventWaits;s.maxWaitUs=maxWaitUs;s.requestedMs=requestedMs;s.error=error;s.cause=cause;return s;
}
}
