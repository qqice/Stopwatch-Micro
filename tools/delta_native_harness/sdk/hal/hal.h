#pragma once
#include <cassert>
struct Hal {
    bool held=false;
    unsigned begins=0,ends=0,failBegin=0;
    bool beginDisplayFlashWindow() {
        assert(!held);++begins;
        if(failBegin==begins || failBegin==~0U)return false;
        held=true;return true;
    }
    void endDisplayFlashWindow() {assert(held);held=false;++ends;}
};
inline Hal& GetHAL() {static Hal hal;return hal;}
