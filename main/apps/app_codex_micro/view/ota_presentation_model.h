#pragma once
#include <ota/mosaico_ota.h>
#include <cstdint>

// Presentation only: no requests, timing, power or installation decisions.
namespace mosaico_ota_view {
using Stage = MosaicoOta::UiStage;
using Work = MosaicoOta::WorkPhase;
enum class Screen : uint8_t {
    Current, Checking, Offer, Preparing, Negotiating, Download, Merge, Verify,
    Ready, Installing, Reboot, BootCheck, Complete, Failed
};
struct Rect { int x=0, y=0, w=0, h=0; };
struct Model {
    Screen screen=Screen::Current;
    const char* heading="CURRENT";
    uint32_t color=0x68C9D6;
    bool title=true, loader=false, numeric=false, current=false, target=false;
    bool targetIsCurrent=false, mode=false, bytes=false, ratio=false, hash=false, signature=false;
    bool error=false;
    unsigned slots=0;
    uint32_t done=0, total=0;
    uint16_t bp=0;
    Rect phase{184,18,256,26}, badge{308,0,132,32};
    Rect currentBox{0,64,440,34}, targetBox{0,54,440,34};
    Rect activity{176,136,88,88}, percent{0,122,440,60}, meter{0,198,440,28};
    Rect byteBox{0,238,440,24}, hashBox{36,238,184,24}, signatureBox{272,238,168,24};
    Rect imageIcon{0,236,28,28}, signatureIcon{236,236,28,28};
    Rect slot[2]{{0,276,186,48},{254,276,186,48}}, arrow{202,282,36,36};
    Rect action{0,334,440,64};
};
constexpr bool known(const char* s) { return s && *s && *s!='-'; }
constexpr bool candidate(const MosaicoOta::UiSnapshot& s) { return s.signatureVerified&&known(s.sha256)&&known(s.targetVersion); }
constexpr uint16_t fraction(uint32_t done,uint32_t total) {
    return !total ? 0 : done>=total ? 10000 : static_cast<uint16_t>(uint64_t(done)*10000/total);
}
constexpr bool active(Stage s) { return s==Stage::Downloading || s==Stage::Verifying; }
constexpr Screen screen(const MosaicoOta::UiSnapshot& s) {
    if(active(s.stage)) {
        switch(s.workPhase) {
        case Work::Preparing:return Screen::Preparing;
        case Work::Negotiating:return Screen::Negotiating;
        case Work::Downloading:return Screen::Download;
        case Work::Merging:return Screen::Merge;
        case Work::Verifying:return Screen::Verify;
        default:return s.stage==Stage::Downloading ? Screen::Download : Screen::Verify;
        }
    }
    switch(s.stage) {
    case Stage::Checking:return Screen::Checking;
    case Stage::Available:return Screen::Offer;
    case Stage::WaitingPower:return s.imageVerified ? Screen::Ready : s.signatureVerified&&known(s.sha256) ? Screen::Offer : Screen::Current;
    case Stage::ReadyInstall:return Screen::Ready;
    case Stage::Installing:return Screen::Installing;
    case Stage::ReadyReboot:return Screen::Reboot;
    case Stage::BootChecking:return Screen::BootCheck;
    case Stage::Complete:return Screen::Complete;
    case Stage::Failed:return Screen::Failed;
    default:return Screen::Current;
    }
}
constexpr Model present(const MosaicoOta::UiSnapshot& s) {
    Model m; m.screen=screen(s);
    m.target=candidate(s);
    m.current=known(s.currentVersion);
    switch(m.screen) {
    case Screen::Current:m.target=false;break;
    case Screen::Checking:m.heading="CHECKING";m.loader=true;m.target=false;break;
    case Screen::Offer:m.heading=s.stage==Stage::WaitingPower ? "DOWNLOAD BLOCKED" : "UPDATE";break;
    case Screen::Preparing:m.heading="PREPARING";m.title=false;m.loader=true;break;
    case Screen::Negotiating:m.heading="DELTA PLAN";m.title=false;m.loader=true;break;
    case Screen::Download:
        m.heading="DOWNLOADING";m.color=0x67E7AE;m.title=false;m.numeric=true;m.bytes=true;m.ratio=true;
        m.done=s.deltaTransfer?s.transferReceived:s.received;m.total=s.deltaTransfer?s.transferSize:s.size;break;
    case Screen::Merge:
        m.heading="MERGING";m.title=false;m.numeric=true;m.bytes=true;m.ratio=true;
        m.done=s.received;m.total=s.size;break;
    case Screen::Verify:
        m.heading="VERIFYING";m.color=0xE9C46A;m.title=false;m.loader=true;m.bytes=true;
        m.hash=known(s.sha256);m.byteBox={280,238,160,24};break;
    case Screen::Ready:
        m.heading=s.stage==Stage::WaitingPower ? "POWER" : "READY";m.color=0x67E7AE;m.title=false;
        m.activity={176,106,88,88};m.bytes=true;m.byteBox={0,200,440,24};m.hash=known(s.sha256);m.signature=known(s.signatureShort);break;
    case Screen::Installing:m.heading="INSTALLING";m.color=0xE9C46A;m.title=false;m.loader=true;break;
    case Screen::Reboot:m.heading="REBOOT";m.color=0xE9C46A;m.title=false;break;
    case Screen::BootCheck:
        m.heading="BOOT CHECK";m.title=false;m.numeric=true;m.bp=s.progressBasisPoints>10000?10000:s.progressBasisPoints;
        m.targetIsCurrent=true;break;
    case Screen::Complete:
        m.heading="VERIFIED";m.color=0x67E7AE;m.targetIsCurrent=true;m.targetBox={0,64,440,34};m.activity={176,124,88,88};
        m.hash=known(s.sha256);m.signature=known(s.signatureShort);break;
    case Screen::Failed:m.heading="FAILED";m.color=0xD18C37;m.title=false;m.error=true;break;
    }
    if(!m.title)m.phase={0,6,296,26};
    if(m.screen==Screen::Current)m.currentBox={0,90,440,34};
    if(m.screen==Screen::Offer) {
        m.currentBox={0,64,440,24};m.targetBox={0,108,440,34};
        m.hash=known(s.sha256);m.signature=known(s.signatureShort);
        m.hashBox.y=m.signatureBox.y=172;m.imageIcon.y=m.signatureIcon.y=170;
        m.slot[0]={0,226,186,96};m.slot[1]={254,226,186,96};m.arrow.y=256;
    }
    if(m.targetIsCurrent) { m.target=m.current;m.current=false; }
    else if(m.target && m.screen!=Screen::Offer && m.screen!=Screen::Preparing && m.screen!=Screen::Negotiating)m.current=false;
    if(m.target && m.current && m.screen!=Screen::Offer)m.currentBox={0,94,440,24};
    if(m.error) {m.current=known(s.currentVersion);m.currentBox={0,64,440,28};m.target=true;m.targetBox={0,118,440,32};}
    m.mode=s.modeKnown && m.screen!=Screen::Current && m.screen!=Screen::Checking && m.screen!=Screen::Offer &&
        m.screen!=Screen::Preparing && m.screen!=Screen::Negotiating && m.screen!=Screen::Complete;
    if(m.bytes && !m.total)m.total=s.size;
    if(m.numeric && m.screen!=Screen::BootCheck)m.bp=fraction(m.done,m.total);
    const bool currentSlot=known(s.currentVersion)&&s.currentSlot>=0&&s.currentSlot<=1;
    const bool transition=currentSlot&&s.targetSlot>=0&&s.targetSlot<=1&&s.currentSlot!=s.targetSlot;
    m.slots=currentSlot?1U:0U;
    if(transition && m.screen!=Screen::Current && m.screen!=Screen::Checking && m.screen!=Screen::BootCheck &&
       m.screen!=Screen::Complete && m.screen!=Screen::Failed)m.slots=2;
    if(m.slots==1) {m.slot[0].x=127;if(m.screen==Screen::Current){m.slot[0].y=194;m.slot[0].h=96;}}
    return m;
}
constexpr bool bounds(Rect a) { return a.w>=0&&a.h>=0&&a.x>=0&&a.y>=0&&a.x+a.w<=440&&a.y+a.h<=402; }
constexpr bool overlaps(Rect a,Rect b) {return a.w&&a.h&&b.w&&b.h&&a.x<b.x+b.w&&b.x<a.x+a.w&&a.y<b.y+b.h&&b.y<a.y+a.h;}
}
