#pragma once
#include "mosaico_wifi_settings_model.h"
namespace MosaicoWifiProfiles {
constexpr unsigned Capacity=6;
struct Profile { char ssid[33]{}, password[65]{}; };
struct Model {
 Profile entries[Capacity]{}; uint8_t count=0, lastSuccess=255;
 constexpr int find(const char* s) const {
  for(unsigned i=0;i<count;++i) { unsigned j=0; while(j<33 && entries[i].ssid[j]==s[j]) { if(!s[j]) return i; ++j; } } return -1;
 }
 constexpr bool upsert(const char* s,const char* p) {
  if(!MosaicoWifi::validCredentials(s,p)) return false;
  int i=find(s); if(i<0) { if(count==Capacity)return false; i=count++; }
  for(unsigned j=0;j<33;++j) { entries[i].ssid[j]=s[j]; if(!s[j])break; }
  // Blank on a remembered network means retain its secret, including open networks.
  if(p[0] || !entries[i].password[0]) {
   for(unsigned j=0;j<65;++j)entries[i].password[j]=0;
   for(unsigned j=0;j<64 && p[j];++j)entries[i].password[j]=p[j];
  } return true;
 }
 constexpr bool forget(const char* s) {
  int i=find(s); if(i<0 || count<=1)return false;
  for(unsigned j=i;j+1<count;++j)entries[j]=entries[j+1];
  entries[--count]=Profile{};
  if(lastSuccess==i)lastSuccess=255; else if(lastSuccess!=255 && lastSuccess>i)--lastSuccess;
  return true;
 }
};
using Blob=std::array<uint8_t,600>;
constexpr uint32_t crc(const Blob& b) { uint32_t v=~0U; for(unsigned i=0;i<596;++i){v^=b[i];for(unsigned k=0;k<8;++k)v=(v>>1)^((v&1)?0xedb88320U:0);}return ~v; }
constexpr Blob encode(const Model& m) {
 Blob b{};b[0]='W';b[1]='P';b[2]='F';b[3]=1;b[4]=m.count;b[5]=m.lastSuccess;
 for(unsigned i=0;i<m.count;++i){for(unsigned j=0;j<33;++j)b[8+i*98+j]=m.entries[i].ssid[j];for(unsigned j=0;j<65;++j)b[41+i*98+j]=m.entries[i].password[j];}
 uint32_t c=crc(b);for(unsigned i=0;i<4;++i)b[596+i]=c>>(8*i);return b;
}
constexpr bool decodeModel(const Blob& b,Model& out) {
 uint32_t c=0;for(unsigned i=0;i<4;++i)c|=uint32_t(b[596+i])<<(8*i);
 if(b[0]!='W'||b[1]!='P'||b[2]!='F'||b[3]!=1||!b[4]||b[4]>Capacity||b[6]||b[7]||c!=crc(b)||(b[5]!=255 && b[5]>=b[4]))return false;
 Model m{};m.count=b[4];m.lastSuccess=b[5];
 // GCC supports constant-evaluation detection in C++17: pure static tests
 // remain constexpr while production decoding scrubs every temporary secret.
 auto finish=[&m](bool valid) constexpr {
  if(!__builtin_is_constant_evaluated()) MosaicoWifi::scrub(&m,sizeof(m));
  return valid;
 };
 for(unsigned i=0;i<Capacity;++i){
  if(i>=m.count){for(unsigned j=0;j<98;++j)if(b[8+i*98+j])return finish(false);continue;}
  for(unsigned j=0;j<33;++j)m.entries[i].ssid[j]=b[8+i*98+j];
  for(unsigned j=0;j<65;++j)m.entries[i].password[j]=b[41+i*98+j];
  if(b[40+i*98]||b[105+i*98]||!MosaicoWifi::validCredentials(m.entries[i].ssid,m.entries[i].password))return finish(false);
  for(unsigned j=0;j<i;++j){unsigned k=0;while(k<33 && m.entries[i].ssid[k]==m.entries[j].ssid[k]){if(!m.entries[i].ssid[k])return finish(false);++k;}}
 }
 out=m;return finish(true);
}
inline bool decode(const Blob& b,Model& out) {
 Model decoded{}; const bool valid=decodeModel(b,decoded);
 if(valid) out=decoded;
 MosaicoWifi::scrub(&decoded,sizeof(decoded)); return valid;
}
struct Visible {char ssid[33]{};int8_t rssi=-127;};
struct Candidates {uint8_t indices[Capacity]{},count=0,next=0;};
constexpr Candidates rank(const Model& m,const Visible* aps,unsigned n) {
 Candidates r{};int strength[Capacity]{};
 for(unsigned i=0;i<m.count;++i){int best=-128;for(unsigned a=0;a<n;++a){if(MosaicoWifi::validCredentials(aps[a].ssid,"" ) && m.find(aps[a].ssid)==int(i) && aps[a].rssi>best)best=aps[a].rssi;}if(best==-128)continue;
  unsigned pos=r.count;while(pos && ((i==m.lastSuccess && r.indices[pos-1]!=m.lastSuccess)||(r.indices[pos-1]!=m.lastSuccess && best>strength[pos-1]))){r.indices[pos]=r.indices[pos-1];strength[pos]=strength[pos-1];--pos;}
  r.indices[pos]=i;strength[pos]=best;++r.count;
 }return r;
}
}
