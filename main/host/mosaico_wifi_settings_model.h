#pragma once
#include <array>
#include <cstddef>
#include <cstdint>
struct WifiSettingsSnapshot {
    char ssid[33]{};
    bool pending=false, rebootRequired=false, restartPending=false, available=false;
    int32_t error=0, restartError=0;
};
namespace MosaicoWifi {
inline void scrub(void* p, size_t n) { volatile unsigned char* b=static_cast<volatile unsigned char*>(p); while(n--) *b++=0; }
constexpr size_t boundedLength(const char* s, size_t maximum) {
    if (!s) return maximum+1;
    size_t n=0; while(n<=maximum && s[n]) ++n; return n;
}
constexpr bool validCredentials(const char* ssid, const char* password) {
    const size_t n=boundedLength(ssid,32), p=boundedLength(password,64);
    if (!n || n>32 || p>64 || (p && p<8)) return false;
    // Strict UTF-8: reject controls, overlong encodings, surrogates and >U+10FFFF.
    for(size_t i=0;i<n;) {
        const unsigned char c=ssid[i++];
        if(c<0x20 || c==0x7f) return false;
        if(c<0x80) continue;
        unsigned count=0; uint32_t cp=0, minimum=0;
        if(c>=0xc2 && c<=0xdf) { count=1; cp=c&31; minimum=0x80; }
        else if(c>=0xe0 && c<=0xef) { count=2; cp=c&15; minimum=0x800; }
        else if(c>=0xf0 && c<=0xf4) { count=3; cp=c&7; minimum=0x10000; }
        else return false;
        if(i+count>n) return false;
        while(count--) { const unsigned char d=ssid[i++]; if((d&0xc0)!=0x80) return false; cp=(cp<<6)|(d&63); }
        if(cp<minimum || cp>0x10ffff || (cp>=0xd800 && cp<=0xdfff) || (cp>=0x80 && cp<=0x9f)) return false;
    }
    for(size_t i=0;i<p;++i) {
        const unsigned char c=password[i];
        if(c<0x20 || c>0x7e) return false;
        if(p==64 && !((c>='0'&&c<='9')||(c>='a'&&c<='f')||(c>='A'&&c<='F'))) return false;
    }
    return true;
}
}

namespace MosaicoWifi {
using Blob=std::array<uint8_t,108>;
constexpr uint32_t crc(const Blob& b) {
 uint32_t v=0xffffffffU;
 for(unsigned i=0;i<104;++i) { v^=b[i]; for(unsigned k=0;k<8;++k) v=(v>>1)^((v&1)?0xedb88320U:0); }
 return ~v;
}
constexpr Blob encode(const char* ssid,const char* password) {
 Blob b{}; b[0]='W'; b[1]='I'; b[2]='F'; b[3]=1;
 for(size_t i=0;ssid[i] && i<32;++i) b[4+i]=ssid[i];
 for(size_t i=0;password[i] && i<64;++i) b[37+i]=password[i];
 const uint32_t c=crc(b); for(unsigned i=0;i<4;++i) b[104+i]=c>>(8*i); return b;
}
constexpr bool validBlob(const Blob& b) {
 uint32_t c=0; for(unsigned i=0;i<4;++i) c|=uint32_t(b[104+i])<<(8*i);
 if(b[0]!='W'||b[1]!='I'||b[2]!='F'||b[3]!=1||b[36]||b[101]||b[102]||b[103]||c!=crc(b)) return false;
 char s[33]{},p[65]{};
 for(unsigned i=0;i<33;++i) s[i]=b[4+i];
 for(unsigned i=0;i<65;++i) p[i]=b[37+i];
 return validCredentials(s,p);
}
inline bool decode(const Blob& b,char (&ssid)[33],char (&password)[65]) {
 if(!validBlob(b)) return false;
 char s[33]{},p[65]{};
 for(unsigned i=0;i<33;++i) s[i]=b[4+i];
 for(unsigned i=0;i<65;++i) p[i]=b[37+i];
 const bool valid=validCredentials(s,p);
 if(valid) { for(unsigned i=0;i<33;++i) ssid[i]=s[i]; for(unsigned i=0;i<65;++i) password[i]=p[i]; }
 scrub(p,sizeof(p)); return valid;
}
}
