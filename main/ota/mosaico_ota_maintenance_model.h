#pragma once
#include <cstdint>
#include <cstring>
namespace MosaicoOtaMaintenance {
inline bool shaValid(const char* s) {
    if (!s || std::strlen(s) != 64) return false;
    for (unsigned i=0;i<64;++i) if (!((s[i]>='0' && s[i]<='9') || (s[i]>='a' && s[i]<='f'))) return false;
    return true;
}
// RAM-only, one boot / one explicitly confirmed USB transaction. No persistence.
// The old 30..600 argument is syntax-compatible only, not an in-flight expiry.
struct Grant {
    bool enabled=false;
    char sha[65]{};
    int64_t lastUs=0;
    uint32_t runningAddress=0, bootAddress=0, unmountEpoch=0;
    void clear() { enabled=false; }
    bool matches(const char* s) const { return shaValid(s) && !std::strcmp(sha,s); }
    bool start(const char* s,uint32_t ttl,int64_t now,uint32_t running,uint32_t boot,uint32_t epoch=0) {
        clear();
        if (!shaValid(s) || ttl<30 || ttl>600 || now<0) return false;
        std::memcpy(sha,s,65); lastUs=now;
        runningAddress=running; bootAddress=boot; unmountEpoch=epoch; enabled=true; return true;
    }
    bool check(int64_t now,bool usb,uint32_t running,uint32_t boot,uint32_t expectedBoot,uint32_t epoch=0) {
        if (!enabled || now<lastUs || !usb || running!=runningAddress || boot!=expectedBoot || epoch!=unmountEpoch) {
            clear(); return false;
        }
        lastUs=now; return true;
    }
};
template<class Snapshot> bool powerSafe(const Snapshot& s,int64_t now) {
    return s.valid && s.sampledUs>0 && now>=s.sampledUs && now-s.sampledUs<=2000000 &&
        s.voltageMv>=3900 && s.voltageMv<=4350 && s.temperatureDeciK>=2832 && s.temperatureDeciK<=3131 &&
        ((s.operationStatus>>1)&3)==3 && !(s.operationStatus&0x0401) && (s.operationStatus&0x0020) &&
        ((s.designMah==65 && s.fullMah==65) || (s.designMah==3000 && s.fullMah==3000)) &&
        s.currentMa>=-130 && s.currentMa<=130 && s.averageCurrentMa>=-130 && s.averageCurrentMa<=130;
}
inline bool refreshDue(bool force,int64_t now,int64_t last,uint32_t end,uint32_t previous) {
    return force || now<last || now-last>=1000000 || end<previous || end-previous>=65536;
}
}
