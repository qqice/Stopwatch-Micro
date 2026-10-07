#pragma once
#include <array>
#include <cstddef>
#include <cstdint>
#include <memory>
#include <hal/ble/codex_micro_ble.h>
#include <host/quota_monitor.h>
#include <host/token_history.h>
#include <lvgl.h>
#include <ota/mosaico_ota.h>
#include <host/mosaico_display_settings.h>
#include <host/mosaico_session_monitor.h>
#include "charge_supply_state.h"

namespace view {
class CodexMicroView {
public:
    enum class Page : uint8_t { Command = 0, History, Agent, OTA, Settings, Sessions };
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
    void setInputSuppressed(bool suppressed);
    void wakeDisplay();
    bool otaBusy() const;
    bool locked() const { return _locked; }
    bool lockForDebug();
    uint32_t lockRefreshCount() const { return _lockRefreshCount; }
    bool showHistory(bool hourly);
    bool selectHistory(std::size_t index);
    void historyDetails(char* out, std::size_t capacity) const;
private:
    struct Hit { CodexMicroView* owner = nullptr; std::size_t index = 0; };
    static void touchEvent(lv_event_t* event);
    static void cellEvent(lv_event_t* event);
    static void modeEvent(lv_event_t* event);
    static void otaEvent(lv_event_t* event);
    static void otaBorderEvent(lv_event_t* event);
    bool otaKeepAwake() const;
    bool otaActionEnabled() const;
    bool otaHasDownloadOffer() const;
    void renderOtaAction();
    void initOta();
    void initSettings();
    void initSessions();
    void refreshSessionsLease();
    void refreshSessions(const CodexMicroState& state);
    void renderSessions();
    void renderSettings();
    void refreshDisplaySettings();
    void applyBurnInShift(bool touching);
    static void settingsEvent(lv_event_t* event);
    static void historyChartEvent(lv_event_t* event);
    void initLockSessions();
    void renderLockSessions();
    void requestLockedSessions();
    static void lockSessionBorderEvent(lv_event_t* event);
    lv_obj_t* pagePanel(Page page) const;
    void navigatePage(int direction);
    void cancelPageSlide();
    static void slideExec(void* owner, int32_t offset);
    static void slideCompleted(lv_anim_t* anim);
    void refreshOta(uint32_t tick);
    void renderOta();
    void refreshQuota(uint32_t now);
    void refreshHistory();
    void refreshBattery(uint32_t now);
    void refreshClock(uint32_t tick, bool force = false);
    void updateOrientation(bool touching);
    void cancelOrientation();
    void startOrientationFade(bool fadeIn);
    static void orientationExec(void* owner, int32_t opacity);
    static void orientationCompleted(lv_anim_t* anim);
    void updateAnimations(uint32_t tick);
    void stopAnimations();
    void updateHistoryHeader(bool force = false);
    void renderHistory();
    void renderSelection();
    void lockDisplay();
    const TokenHistoryCell* selectedCell() const;
    MosaicoOta::UiSnapshot _ota{};
    bool _otaSeen = false, _otaPending = false, _otaApproved = false;
    bool _externalPowerReady = false, _otaNetworkReady = false;
    Page _otaReturn = Page::Command;
    lv_obj_t* _otaPage = nullptr;
    lv_obj_t* _otaTitle = nullptr;
    lv_obj_t* _otaCurrent = nullptr;
    lv_obj_t* _otaTarget = nullptr;
    lv_obj_t* _otaPhase = nullptr;
    lv_obj_t* _otaStageIcon = nullptr;
    lv_obj_t* _otaPercent = nullptr;
    lv_obj_t* _otaMeter = nullptr;
    lv_obj_t* _otaBytes = nullptr;
    lv_obj_t* _otaHash = nullptr;
    lv_obj_t* _otaSignature = nullptr;
    lv_obj_t* _otaImageIcon = nullptr;
    lv_obj_t* _otaSignatureIcon = nullptr;
    lv_obj_t* _otaArrow = nullptr;
    std::array<lv_obj_t*, 2> _otaSlots{}, _otaSlotNames{}, _otaSlotNumbers{}, _otaChips{};
    lv_obj_t* _otaButton = nullptr;
    lv_obj_t* _otaButtonLabel = nullptr;
    lv_obj_t* _settingsPage = nullptr;
    lv_obj_t* _settingsStatus = nullptr;
    std::array<lv_obj_t*, 6> _settingsValues{};
    std::array<Hit, 12> _settingsHits{};
    MosaicoDisplay::Snapshot _displaySettings{};
    bool _settingsRequestFailed = false;
    bool _chargeProfile = false, _profileSeen = false;
    mosaico_charge::ChargeSupplyState _chargeSupply{};
    bool _shiftPending = false;
    unsigned _shiftIndex = 0;
    int _appliedBrightness = -1;
    lv_obj_t* _sessionsPage = nullptr;
    lv_obj_t* _sessionsCounts = nullptr;
    lv_obj_t* _sessionsLink = nullptr;
    std::array<lv_obj_t*,6> _sessionCards{}, _sessionNumbers{}, _sessionNames{}, _sessionAges{};
    CodexMicroState _sessionState{};
    MosaicoSessions::Snapshot _sessionBackend{};
    uint32_t _sessionsRevision = UINT32_MAX, _sessionsGeneration = UINT32_MAX, _sessionsAgeSecond = UINT32_MAX;
    uint8_t _sessionsKnownMask = 0;
    bool _sessionsHadStatus = false;
    lv_obj_t* _root = nullptr;
    lv_obj_t* _quotaPage = nullptr;
    lv_obj_t* _historyPage = nullptr;
    lv_obj_t* _overlay = nullptr;
    lv_obj_t* _lockPanel = nullptr;
    lv_obj_t* _lockClock = nullptr;
    lv_obj_t* _lockQuota = nullptr;
    lv_obj_t* _lockResetTime = nullptr;
    lv_obj_t* _lockResetIcon = nullptr;
    lv_obj_t* _lockBattery = nullptr;
    std::array<lv_obj_t*,6> _lockSessionCards{}, _lockSessionNumbers{};
    std::array<Hit,6> _lockSessionHits{};
    std::array<uint32_t,6> _lockSessionColors{};
    lv_obj_t* _lockSessionsAge = nullptr;
    uint32_t _lockSessionsRevision = UINT32_MAX;
    lv_obj_t* _battery = nullptr;
    lv_obj_t* _batteryIcon = nullptr;
    lv_obj_t* _wifiIcon = nullptr;
    lv_obj_t* _boltIcon = nullptr;
    lv_obj_t* _clockIcon = nullptr;
    lv_obj_t* _clockDate = nullptr;
    lv_obj_t* _rotationCurtain = nullptr;
    enum class RotationPhase : uint8_t { Idle, FadeOut, WaitBlack, WaitRotated, FadeIn };
    RotationPhase _rotationPhase = RotationPhase::Idle;
    bool _rotationFault = false; // Sticky: never expose a direction-mismatched touch UI.
    uint16_t _rotationTarget = 0;
    uint32_t _motionGeneration = UINT32_MAX, _rotationGeneration = 0, _rotationFrame = 0;
    uint32_t _clockReadTick = 0;
    int64_t _clockMinute = -2;
    lv_obj_t* _bucketCount = nullptr;
    lv_obj_t* _lockBatteryIcon = nullptr;
    lv_obj_t* _resetCount = nullptr;
    std::array<lv_obj_t*, 3> _resetIcons{};
    std::array<lv_obj_t*, 8> _creditIcons{}, _creditValues{};
    std::array<std::array<lv_obj_t*, 2>, 8> _resetTimes{}, _resetBars{}, _hourglassIcons{};
    lv_obj_t* _historyClock = nullptr;
    lv_obj_t* _historyAge = nullptr;
    lv_obj_t* _footer = nullptr;
    lv_obj_t* _quotaStatus = nullptr;
    std::array<lv_obj_t*, 8> _cards{};
    std::array<lv_obj_t*, 8> _cardTitles{}, _cardBadges{}, _cardValues{}, _cardMeta{}, _secondValues{};
    std::array<std::array<lv_obj_t*, 2>, 8> _quotaIcons{}, _windowBars{};
    std::array<lv_obj_t*, 30> _cells{}, _cellLabels{};
    std::array<Hit, 30> _hits{};
    std::array<Hit, 2> _modeHits{};
    std::array<lv_obj_t*, 2> _modeButtons{};
    lv_obj_t* _range = nullptr;
    lv_obj_t* _details = nullptr;
    lv_obj_t* _historyChart = nullptr;
    lv_obj_t* _trendHint = nullptr;
    uint32_t _trendAgeKey = UINT32_MAX;
    std::unique_ptr<QuotaMonitorSnapshot> _quota;
    std::unique_ptr<TokenHistorySnapshot> _history;
    Page _page = Page::Command;
    bool _hourly = false, _locked = false, _suppressed = false;
    bool _touchTracking = false, _swipeConsumed = false;
    lv_point_t _touchStart{};
    lv_obj_t* _slideFrom = nullptr;
    lv_obj_t* _slideTo = nullptr;
    int _slideDirection = 1;
    std::size_t _selected = SIZE_MAX;
    uint32_t _activity = 0, _refresh = 0, _lockRefreshCount = 0;
    uint32_t _quotaRevision = UINT32_MAX, _historyRevision = UINT32_MAX;
    uint32_t _historyAgeKey = UINT32_MAX;
    uint32_t _motionEpoch = 0, _motionTick = 0, _batteryReadTick = 0;
    bool _batterySeen = false, _batteryValid = false, _batteryCharging = false;
    int _brightness = 80;
};
}
