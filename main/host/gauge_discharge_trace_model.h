#pragma once
#include <array>
#include <cstddef>
#include <cstdint>

namespace GaugeDischargeTrace {
constexpr size_t Capacity=64, RecordSize=48, BlobSize=32+Capacity*RecordSize+4;
using Blob=std::array<uint8_t,BlobSize>;
enum Flags : uint16_t { Valid=1, CapacityValid=2, TemperatureValid=4, EpochValid=8, NominalConfigured=16, RawCoulombValid=32 };
struct Sample {
    uint32_t boot=0;
    uint64_t monotonicMs=0, epoch=0;
    uint16_t flags=0, soc=0, voltageMv=0;
    int16_t currentMa=0, averageCurrentMa=0;
    uint16_t remainingMah=0, fullMah=0, designMah=0, operationStatus=0,
             batteryStatus=0, stateOfHealth=0, cycleCount=0, temperatureDeciK=0, rawCoulombMah=0;
};
constexpr bool low(const Sample& s) { return (s.flags&Valid) && (s.soc<=15 || s.voltageMv<=3500); }
constexpr bool writable(const Sample& s) { return (s.flags&Valid) && s.voltageMv>=3300; }
struct Model {
    std::array<Sample,Capacity> rows{};
    size_t head=0,count=0;
    bool armed=false, lowSeen=false, finalizedChargeAfterLow=false;
    uint8_t chargingSamples=0;
    uint32_t boot=1;
    bool sampled=false, lastLow=false, checkpointed=false, dirty=false;
    uint64_t lastSampleMs=0, lastCheckpointMs=0;
    constexpr const Sample& at(size_t i) const { return rows[(head+Capacity-count+i)%Capacity]; }
    constexpr bool due(uint64_t now) const {
        return armed && (!sampled || now-lastSampleMs >= (lastLow?10000ULL:60000ULL));
    }
    constexpr void append(Sample s) {
        s.boot=boot;rows[head]=s;head=(head+1)%Capacity;if(count<Capacity)++count;
        if(s.flags&Valid){
            if((s.soc<=5 || s.voltageMv<=3500) && (s.currentMa<0 || s.averageCurrentMa<0))lowSeen=true;
            if(lowSeen && s.currentMa>5){
                if(chargingSamples<2)++chargingSamples;
                if(chargingSamples>=2){armed=false;finalizedChargeAfterLow=true;}
            }else chargingSamples=0;
        }else chargingSamples=0;
        sampled=true;lastSampleMs=s.monotonicMs;if(s.flags&Valid)lastLow=low(s);dirty=true;
    }
    constexpr bool checkpointDue(uint64_t now) const {
        return dirty && lastLow && (!checkpointed || now-lastCheckpointMs>=60000ULL);
    }
    constexpr void attempted(uint64_t now) { checkpointed=true;lastCheckpointMs=now; }
};
constexpr void put(Blob& b,size_t p,uint64_t v,size_t n) { for(size_t i=0;i<n;++i)b[p+i]=uint8_t(v>>(8*i)); }
constexpr uint64_t get(const Blob& b,size_t p,size_t n) { uint64_t v=0;for(size_t i=0;i<n;++i)v|=uint64_t(b[p+i])<<(8*i);return v; }
constexpr uint32_t crc(const Blob& b) {
    uint32_t c=0xffffffffU;
    for(size_t i=0;i<BlobSize-4;++i){ c^=b[i];for(unsigned j=0;j<8;++j)c=(c>>1)^(0xedb88320U&uint32_t(-int32_t(c&1))); }
    return ~c;
}
constexpr void encodeInto(const Model& m,Blob& b) {
    bool raw=false;for(size_t i=0;i<m.count;++i)raw|=bool(m.at(i).flags&RawCoulombValid);
    for(auto& byte:b)byte=0;
    put(b,0,0x31544447,4);put(b,4,raw?2:1,2);put(b,6,RecordSize,2);
    put(b,8,m.count,2);put(b,10,m.armed,1);put(b,11,(m.lowSeen?1:0)|(m.finalizedChargeAfterLow?2:0),1);put(b,12,m.boot,4);
    for(size_t i=0;i<m.count;++i){const auto& s=m.at(i);size_t p=32+i*RecordSize;
        put(b,p,s.boot,4);put(b,p+4,s.monotonicMs,8);put(b,p+12,s.epoch,8);put(b,p+20,s.flags,2);put(b,p+22,s.soc,2);
        const uint16_t v[]={s.voltageMv,uint16_t(s.currentMa),uint16_t(s.averageCurrentMa),s.remainingMah,s.fullMah,s.designMah,s.operationStatus,s.batteryStatus,s.stateOfHealth,s.cycleCount,s.temperatureDeciK};
        for(size_t j=0;j<11;++j)put(b,p+24+2*j,v[j],2);
        if(s.flags&RawCoulombValid)put(b,p+46,s.rawCoulombMah,2);
    }
    put(b,BlobSize-4,crc(b),4);
}
constexpr Blob encode(const Model& m) {Blob b{};encodeInto(m,b);return b;}
constexpr bool decode(const Blob& b,Model& out) {
    if(get(b,0,4)!=0x31544447 || (get(b,4,2)!=1 && get(b,4,2)!=2) || get(b,6,2)!=RecordSize || get(b,8,2)>Capacity || b[10]>1 || get(b,BlobSize-4,4)!=crc(b))return false;
    Model m;m.armed=b[10];m.lowSeen=b[11]&1;m.finalizedChargeAfterLow=b[11]&2;if(b[11]>3 || (m.finalizedChargeAfterLow && (!m.lowSeen || m.armed)))return false;m.boot=uint32_t(get(b,12,4));
    if(!m.boot)return false;
    const size_t count=size_t(get(b,8,2));
    for(size_t i=0;i<count;++i){size_t p=32+i*RecordSize;Sample s;
        s.boot=uint32_t(get(b,p,4));s.monotonicMs=get(b,p+4,8);s.epoch=get(b,p+12,8);s.flags=uint16_t(get(b,p+20,2));s.soc=uint16_t(get(b,p+22,2));
        if(!s.boot || s.boot>m.boot || (s.flags&~(get(b,4,2)==2?63U:31U)))return false;
        s.voltageMv=uint16_t(get(b,p+24,2));s.currentMa=int16_t(get(b,p+26,2));s.averageCurrentMa=int16_t(get(b,p+28,2));
        s.remainingMah=uint16_t(get(b,p+30,2));s.fullMah=uint16_t(get(b,p+32,2));s.designMah=uint16_t(get(b,p+34,2));
        s.operationStatus=uint16_t(get(b,p+36,2));s.batteryStatus=uint16_t(get(b,p+38,2));s.stateOfHealth=uint16_t(get(b,p+40,2));s.cycleCount=uint16_t(get(b,p+42,2));s.temperatureDeciK=uint16_t(get(b,p+44,2));s.rawCoulombMah=uint16_t(get(b,p+46,2));
        if(i && (s.boot<m.rows[i-1].boot || (s.boot==m.rows[i-1].boot && s.monotonicMs<m.rows[i-1].monotonicMs)))return false;
        m.rows[i]=s;
    }
    m.count=count;m.head=count%Capacity;
    // Reject noncanonical reserved bytes, padding, unused records, and invalid booleans.
    for(size_t i=16;i<32;++i)if(b[i])return false;
    for(size_t i=0;i<count;++i)if(!(m.rows[i].flags&RawCoulombValid) && (b[32+i*RecordSize+46] || b[32+i*RecordSize+47]))return false;
    for(size_t i=32+count*RecordSize;i<BlobSize-4;++i)if(b[i])return false;
    out=m;return true;
}
// Expected is produced by encode(); exact comparison also rejects noncanonical bytes.
constexpr bool verifiedReadback(const Blob& expected,const Blob& actual,size_t size) {
    if(size!=BlobSize || get(actual,BlobSize-4,4)!=crc(actual))return false;
    for(size_t i=0;i<BlobSize;++i)if(expected[i]!=actual[i])return false;
    return true;
}
static_assert(BlobSize<=4096,"bounded NVS trace");
}
