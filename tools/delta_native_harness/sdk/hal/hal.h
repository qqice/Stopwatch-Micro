#pragma once
#include <cassert>
#include <esp_timer.h>
extern bool pendingIoYield;
struct Hal {
    bool held=false;
    unsigned begins=0,ends=0,failBegin=0;
    bool beginDisplayFlashWindow(int64_t deadlineUs) {
        assert(!held&&!pendingIoYield);assert(deadlineUs==1000000);++begins;
        if(fakeTime>=deadlineUs)return false;
        if(failBegin==begins || failBegin==~0U)return false;
        held=true;return true;
    }
    void endDisplayFlashWindow() {assert(held);held=false;++ends;}
};
inline Hal& GetHAL() {static Hal hal;return hal;}
