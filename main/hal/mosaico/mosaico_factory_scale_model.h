#pragma once
#include "mosaico_gauge_recovery_model.h"

// OFFLINE model only. Not included by HAL and supplies no device/NVS writer.
// Scale::Two names the TARGET's nominal mapping, never the factory baseline.
namespace mosaico_factory_scale {
namespace recovery=mosaico_gauge_recovery;
inline constexpr bool readyForHardware=false;
inline constexpr uint32_t factorySourceCrc=0x70a767beU;
inline constexpr uint32_t candidateTargetCrc=0xb5b0f0d6U;
inline constexpr uint8_t schema=3;
enum class Profile:uint8_t {Unknown,Factory3000RollbackToNominal65Virtual130};
enum class Units:uint8_t {Unknown,ExperimentalVirtualTwo};
struct Envelope {
    uint8_t version=0;
    Profile profile=Profile::Unknown;
    uint32_t sourceCrc=0,targetCrc=0;
    // These are external verification requirements, not proofs manufactured by
    // this model. The future wire schema must bind hashes, profile and epoch.
    bool completeImageHashesVerified=false;
    bool bothActualSignedImagesSupportProfile=false;
};
struct Result {
    recovery::Decision decision{};
    Units units=Units::Unknown;
    bool capacityCalibrated=false,nativeWaiver=false,clearHistoricalFailure=false;
};
struct Change {uint16_t address;uint8_t width;uint32_t before,after;};
inline constexpr std::array<Change,17> changes{{
    {0x9184,4,0x7e73b645,0x7f73b645},{0x9188,4,0x930aa522,0x940aa522},
    {0x91de,1,5,10},{0x91fb,2,200,400},{0x9201,2,100,200},
    {0x920e,2,150,300},{0x9210,2,175,350},{0x9217,2,10,20},
    {0x9228,2,60,120},{0x922a,2,75,150},{0x922c,2,40,80},
    {0x923c,1,0xf6,0xec},{0x9264,2,1500,3000},{0x926b,2,200,400},
    {0x929d,2,3000,130},{0x929f,2,3000,130},{0x92ab,2,867,434}
}};
constexpr uint32_t raw(const uint8_t (&bytes)[4],unsigned width){
    uint32_t value=0;for(unsigned i=0;i<width;++i){value=(value<<8)|bytes[i];}return value;
}
// Same ordered 917-byte public131 payload as the immutable factory backup.
constexpr uint32_t digest(const recovery::Snapshot& s,bool target){
    uint32_t crc=0xffffffffU;
    for(const auto& field:s.fields){
        for(unsigned i=0;i<7;++i){
            const uint8_t v=i<2?uint8_t(field.address>>(8*i)):i==2?field.width:
                (target?field.target[i-3]:field.original[i-3]);
            crc^=v;for(unsigned b=0;b<8;++b){crc=(crc>>1)^((crc&1)?0xedb88320U:0);}
        }
    }
    return ~crc;
}
// Pure immutable image validation; no external cryptographic proof is implied.
constexpr bool closedImageTemplate(const recovery::Snapshot& s){
    if(s.scale!=recovery::Scale::Two||s.count!=recovery::fieldCount){return false;}
    unsigned changed=0;bool cfg=false,offset=false;
    for(size_t i=0;i<recovery::fieldCount;++i){
        const auto& f=s.fields[i];
        if(f.address!=recovery::layout[i].address||f.width!=recovery::layout[i].width){return false;}
        const Change* owned=nullptr;
        for(const auto& c:changes){if(c.address==f.address){owned=&c;}}
        if(owned){
            if(f.width!=owned->width||raw(f.original,f.width)!=owned->before||raw(f.target,f.width)!=owned->after){return false;}
            ++changed;
        }else{
            for(unsigned j=0;j<4;++j){if(f.original[j]!=f.target[j]){return false;}}
        }
        for(unsigned j=f.width;j<4;++j){if(f.original[j]||f.target[j]){return false;}}
        if(f.address==0x929b){cfg=raw(f.original,f.width)==0x102a;}
        if(f.address==0x9180){offset=raw(f.original,f.width)==0xfff0;}
    }
    return changed==17&&cfg&&offset&&digest(s,false)==factorySourceCrc&&digest(s,true)==candidateTargetCrc;
}
constexpr bool closedProfile(const Envelope& e,const recovery::Snapshot& s){
    if(e.version!=schema||e.profile!=Profile::Factory3000RollbackToNominal65Virtual130||
       !e.completeImageHashesVerified||!e.bothActualSignedImagesSupportProfile||
       e.sourceCrc!=factorySourceCrc||e.targetCrc!=candidateTargetCrc||s.scale!=recovery::Scale::Two||
       s.count!=recovery::fieldCount){return false;}
    return closedImageTemplate(s);
}

constexpr Result reject(){return {};}
constexpr Result wrap(recovery::Decision d){
    d.publishPhysicalTelemetry=false;d.parameterWritesAuthorized=false;
    return {d,Units::Unknown,false,false,false};
}
constexpr Result startup(const Envelope& e,const recovery::Snapshot& s,const recovery::Context& c,const recovery::Observation& o){
    return closedProfile(e,s)?wrap(recovery::startup(s,c,o)):reject();
}
constexpr Result apply(const Envelope& e,const recovery::Snapshot& s,const recovery::Context& c,const recovery::Observation& o,const recovery::DurableProof& p,uint64_t next){
    return closedProfile(e,s)?wrap(recovery::apply(s,c,o,p,next)):reject();
}
constexpr Result restore(const Envelope& e,const recovery::Snapshot& s,const recovery::Context& c,const recovery::Observation& o,const recovery::DurableProof& p,uint64_t next){
    // Unknown/interrupted CFG is not a license for blind writes or exit/reset.
    return closedProfile(e,s)&&o.sealed&&o.cfg0?wrap(recovery::restore(s,c,o,p,next)):reject();
}
constexpr Result finishRestore(const Envelope& e,const recovery::Snapshot& s,const recovery::Context& c,const recovery::Observation& o,const recovery::DurableProof& p,uint64_t next){
    return closedProfile(e,s)?wrap(recovery::finishRestore(s,c,o,p,next)):reject();
}
constexpr Result finishApply(const Envelope& e,const recovery::Snapshot& s,const recovery::Context& c,const recovery::Observation& o,const recovery::DurableProof& p,uint64_t next){
    if(!closedProfile(e,s)){return reject();}
    auto r=wrap(recovery::finishApply(s,c,o,p,next));
    // Planning label only. Caller still has to commit/readback the new record;
    // this never publishes live telemetry or claims capacity accuracy.
    if(r.decision.action==recovery::Action::MarkApplied){r.units=Units::ExperimentalVirtualTwo;}
    return r;
}
} // namespace mosaico_factory_scale
