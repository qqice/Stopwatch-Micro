#pragma once
#include <cstddef>
#include <cstdint>
#include <atomic>
#include <wireguardif_rx_stats.h>
#include <microlink.h>
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
    enum class StartupDecision : uint8_t {
        None=0,Disabled=1,InvalidConfig=2,AlreadyStarted=3,ResumeStopFailed=4,
        ResumeStartFailed=5,Resumed=6,BudgetRejected=7,LargestRejected=8,
        SelftestFailed=9,InitFailed=10,StartFailed=11,Started=12
    };
    // Last completed start() call only. Numeric cache, not readiness/success IO.
    struct StartupDiagnostics {
        size_t actualFree=0,largest=0,rtcReservation=0,nandReservation=0,normalizedBudget=0;
        StartupDecision decision=StartupDecision::None;
        int32_t error=0;
        uint32_t attempts=0;
    };
    StartupDiagnostics startupDiagnostics() const;
    FetchDiagnostics fetchDiagnostics(FetchPath path) const;
    wireguardif_rx_stats_t rxDiagnostics() const;
    microlink_tcp_connect_diag_t tcpConnectDiagnostics() const;
    bool setDerpTxRetryBudgetMs(uint32_t milliseconds); // Physical diagnostic only; RAM, never NVS.
    uint32_t derpTxRetryBudgetMs() const;
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
    void publishStartupDiagnostics(StartupDiagnostics value);
    StartupDiagnostics _startup_diagnostics{};
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
