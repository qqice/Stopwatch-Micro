#pragma once
#include "mosaico_fcc_limit_repair_model.h"
#include <cstddef>

namespace mosaico_fcc_limit {
// Canonical 48-byte little-endian envelope. Never serialize C++ padding.
constexpr size_t recordSize = 48;
using Record = uint8_t[recordSize];
constexpr void put16(uint8_t* p, uint16_t v) { p[0]=v; p[1]=v>>8; }
constexpr uint16_t get16(const uint8_t* p) { return uint16_t(p[0]) | (uint16_t(p[1])<<8); }
constexpr uint32_t recordCrc(const uint8_t* p, size_t n) {
    uint32_t crc=0xffffffffU;
    for(size_t i=0;i<n;++i) { crc^=p[i]; for(unsigned b=0;b<8;++b) crc=(crc>>1)^((crc&1)?0xedb88320U:0); }
    return ~crc;
}
constexpr bool journalShape(const Journal& j, Mode mode=Mode::Runtime) {
    if((mode!=Mode::Runtime&&mode!=Mode::ColdFactory) || j.schema!=1 || uint8_t(j.state)<1 || uint8_t(j.state)>3 || (j.intents & ~uint16_t(mode==Mode::Runtime?1023:4095))) return false;
    if(j.state==State::Verified) {
        const uint16_t required=Unseal|Full|Enter|Config|TargetVerified|Exit|Seal|
            (j.before.fcc!=65?Fcc:0)|(mode==Mode::ColdFactory?Dc:0);
        if(!j.backupValid||!j.fullAccessVerified||(j.intents&required)!=required||
           (j.intents&(RestoreConfig|RestoreFcc|RestoreDc)))return false;
    }
    if(!j.backupValid) return !j.before.config && !j.before.dc && !j.before.fcc &&
        !j.target.config && !j.target.dc && !j.target.fcc;
    return j.fullAccessVerified && j.before.config<=0x1fff && !(j.before.config&limitBit) &&
        (mode==Mode::Runtime ? (j.before.dc==65 && (j.before.fcc==65 || j.before.fcc==100)) :
            (j.before.dc==3000 && j.before.fcc==3000)) &&
        j.target.config==(j.before.config|limitBit) && j.target.dc==65 && j.target.fcc==65;
}
constexpr bool encode(const Journal& j, Record& r, Mode mode=Mode::Runtime) {
    if(!journalShape(j,mode)) return false;
    for(size_t i=0;i<recordSize;++i) r[i]=0;
    r[0]='F';r[1]='C';r[2]='C';r[3]='1'; put16(r+4,j.schema);put16(r+6,0x0220);
    r[8]=1;r[11]=uint8_t(mode);r[9]=uint8_t(j.state);r[10]=(j.backupValid?1:0)|(j.fullAccessVerified?2:0);
    for(unsigned i=0;i<6;++i) { r[12+i]=j.mac[i]; }
    put16(r+18,j.intents);
    put16(r+20,j.before.config);put16(r+22,j.before.dc);put16(r+24,j.before.fcc);
    put16(r+26,j.target.config);put16(r+28,j.target.dc);put16(r+30,j.target.fcc);
    uint32_t crc=recordCrc(r,44);for(unsigned i=0;i<4;++i)r[44+i]=crc>>(8*i);
    return true;
}
constexpr bool decode(const Record& r, const uint8_t mac[6], Journal& j, Mode mode=Mode::Runtime) {
    if(r[0]!='F'||r[1]!='C'||r[2]!='C'||r[3]!='1'||get16(r+4)!=1||get16(r+6)!=0x0220||r[8]!=1||r[11]!=uint8_t(mode)||(r[10]&~3))return false;
    for(unsigned i=32;i<44;++i)if(r[i])return false;
    uint32_t crc=0;for(unsigned i=0;i<4;++i)crc|=uint32_t(r[44+i])<<(8*i);
    if(crc!=recordCrc(r,44))return false;
    Journal v{};v.schema=get16(r+4);v.state=State(r[9]);v.backupValid=r[10]&1;v.fullAccessVerified=r[10]&2;
    for(unsigned i=0;i<6;++i){if(r[12+i]!=mac[i])return false;v.mac[i]=r[12+i];}
    v.intents=get16(r+18);v.before={get16(r+20),get16(r+22),get16(r+24)};v.target={get16(r+26),get16(r+28),get16(r+30)};
    if(!journalShape(v,mode))return false;
    j=v;
    return true;
}
constexpr bool verifiedRuntimePermission(const Journal& j) {
    const uint16_t required=Unseal|Full|Enter|Config|TargetVerified|Exit|Seal|(j.before.fcc==100?Fcc:0);
    return journalShape(j) && j.state==State::Verified && j.backupValid && j.fullAccessVerified &&
        (j.intents&required)==required && !(j.intents&(RestoreConfig|RestoreFcc|Dc|RestoreDc));
}
constexpr bool equalRecord(const Record& a,const Record& b){for(size_t i=0;i<recordSize;++i)if(a[i]!=b[i])return false;return true;}
} // namespace mosaico_fcc_limit
