#pragma once
#include <cstddef>
#include <cstdint>
#include <atomic>
struct microlink_s;
class TailnetQuota {
public:
    // Numeric-only, last completed fetch; path/status zero does not imply success.
    enum class FetchPath : uint8_t { Quota = 0, History = 1, Ota = 2, Other = 3 };
    enum class FetchStage : uint8_t {
        None = 0, NotReady = 1, Input = 2, Connect = 3, Send = 4, Allocation = 5,
        Receive = 6, HttpStatus = 7, Header = 8, Oversize = 9, Deadline = 10,
        Incomplete = 11, Success = 12
    };
    struct FetchDiagnostics {
        FetchStage stage = FetchStage::None;
        int httpStatus = 0;
        uint32_t received = 0, contentLength = 0, elapsedMs = 0;
        bool result = false;
    };
    FetchDiagnostics fetchDiagnostics(FetchPath path) const;
    void load();
    bool configure(const char* base64);
    bool enabled() const
    {
        return _enabled;
    }
    void start();
    void rebind();
    bool pause();  // Call only from the network owner, after HTTP requests finish.
    bool fetch(const char* token, char* body, size_t capacity, int& length, const char* pathOverride = nullptr);
    int state() const;
    bool ready() const;
    uint32_t ip() const;

private:
    void publishFetchDiagnostics(FetchPath path, const FetchDiagnostics& value);
    FetchDiagnostics _fetch_diagnostics[4]{};
    std::atomic<bool> _enabled{false};
    std::atomic<bool> _valid{false};
    std::atomic<bool> _pause_requested{false};
    std::atomic<microlink_s*> _client{nullptr};
    char _key[257]{}, _host[16]{}, _path[192]{};
    uint16_t _port = 0;
    uint32_t _peer = 0;
};
TailnetQuota& GetTailnetQuota();
