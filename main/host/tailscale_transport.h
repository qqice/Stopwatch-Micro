#pragma once
#include <cstddef>
#include <cstdint>
#include <atomic>
struct microlink_s;
class TailnetQuota {
public:
    void load();
    bool configure(const char* base64);
    bool enabled() const
    {
        return _enabled;
    }
    void start();
    void rebind();
    bool fetch(const char* token, char* body, size_t capacity, int& length, const char* pathOverride = nullptr);
    int state() const;
    bool ready() const;
    uint32_t ip() const;

private:
    std::atomic<bool> _enabled{false};
    std::atomic<bool> _valid{false};
    std::atomic<microlink_s*> _client{nullptr};
    char _key[257]{}, _host[16]{}, _path[192]{};
    uint16_t _port = 0;
    uint32_t _peer = 0;
};
TailnetQuota& GetTailnetQuota();
