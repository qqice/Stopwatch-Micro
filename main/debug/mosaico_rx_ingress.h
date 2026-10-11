// SPDX-License-Identifier: MIT
#pragma once
#include <cstddef>
#include <cstdint>
#include <cstring>
namespace mosaico_rx_ingress {
enum class Classification : uint8_t { Invalid, NonIpv4, Fragment, NonTcp, OtherTcp, Target };
struct Record {
    uint64_t localUs=0;
    uint32_t ingressId=0, src=0, dst=0, seq=0, ack=0;
    int32_t netifResult=0;
    uint16_t frameLength=0, ipLength=0, srcPort=0, dstPort=0, tcpPayloadLength=0;
    uint8_t tcpFlags=0, ipHeaderLength=0, tcpHeaderLength=0, vlanTags=0;
    Classification classification=Classification::Invalid;
};
struct Snapshot {
    bool active=false, queued=false;
    uint32_t deadlineMs=0, src=0, seen=0, sta=0, invalid=0, filtered=0, target=0, ringDrops=0;
    uint32_t ok=0, noMem=0, fail=0, other=0;
    uint16_t srcPort=0, buffered=0;
    int32_t lastError=0;
};
bool request(bool on,uint32_t src=0,uint16_t port=0,uint32_t ttlSeconds=0);
// Owner only. The handle lookup is performed once per accepted enable, never in wrapper.
void service(bool allowEnable=true);
Snapshot snapshot();
std::size_t drain(Record* out,std::size_t capacity);
namespace detail {
inline bool alive(uint32_t now,uint32_t deadline){return int32_t(now-deadline)<0;}
inline bool validTarget(uint32_t ip,uint16_t port,uint32_t ttl){const auto first=ip>>24;return first>0 && first<224 && first!=127 && port && ttl && ttl<=120;}
inline bool decimal(const char* p,uint32_t max,uint32_t& out){if(!p || !*p)return false;uint32_t n=0;for(;*p;++p){if(*p<'0'||*p>'9')return false;auto d=uint32_t(*p-'0');if(d>max||n>(max-d)/10)return false;n=n*10+d;}out=n;return n!=0;}
inline bool targetArguments(const char* address,const char* p,const char* t,uint32_t& ip,uint16_t& port,uint32_t& ttl){
 if(!address)return false;
 uint32_t addr=0,n=0,s=0;const char* c=address;
 for(unsigned part=0;part<4;++part){uint32_t o=0;unsigned digits=0;while(*c>='0'&&*c<='9'){o=o*10+uint32_t(*c++-'0');if(++digits>3||o>255)return false;}if(!digits||(part<3?*c!='.':*c!='\0'))return false;addr=(addr<<8)|o;if(part<3)++c;}
 if(!decimal(p,65535,n)||!decimal(t,120,s)||!validTarget(addr,uint16_t(n),s))return false;
 ip=addr;port=uint16_t(n);ttl=s;return true;
}
using Read=bool(*)(void*,std::size_t,uint8_t*,std::size_t);
inline uint16_t be16(const uint8_t* p){return uint16_t((uint16_t(p[0])<<8)|p[1]);}
inline uint32_t be32(const uint8_t* p){return (uint32_t(p[0])<<24)|(uint32_t(p[1])<<16)|(uint32_t(p[2])<<8)|p[3];}
// Only fixed Ethernet/VLAN/IPv4/TCP fields are copied. Options lengths are validated,
// not copied. No body, FCS, UDP/WireGuard or transport checksum body read.
inline Classification parse(Read read,void* ctx,std::size_t len,uint32_t src,uint16_t port,Record& r){
 uint8_t eth[14]{},tag[4]{},ip[20]{},tcp[20]{};
 if(len<14||!read(ctx,0,eth,14))return Classification::Invalid;
 auto type=be16(eth+12);std::size_t off=14;
 while(type==0x8100||type==0x88a8){if(r.vlanTags==2)return Classification::Invalid;if(len-off<4||!read(ctx,off,tag,4))return Classification::Invalid;type=be16(tag+2);off+=4;++r.vlanTags;}
 if(type!=0x0800)return Classification::NonIpv4;
 if(len-off<20||!read(ctx,off,ip,20)||(ip[0]>>4)!=4)return Classification::Invalid;
 const uint16_t ih=uint16_t(ip[0]&15)*4,total=be16(ip+2);
 if(ih<20||ih>60||total<ih||total>len-off)return Classification::Invalid;
 r.src=be32(ip+12);r.dst=be32(ip+16);r.ipLength=total;r.ipHeaderLength=uint8_t(ih);
 if(be16(ip+6)&0x3fff)return Classification::Fragment;
 if(ip[9]!=6)return Classification::NonTcp;
 if(total-ih<20||!read(ctx,off+ih,tcp,20))return Classification::Invalid;
 auto th=uint16_t(tcp[12]>>4)*4;
 if(th<20||th>60||th>total-ih)return Classification::Invalid;
 r.srcPort=be16(tcp);r.dstPort=be16(tcp+2);r.seq=be32(tcp+4);r.ack=be32(tcp+8);r.tcpFlags=tcp[13];r.tcpHeaderLength=uint8_t(th);r.tcpPayloadLength=uint16_t(total-ih-th);
 return r.src==src&&r.srcPort==port?Classification::Target:Classification::OtherTcp;
}
struct MetadataRing {
 Record records[128]{};uint16_t head=0,count=0;
 void clear(){head=count=0;}
 bool push(const Record& r){if(count==128)return false;records[(head+count)%128]=r;++count;return true;}
 std::size_t pop(Record* out,std::size_t cap){if(!out)return 0;if(cap>16)cap=16;auto n=cap<count?cap:count;for(std::size_t i=0;i<n;++i){out[i]=records[head];head=(head+1)%128;}count-=uint16_t(n);return n;}
};
static_assert(sizeof(Record)<=48,"Ingress ring exceeds bounded DRAM budget");
}
}

