#pragma once
#include <array>
#include <cstddef>
#include <cstdint>
#include <memory>
#include <hal/ble/codex_micro_ble.h>
#include <host/quota_monitor.h>
#include <host/token_history.h>
#include <lvgl.h>

namespace view {
class CodexMicroView {
public:
    enum class Page : uint8_t { Command = 0, History, Agent };
    ~CodexMicroView();
    void init(lv_obj_t* parent);
    void update(const CodexMicroState& state);
    void togglePage();
    void setMicActive(bool) {}
    bool ready() const { return _root != nullptr; }
    bool functionalEnabled() const { return ready(); }
    bool micActive() const { return false; }
    Page currentPage() const { return _page; }
    bool setPageForDebug(Page page);
    void setInputSuppressed(bool suppressed) { _suppressed = suppressed; }
    void wakeDisplay();
    bool locked() const { return _locked; }
    bool lockForDebug();
    uint32_t lockRefreshCount() const { return _lockRefreshCount; }
    bool showHistory(bool hourly);
    bool selectHistory(std::size_t index);
    void historyDetails(char* out, std::size_t capacity) const;
private:
    struct Hit { CodexMicroView* owner = nullptr; std::size_t index = 0; };
    static void touchEvent(lv_event_t* event);
    static void wakeEvent(lv_event_t* event);
    static void cellEvent(lv_event_t* event);
    static void modeEvent(lv_event_t* event);
    void refreshQuota(uint32_t now);
    void refreshHistory();
    void updateHistoryHeader(bool force = false);
    void renderHistory();
    void renderSelection();
    void lockDisplay();
    const TokenHistoryCell* selectedCell() const;
    lv_obj_t* _root = nullptr;
    lv_obj_t* _quotaPage = nullptr;
    lv_obj_t* _historyPage = nullptr;
    lv_obj_t* _overlay = nullptr;
    lv_obj_t* _lockPanel = nullptr;
    lv_obj_t* _lockQuota = nullptr;
    lv_obj_t* _lockBattery = nullptr;
    lv_obj_t* _battery = nullptr;
    lv_obj_t* _footer = nullptr;
    lv_obj_t* _quotaStatus = nullptr;
    std::array<lv_obj_t*, 8> _cards{};
    std::array<lv_obj_t*, 8> _cardTitles{}, _cardValues{}, _cardMeta{}, _secondValues{};
    std::array<std::array<lv_obj_t*, 2>, 8> _windowLabels{}, _windowBars{};
    std::array<lv_obj_t*, 30> _cells{}, _cellLabels{};
    std::array<Hit, 30> _hits{};
    std::array<Hit, 2> _modeHits{};
    std::array<lv_obj_t*, 2> _modeButtons{};
    lv_obj_t* _range = nullptr;
    lv_obj_t* _legend = nullptr;
    lv_obj_t* _details = nullptr;
    std::unique_ptr<QuotaMonitorSnapshot> _quota;
    std::unique_ptr<TokenHistorySnapshot> _history;
    Page _page = Page::Command;
    bool _hourly = false, _locked = false, _suppressed = false, _wakeGesture = false;
    std::size_t _selected = SIZE_MAX;
    uint32_t _activity = 0, _refresh = 0, _lockRefreshCount = 0;
    uint32_t _quotaRevision = UINT32_MAX, _historyRevision = UINT32_MAX;
    uint32_t _historyAgeKey = UINT32_MAX;
    int _brightness = 80;
};
}
