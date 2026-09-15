#pragma once
#include <atomic>
#include <cstddef>
#include <cstdint>

class NetworkQuota {
public:
    void begin();
    bool configure(const char* base64);
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
    std::atomic<bool> _configured{false}, _connected{false};
    std::atomic<uint32_t> _accepted{0}, _failures{0};
    std::atomic<uint32_t> _history_accepted{0}, _history_failures{0};
    char _ssid[33]{}, _password[65]{}, _url[257]{}, _token[129]{};
};
NetworkQuota& GetNetworkQuota();
