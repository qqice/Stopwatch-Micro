// Closed read-only bq27220 0.03 build 4 diagnostics. No inferred units.
#pragma once
#include <cstdint>
#include <cstddef>
#include <type_traits>
#include "mosaico_gauge_unit_tag.h"
namespace mosaico_gauge_engine {
enum Valid : uint16_t { Cedv=1, Smooth=2, Identity=4, DefinitionMatch=8,
 CedvTx=16, CedvEcho=32, CedvData=64, SmoothTx=128, SmoothEcho=256, SmoothData=512,
 IdentityTx=1024, IdentityEcho=2048, IdentityData=4096, StartupDeferred=32768 };
constexpr uint64_t StartupDeferUntilMs=300000;
constexpr bool startupDeferred(bool enabled,uint64_t uptimeMs){return enabled&&uptimeMs<StartupDeferUntilMs;}
struct Snapshot {
 uint64_t sampledUs;
 uint16_t validMask,echo73,echo76,echo02;
 uint8_t phase,outcome,rawCedv[23],rawSmooth[17],rawIdentity[11];
 mosaico_gauge_unit_tag::Tag units;
};
static_assert(std::is_trivial<Snapshot>::value && std::is_standard_layout<Snapshot>::value);
constexpr uint16_t leWord(const uint8_t* p,size_t o){return uint16_t(p[o])|(uint16_t(p[o+1])<<8);}
constexpr uint16_t beWord(const uint8_t* p,size_t o){return (uint16_t(p[o])<<8)|p[o+1];}
namespace detail {
template<uint8_t Selector,size_t N,class Transport>
constexpr bool block(Transport& bus,uint8_t (&out)[N],uint16_t& echo,uint16_t& flags,
                    uint16_t tx,uint16_t echoed,uint16_t data) {
 static_assert((Selector==0x73&&N==23)||(Selector==0x76&&N==17)||(Selector==2&&N==11));
 echo=0;for(size_t i=0;i<N;++i)out[i]=0;
 const uint8_t command[]={0,Selector,0};
 if(!bus.transmit(command,sizeof(command)))return false;
 flags|=tx;bus.delayMs(20);
 uint8_t e[2]{};
 if(bus.read(0x3e,e,2)){echo=leWord(e,0);flags|=echoed;}
 // Discard partial destinations on a failed exact-length transport read.
 uint8_t raw[N]{};
 if(bus.read(0x40,raw,N)){for(size_t i=0;i<N;++i)out[i]=raw[i];flags|=data;}
 return (flags&(tx|echoed|data))==(tx|echoed|data)&&echo==Selector;
}
}
template<class Transport> constexpr bool read(Transport& bus,Snapshot& s) {
 s.validMask=0;
 if(detail::block<2>(bus,s.rawIdentity,s.echo02,s.validMask,IdentityTx,IdentityEcho,IdentityData))s.validMask|=Identity;
 if((s.validMask&Identity)&&beWord(s.rawIdentity,0)==0x0220&&beWord(s.rawIdentity,2)==3&&beWord(s.rawIdentity,4)==4)s.validMask|=DefinitionMatch;
 if(detail::block<0x73>(bus,s.rawCedv,s.echo73,s.validMask,CedvTx,CedvEcho,CedvData))s.validMask|=Cedv;
 if(detail::block<0x76>(bus,s.rawSmooth,s.echo76,s.validMask,SmoothTx,SmoothEcho,SmoothData))s.validMask|=Smooth;
 return (s.validMask&(Cedv|Smooth|DefinitionMatch))==(Cedv|Smooth|DefinitionMatch);
}
}
