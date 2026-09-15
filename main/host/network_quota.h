#pragma once
#include <atomic>
#include <cstddef>
#include <cstdint>
#include <freertos/FreeRTOS.h>
#include <freertos/task.h>

struct IdlePowerStats {
    bool locked = false, wifiRunning = false;
    uint8_t profile = 2, phase = 0;
    uint32_t cpuMHz = 240, cycles = 0;
    uint64_t offMs = 0;
    int clockError = 0;
};

class NetworkQuota {
public:
    void begin();
    bool configure(const char* base64);
    void setLocked(bool locked);
    bool idleLocked() const
    {
        return _locked.load() && _power_profile.load() != 0;
    }
    void setPowerProfile(uint8_t profile);
    void refreshWhileLocked();
    IdlePowerStats powerStats() const;
    bool configured() const
    {
        return _configured;
    }
    bool connected() const
    {
        return _connected;
    }
    uint32_t accepted() const
    {
        return _accepted;
    }
    uint32_t failures() const
    {
        return _failures;
    }
    uint32_t historyAccepted() const
    {
        return _history_accepted;
    }
    uint32_t historyFailures() const
    {
        return _history_failures;
    }

private:
    static void task(void* arg);
    void run();
    bool fetch();
    bool requestJson(const char* path, char* body, size_t capacity, int& used);
    bool fetchHistory();
    void wait(uint32_t milliseconds);
    void setCpu(uint32_t mhz);
    void recordWifiRunning(bool running);
    void setPhase(uint8_t phase);
    TaskHandle_t _task_handle = nullptr;
    std::atomic<bool> _locked{false}, _wifi_running{false}, _force_refresh{false};
    std::atomic<uint8_t> _power_profile{2}, _power_phase{0};
    std::atomic<uint32_t> _power_cycles{0};
    std::atomic<int> _clock_error{0};
    uint32_t _cpu_target       = 0;
    uint64_t _off_completed_us = 0, _off_since_us = 0;
    mutable portMUX_TYPE _power_mux = portMUX_INITIALIZER_UNLOCKED;
    std::atomic<bool> _configured{false}, _connected{false};
    std::atomic<uint32_t> _accepted{0}, _failures{0};
    std::atomic<uint32_t> _history_accepted{0}, _history_failures{0};
    char _ssid[33]{}, _password[65]{}, _url[257]{}, _token[129]{};
};
NetworkQuota& GetNetworkQuota();
