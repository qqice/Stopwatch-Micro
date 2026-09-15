/*
 * SPDX-License-Identifier: MIT
 */
#pragma once

#include <array>
#include <cstddef>
#include <cstdint>

#include <hal/ble/codex_micro_ble.h>
#include <lvgl.h>

namespace view {

class CodexMicroView {
public:
    enum class Page : uint8_t {
        Command = 0,
        Agent,
    };

    ~CodexMicroView();

    void init(lv_obj_t* parent);
    void update(const CodexMicroState& state);
    void togglePage();
    void setMicActive(bool active);
    bool ready() const;
    bool functionalEnabled() const;
    bool micActive() const;
    Page currentPage() const;
    bool setPageForDebug(Page page);
    void setInputSuppressed(bool suppressed);
    void wakeDisplay();
    bool locked() const;
    bool lockForDebug();
    uint32_t lockRefreshCount() const;

private:
    enum class DisplayPowerState : uint8_t {
        Active,
        Locked,
    };

    enum class Icon : uint8_t {
        Fast,
        Approve,
        Decline,
        NewChat,
        Mic,
        Fingerprint,
    };

    enum class CommandAction : uint8_t {
        Key,
        Plan,
    };

    struct KeyContext {
        CodexMicroView* owner     = nullptr;
        CodexMicroControl control = CodexMicroControl::Agent1;
        int8_t agent              = -1;
        bool active               = false;
    };

    struct IconContext {
        CodexMicroView* owner = nullptr;
        Icon icon             = Icon::Fast;
    };

    struct CommandContext {
        CodexMicroView* owner     = nullptr;
        CommandAction action      = CommandAction::Key;
        CodexMicroControl control = CodexMicroControl::Fast;
        std::size_t slot          = 0;
        bool active               = false;
    };

    static void keyEvent(lv_event_t* event);
    static void commandEvent(lv_event_t* event);
    static void iconEvent(lv_event_t* event);
    static void dialTrackEvent(lv_event_t* event);
    static void dialHitTestEvent(lv_event_t* event);
    static void dialEvent(lv_event_t* event);
    static void touchEvent(lv_event_t* event);
    static void wakeOverlayEvent(lv_event_t* event);

    void setPage(Page page);
    void renderPage();
    void renderCommand(lv_obj_t* parent);
    void renderNavigation(lv_obj_t* parent);
    void renderCenterStatus(lv_obj_t* parent);
    void renderAgent(lv_obj_t* parent);
    void releaseActiveInputs();

    lv_obj_t* createPageRoot();
    lv_obj_t* createKeyButton(lv_obj_t* parent, std::array<lv_obj_t*, 6>& buttons, std::array<KeyContext, 6>& contexts,
                              std::size_t slot, int x, int y, int width, int height, CodexMicroControl control,
                              int8_t agent, uint32_t background, uint32_t border, int radius);
    void createCommandButton(lv_obj_t* parent, std::size_t slot, int x, int y, const char* label, CommandAction action,
                             CodexMicroControl control);
    void stylePanel(lv_obj_t* object, uint32_t background, uint32_t border, int radius, int border_width = 1);
    void updateConnection(const CodexMicroState& state);
    void updateAmbientLighting(const CodexMicroLight& light);
    void updateCommandLighting(const CodexMicroState& state);
    void updateCenterStatus(const CodexMicroState& state);
    void updateAgentLights(const CodexMicroState& state);
    void updateMicMeter();
    void updateDisplayPower(uint32_t tick);
    void setDisplayPower(DisplayPowerState state);
    void lockDisplay();
    void updateLockedScreen(const CodexMicroState& state, uint32_t tick, bool force);
    bool interactionActive() const;
    void invalidateCommandButton(std::size_t slot);
    void setDialVisualStep(float step);
    void updateDialFromPoint(const lv_point_t& point);
    void beginDialReturn();
    void updateDialReturn();
    void resetDial();
    void releaseDialGesture();

    lv_obj_t* _root                           = nullptr;
    std::array<lv_obj_t*, 14> _ambient_layers = {};
    lv_obj_t* _touch_control                  = nullptr;
    lv_obj_t* _mic_screen                     = nullptr;
    lv_obj_t* _pairing_screen                 = nullptr;
    lv_obj_t* _offline_screen                 = nullptr;
    lv_obj_t* _offline_label                  = nullptr;
    uint32_t _offline_update_tick             = 0;
    lv_obj_t* _pairing_pulse                  = nullptr;
    lv_obj_t* _pairing_core                   = nullptr;
    lv_obj_t* _pairing_reset_control          = nullptr;
    lv_obj_t* _wake_overlay                   = nullptr;
    lv_obj_t* _lock_screen                    = nullptr;
    lv_obj_t* _lock_quota_label               = nullptr;
    lv_obj_t* _lock_battery_label             = nullptr;
    lv_obj_t* _usage_card                     = nullptr;
    lv_obj_t* _usage_status_label             = nullptr;
    lv_obj_t* _usage_value_label              = nullptr;
    lv_obj_t* _usage_bar                      = nullptr;
    lv_obj_t* _usage_reset_label              = nullptr;
    lv_obj_t* _battery_label                  = nullptr;
    std::array<lv_obj_t*, 9> _mic_bars        = {};
    std::array<lv_obj_t*, 3> _pairing_dots    = {};
    std::array<lv_obj_t*, 2> _page_roots      = {};

    std::array<lv_obj_t*, 6> _command_buttons       = {};
    std::array<CommandContext, 6> _command_contexts = {};
    std::array<bool, 6> _command_lit                = {};
    std::array<uint32_t, 6> _command_light_colors   = {};
    std::array<lv_obj_t*, 6> _agent_buttons         = {};
    std::array<lv_obj_t*, 6> _agent_labels          = {};
    std::array<lv_obj_t*, 6> _agent_dots            = {};
    std::array<KeyContext, 6> _agent_contexts       = {};
    std::array<IconContext, 8> _icon_contexts       = {};

    lv_obj_t* _dial                   = nullptr;
    lv_obj_t* _dial_thumb             = nullptr;
    bool _dial_pressed                = false;
    bool _dial_rotating               = false;
    bool _dial_returning              = false;
    bool _dial_host_press_active      = false;
    int _dial_step                    = 10;
    float _dial_visual_step           = 10.0f;
    float _dial_return_start_step     = 10.0f;
    uint32_t _dial_press_tick         = 0;
    uint32_t _dial_release_tick       = 0;
    uint32_t _dial_return_tick        = 0;
    uint32_t _dial_last_feedback_tick = 0;

    bool _touch_pressed                      = false;
    bool _mic_active                         = false;
    uint32_t _mic_last_update_tick           = 0;
    uint32_t _command_last_update_tick       = 0;
    uint32_t _agent_last_update_tick         = 0;
    uint32_t _center_status_last_update_tick = 0;
    uint32_t _last_host_bridge_revision      = UINT32_MAX;
    uint8_t _last_status_battery             = UINT8_MAX;
    bool _last_status_charging               = false;
    CodexMicroLight _ambient_light           = {};
    uint32_t _ambient_base_color             = UINT32_MAX;
    uint8_t _ambient_base_brightness         = UINT8_MAX;
    bool _ambient_visible                    = false;
    uint32_t _touch_press_tick               = 0;
    uint32_t _last_state_revision            = UINT32_MAX;
    int8_t _last_connection_phase            = -1;
    bool _functional_enabled                 = false;
    bool _input_suppressed                   = false;
    bool _wake_overlay_armed                 = false;
    DisplayPowerState _display_power         = DisplayPowerState::Active;
    uint32_t _last_activity_tick             = 0;
    uint32_t _lock_last_refresh_tick         = 0;
    uint32_t _lock_refresh_count             = 0;
    bool _locked                             = false;
    uint8_t _pixel_shift_index               = UINT8_MAX;
    int _display_base_brightness             = 80;
    bool _page_dirty                         = true;
    Page _page                               = Page::Command;
};

}  // namespace view
