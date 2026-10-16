#pragma once
#include <cstdint>

// Manual, volatile, one-shot diagnostic only. No automatic policy, NVS or
// capacity/calibration/security/control-state correction is authorized here.
namespace mosaico_ocv {
struct Sample {
    uint16_t temperatureDeciK=0,voltageMv=0,batteryStatus=0,remainingMah=0,fcc=0,dc=0,soc=0,operation=0;
    int16_t currentMa=0,averageMa=0;
};
template<class Reader> constexpr bool readSnapshot(Reader read,Sample& out) {
    constexpr uint8_t regs[]={0x06,0x08,0x0A,0x0C,0x10,0x12,0x14,0x2C,0x3A,0x3C};
    uint16_t v[10]{};bool ok=true;
    for(unsigned i=0;i<10;++i)ok&=read(regs[i],v[i]);
    if(!ok)return false;
    out={v[0],v[1],v[2],v[4],v[5],v[9],v[7],v[8],static_cast<int16_t>(v[3]),static_cast<int16_t>(v[6])};
    return true;
}
constexpr bool eligible(const Sample& s) {
    return s.voltageMv>=4150&&s.voltageMv<=4350&&s.temperatureDeciK>=2832&&s.temperatureDeciK<=3181&&
        s.currentMa>=-3&&s.currentMa<=3&&s.averageMa>=-3&&s.averageMa<=3&&s.dc==65&&s.fcc==65&&
        s.soc<=100&&s.remainingMah<=s.fcc&&((s.operation>>1)&3)==3&&
        (s.operation&0x0020)&&!(s.operation&0x0401)&&(s.batteryStatus&0x0008)&&!(s.batteryStatus&0x2D02);
}
struct Qualification {
    static constexpr int64_t durationUs=120000000,maxGapUs=2500000;
    bool started=false,failed=false;
    int64_t firstUs=0,lastUs=0;
    uint16_t minMv=0,maxMv=0,minTemp=0,maxTemp=0;
    int16_t minCurrent=0,maxCurrent=0,minAverage=0,maxAverage=0;
    constexpr bool sample(const Sample& s,int64_t now) {
        if(failed)return false;
        if(now<0||!eligible(s)||(started&&(now<lastUs||now-lastUs>maxGapUs))) {failed=true;return false;}
        if(!started) {
            started=true;firstUs=now;
            minMv=maxMv=s.voltageMv;minTemp=maxTemp=s.temperatureDeciK;
            minCurrent=maxCurrent=s.currentMa;minAverage=maxAverage=s.averageMa;
        } else {
            minMv=s.voltageMv<minMv?s.voltageMv:minMv;maxMv=s.voltageMv>maxMv?s.voltageMv:maxMv;
            minTemp=s.temperatureDeciK<minTemp?s.temperatureDeciK:minTemp;maxTemp=s.temperatureDeciK>maxTemp?s.temperatureDeciK:maxTemp;
            minCurrent=s.currentMa<minCurrent?s.currentMa:minCurrent;maxCurrent=s.currentMa>maxCurrent?s.currentMa:maxCurrent;
            minAverage=s.averageMa<minAverage?s.averageMa:minAverage;maxAverage=s.averageMa>maxAverage?s.averageMa:maxAverage;
        }
        lastUs=now;
        if(maxMv-minMv>10||maxTemp-minTemp>5||maxCurrent-minCurrent>2||maxAverage-minAverage>2){failed=true;return false;}
        return true;
    }
    constexpr bool ready(int64_t now) const {
        return started&&!failed&&now>=lastUs&&now-lastUs<=maxGapUs&&now-firstUs>=durationUs;
    }
};
enum class SendResult : uint8_t { GuardRefused,AckFailed,Acked };
struct SendReport {SendResult result=SendResult::GuardRefused;Sample sample{};bool sampleValid=false;};
enum class Result : uint8_t { Rejected,AlreadyAttempted,GuardRefused,SentAckFailed,SentUnconfirmed,PostDisturbed,Confirmed };
struct Report {
    Result result=Result::Rejected;
    bool attempted=false,sent=false,ack=false,edge=false,conditions=false,beforeValid=false,afterValid=false;
    Sample before{},after{};Qualification range{};
};
// Backend snapshot/identity calls own only short battery locks; the outer HAL
// owns the transaction mutex. This class performs no implicit retry or write
// besides the single explicit command after qualification and volatile latch.
template<class Backend> class Coordinator {
    Backend& b;Report r{};Qualification q{};
    constexpr Report finish(Result value){r.result=value;r.range=q;return r;}
public:
    constexpr explicit Coordinator(Backend& backend):b(backend){}
    constexpr Report run() {
        if(b.wasAttempted()){r.attempted=true;return finish(Result::AlreadyAttempted);}
        if(!b.identity())return finish(Result::Rejected);
        for(;;) {
            Sample s{};const bool read=b.snapshot(s);const int64_t now=b.nowUs();
            if(read){r.before=s;r.beforeValid=true;}
            if(!read||!q.sample(s,now))return finish(Result::Rejected);
            if(q.ready(now))break;
            const int64_t left=Qualification::durationUs-(now-q.firstUs);
            b.waitMs(static_cast<uint32_t>(left>1000000?1000:(left+999)/1000));
        }
        // Reconfirm real device and all conditions immediately before the latch.
        if(!b.identity())return finish(Result::Rejected);
        Sample fresh{};const bool read=b.snapshot(fresh);const int64_t checked=b.nowUs();
        if(read){r.before=fresh;r.beforeValid=true;}
        if(!read||!q.sample(fresh,checked)||!q.ready(checked))return finish(Result::Rejected);
        if(!b.latchAttempt()){r.attempted=true;return finish(Result::AlreadyAttempted);}
        r.attempted=true;
        const auto command=b.sendOcv(q);
        if(command.sampleValid){r.before=command.sample;r.beforeValid=true;}
        r.sent=command.result!=SendResult::GuardRefused;r.ack=command.result==SendResult::Acked;
        if(!r.sent)return finish(Result::GuardRefused);
        const int64_t postStart=b.nowUs();bool postClear=false;
        for(;;) {
            Sample post{};const bool observed=b.snapshot(post);const int64_t now=b.nowUs();
            if(observed) {
                r.after=post;r.afterValid=true;
                // Only an actual POST-send clear -> set edge counts. Neither
                // stale COMP=1 nor pre-send COMP=0 establishes completion.
                if(!(post.batteryStatus&0x4000))postClear=true;
                else if(postClear)r.edge=true;
            }
            if(!observed||!q.sample(post,now))return finish(Result::PostDisturbed);
            if(now-postStart>=5000000)break;
            const int64_t left=5000000-(now-postStart);
            b.waitMs(static_cast<uint32_t>(left>1000000?1000:(left+999)/1000));
        }
        r.conditions=true;
        return finish(!r.ack?Result::SentAckFailed:(r.edge&&(r.after.batteryStatus&0x0020)?Result::Confirmed:Result::SentUnconfirmed));
    }
};
constexpr const char* resultName(Result r) {
    switch(r) {
        case Result::AlreadyAttempted:return "REJECTED_ALREADY_ATTEMPTED";
        case Result::GuardRefused:return "REJECTED_WIRE_GUARD_NO_PACKET";
        case Result::SentAckFailed:return "SENT_ACK_FAILED_UNCONFIRMED";
        case Result::SentUnconfirmed:return "SENT_OBSERVED_UNCONFIRMED";
        case Result::PostDisturbed:return "SENT_POST_CONDITIONS_VIOLATED";
        case Result::Confirmed:return "CONFIRMED_POST_CLEAR_SET_EDGE";
        default:return "REJECTED_NO_WRITE";
    }
}
} // namespace mosaico_ocv
