/* SPDX-License-Identifier: MIT */
#include "main_idle_wait.h"
#include <sdkconfig.h>
#include <freertos/FreeRTOS.h>
#include <freertos/task.h>
#include <esp_timer.h>
#include <atomic>
#include <soc/soc_caps.h>
#include <host/uart_fifo_recovery_model.h>
#if CONFIG_MOSAICO_EVENT_IDLE_WAIT && CONFIG_IDF_TARGET_ESP32S31 && configTASK_NOTIFICATION_ARRAY_ENTRIES >= 2 && !CONFIG_UART_ISR_IN_IRAM && (!CONFIG_MOSAICO_UART_FIFO_RECOVERY || (SOC_UART_WAKEUP_SUPPORT_FIFO_THRESH_MODE && SOC_PM_SUPPORT_PMU_CLK_ICG))
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
#if CONFIG_MOSAICO_UART_FIFO_RECOVERY && CONFIG_IDF_TARGET_ESP32S31
constexpr bool fifoRecovery=true;
#else
constexpr bool fifoRecovery=false;
#endif
std::atomic<bool> rxReady{false}, recoveryReady{false};
std::atomic<int32_t> recoveryError{0};
std::atomic<uint32_t> readCount{0}, errorCount{0}, lastReadUs{0};
uint32_t queuePeak=0, wakeEvents=0, dataEvents=0, lastWakeUs=0, lastDataUs=0;
uint32_t lastWaitNotify=0, lastWaitUs=0, lastWaitMs=20, maxWaitMs=20;
Cause lastWaitCause=Cause::Disabled, maxWaitCause=Cause::Disabled;
bool uartComplete() {
    return uartReady && (!fifoRecovery || (rxReady.load(std::memory_order_acquire) && recoveryReady.load(std::memory_order_acquire)));
}
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
    if(event==UART_SELECT_READ_NOTIF) {
        readCount.fetch_add(1,std::memory_order_relaxed);
        lastReadUs.store(static_cast<uint32_t>(esp_timer_get_time()),std::memory_order_relaxed);
    } else errorCount.fetch_add(1,std::memory_order_relaxed);
    // UART driver already holds its public selectlock and performs the yield.
    portENTER_CRITICAL_ISR(&taskLock);
    if(mainTask)vTaskNotifyGiveIndexedFromISR(mainTask,NotifyIndex,woken);
    portEXIT_CRITICAL_ISR(&taskLock);
}
#endif
}
Lifetime::Lifetime() {
#if MAIN_IDLE_SUPPORTED
    portENTER_CRITICAL(&taskLock);mainTask=xTaskGetCurrentTaskHandle();portEXIT_CRITICAL(&taskLock);
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
    if(value && (!MAIN_IDLE_SUPPORTED || !gpioReady || !uartComplete()))return false;
    enabled=value;return true;
}
void uartRxConfigured(bool ready,int32_t rc) {
    recoveryError.store(rc,std::memory_order_relaxed);rxReady.store(ready,std::memory_order_release);
}
void uartRecoveryConfigured(bool ready,int32_t rc) {
    recoveryError.store(rc,std::memory_order_relaxed);recoveryReady.store(ready,std::memory_order_release);
}
void uartEvent(bool wake,bool data,uint32_t depth) {
    if(depth>queuePeak)queuePeak=depth;
    const auto now=(wake || data) ? static_cast<uint32_t>(esp_timer_get_time()) : 0;
    if(wake) {++wakeEvents;lastWakeUs=now;}
    if(data) {++dataEvents;lastDataUs=now;}
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
void usbEvent() {
#if MAIN_IDLE_SUPPORTED
    // The task handle is valid only inside Lifetime. Startup callbacks and
    // callbacks after teardown still count in USB diagnostics but cannot wake.
    portENTER_CRITICAL(&taskLock);
    if(mainTask)xTaskNotifyGiveIndexed(mainTask,NotifyIndex);
    portEXIT_CRITICAL(&taskLock);
#endif
}
void wait(bool locked,bool ota,bool usb,bool wifi,bool ble) {
    Gates gates;gates.locked=locked;gates.enabled=enabled;gates.supported=MAIN_IDLE_SUPPORTED;
    gates.gpio=gpioReady;gates.uart=uartComplete();gates.ota=ota;gates.usb=usb;gates.wifi=wifi;gates.ble=ble;
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
    lastWaitNotify=0;lastWaitCause=cause;lastWaitMs=requestedMs;
#if MAIN_IDLE_SUPPORTED
    if(cause==Cause::Event) {
        ++eventWaits;
        // Do not clear before waiting: IRQs racing the eligibility sample are
        // latched. Index 0 belongs to startup/vendor tasks, never consume it.
        lastWaitNotify=ulTaskNotifyTakeIndexed(NotifyIndex,pdTRUE,pdMS_TO_TICKS(requestedMs));
    } else
#endif
    vTaskDelay(pdMS_TO_TICKS(requestedMs));
    const auto elapsed=esp_timer_get_time()-start;
    lastWaitUs=elapsed<=0 ? 0 : static_cast<uint64_t>(elapsed)>UINT32_MAX ? UINT32_MAX : static_cast<uint32_t>(elapsed);
    if(lastWaitUs>maxWaitUs) {maxWaitUs=lastWaitUs;maxWaitCause=lastWaitCause;maxWaitMs=lastWaitMs;}
}
Snapshot snapshot() {
    Snapshot s;s.supported=MAIN_IDLE_SUPPORTED;s.enabled=enabled;s.gpio=gpioReady;s.uart=uartReady;
    s.gpioWakes=gpioCount.load(std::memory_order_relaxed);s.uartWakes=uartCount.load(std::memory_order_relaxed);
    s.rxReady=rxReady.load(std::memory_order_acquire);s.recoveryReady=recoveryReady.load(std::memory_order_acquire);
    s.recoveryError=recoveryError.load(std::memory_order_relaxed);
    s.requestedWakeMode=fifoRecovery ? 1 : 0;
    s.appliedWakeMode=s.recoveryReady ? s.requestedWakeMode : -1;
    s.wakeThreshold=fifoRecovery ? UartFifoRecovery::WakeThreshold : 3;
    s.rxFullThreshold=fifoRecovery ? UartFifoRecovery::FullThreshold : 120;
    s.queueCapacity=fifoRecovery ? UartFifoRecovery::QueueCapacity : 8;s.queuePeak=queuePeak;
    s.wakeEvents=wakeEvents;s.dataEvents=dataEvents;s.lastWakeUs=lastWakeUs;s.lastDataUs=lastDataUs;
    s.readNotifies=readCount.load(std::memory_order_relaxed);s.errorNotifies=errorCount.load(std::memory_order_relaxed);
    s.lastReadUs=lastReadUs.load(std::memory_order_relaxed);
    s.lastWaitNotify=lastWaitNotify;s.lastWaitUs=lastWaitUs;s.lastWaitMs=lastWaitMs;s.maxWaitMs=maxWaitMs;
    s.lastWaitCause=lastWaitCause;s.maxWaitCause=maxWaitCause;
    s.eventWaits=eventWaits;s.maxWaitUs=maxWaitUs;s.requestedMs=requestedMs;s.error=error;s.cause=cause;return s;
}
}
