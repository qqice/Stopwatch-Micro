#include "mosaico_lan_tcp_probe.h"
#include <cstring>
#include <cerrno>
#include <esp_netif.h>
#include <esp_timer.h>
#include <freertos/task.h>
#include <lwip/sockets.h>
#include <fcntl.h>
#include <unistd.h>
namespace MosaicoLanTcp {
static bool decimal(const char* s,uint32_t max,uint32_t& v) {
 if(!s || !*s) return false;
 v=0;
 for(;*s;++s) { if(*s<'0'||*s>'9') return false; unsigned n=*s-'0'; if(v>(max-n)/10) return false; v=v*10+n; } return v<=max;
}
bool parse(const char* ip,const char* port,const char* seconds,Request& out) {
 if(!ip) return false;
 uint32_t address=0; const char* p=ip;
 for(int i=0;i<4;++i) { uint32_t octet=0; unsigned digits=0; while(*p>='0'&&*p<='9') { octet=octet*10+(*p++-'0'); if(++digits>3||octet>255) return false; }
 if(!digits || (i<3 ? *p++!='.' : *p!='\0')) return false;
 address=(address<<8)|octet; }
 uint32_t a,b; if(!decimal(port,65535,a)||a<1024||!decimal(seconds,60,b)||b<10) return false;
 if(!((address>>24)==10 || (address>>20)==0xac1 || (address>>16)==0xc0a8)) return false;
 out={address,static_cast<uint16_t>(a),b}; return true;
}
bool destinationAllowed(uint32_t d,uint32_t s,uint32_t m) {
 return s && m && d!=s && (d&m)==(s&m) && (d&~m)!=0 && (d&~m)!=~m &&
 ((d>>24)==10 || (d>>20)==0xac1 || (d>>16)==0xc0a8);
}
Snapshot Probe::snapshot() const { portENTER_CRITICAL(&_mux); auto s=_cache; portEXIT_CRITICAL(&_mux); return s; }
void Probe::publish(const Snapshot& s) { portENTER_CRITICAL(&_mux); _cache=s; if(s.finished && !s.active && !s.queued && !s.cleanupDebt) _outstanding.store(false); portEXIT_CRITICAL(&_mux); }
bool Probe::request(const Request& r,uint16_t trial,uint8_t stage) {
 if(r.port<1024 || r.seconds<10 || r.seconds>60 ||
    !((r.destination>>24)==10 || (r.destination>>20)==0xac1 || (r.destination>>16)==0xc0a8)) return false;
 portENTER_CRITICAL(&_mux);
 if(_cache.queued||_cache.active||_cache.cleanupDebt) { portEXIT_CRITICAL(&_mux); return false; }
 auto id=_cache.requestId+1; _cache={}; _cache.requestId=id; _cache.trialId=trial; _cache.stage=stage;
 _cache.destination=r.destination; _cache.destinationPort=r.port; _cache.seconds=r.seconds; _cancel.store(false); _cache.queued=true; _outstanding.store(true);
 portEXIT_CRITICAL(&_mux); return true;
}
bool Probe::quiesce() {
 if(!busy()) return true;
 cancel(); auto s=snapshot();
 if(s.queued) { s.queued=false; s.finished=true; s.error=ECANCELED; s.endUs=esp_timer_get_time(); publish(s); }
 return !s.active && !s.cleanupDebt;
}
void Probe::service(bool allowed,uint16_t trial,uint8_t stage) {
 if(!busy()) return;
 auto s=snapshot(); if(!s.queued&&!s.active) return;
 if(!allowed||s.trialId!=trial||s.stage!=stage||_cancel.load()) { quiesce(); return; }
 if(s.active) return;
 esp_netif_t* sta=esp_netif_get_handle_from_ifkey("WIFI_STA_DEF"); esp_netif_ip_info_t info{};
 s.error=EINVAL;
 if(!sta||esp_netif_get_ip_info(sta,&info)!=ESP_OK) { s.queued=false;s.finished=true;publish(s);return; }
 s.source=ntohl(info.ip.addr); s.netifIndex=esp_netif_get_netif_impl_index(sta);
 char name[16]{};
 if(!destinationAllowed(s.destination,s.source,ntohl(info.netmask.addr)) || s.netifIndex<=0 || esp_netif_get_netif_impl_name(sta,name)!=ESP_OK) {
 s.queued=false;s.finished=true;publish(s);return; }
 if(_cancel.load()) { s.queued=false;s.finished=true;s.error=ECANCELED;s.endUs=esp_timer_get_time();publish(s);return; }
 std::memcpy(_interface,name,sizeof(_interface)); s.queued=false;s.active=true;s.error=0;s.startUs=esp_timer_get_time(); publish(s);
 // No handle escapes. run copies configuration; reservation remains active until close.
 if(xTaskCreate(worker,"lan_tcp_probe",4096,this,3,nullptr)!=pdPASS) { s.active=false;s.finished=true;s.error=ENOMEM;s.endUs=esp_timer_get_time();publish(s); }
}
void Probe::worker(void* arg) { static_cast<Probe*>(arg)->run(); vTaskDelete(nullptr); }
void Probe::run() {
 auto s=snapshot(); char name[16]; std::memcpy(name,_interface,sizeof(name)); int fd=-1;
 const int64_t deadline=s.startUs+(int64_t(s.seconds)+5)*1000000;
 auto waitReady=[&](bool writing,int64_t until) {
  while(!_cancel.load()) { if(esp_timer_get_time()>=until) { s.error=ETIMEDOUT;return false; }
   fd_set set; FD_ZERO(&set); FD_SET(fd,&set);
   const int64_t remaining=until-esp_timer_get_time();
   if(remaining<=0) {s.error=ETIMEDOUT;return false;}
   timeval t{0,static_cast<long>(remaining<100000?remaining:100000)};
   int ret=select(fd+1,writing?nullptr:&set,writing?&set:nullptr,nullptr,&t);
   if(ret>0) return true;
   if(ret<0&&errno!=EINTR) { s.error=errno;return false; }
  } s.error=ECANCELED;return false;
 };
 do {
  if(_cancel.load()) { s.error=ECANCELED;break; }
  if(esp_timer_get_time()>=deadline) {s.error=ETIMEDOUT;break;}
  fd=socket(AF_INET,SOCK_STREAM,IPPROTO_TCP); if(fd<0) { s.error=errno;break; } s.fdOpen=true;publish(s);
  struct ifreq iface{}; std::strncpy(iface.ifr_name,name,sizeof(iface.ifr_name)-1);
  sockaddr_in src{};src.sin_family=AF_INET;src.sin_addr.s_addr=htonl(s.source);
  if(setsockopt(fd,SOL_SOCKET,SO_BINDTODEVICE,&iface,sizeof(iface))!=0 || bind(fd,reinterpret_cast<sockaddr*>(&src),sizeof(src))!=0) { s.error=errno;break; }
  s.bindingSuccess=true;
  if(fcntl(fd,F_SETFL,O_NONBLOCK)!=0) { s.error=errno;break; }
  sockaddr_in dst{};dst.sin_family=AF_INET;dst.sin_addr.s_addr=htonl(s.destination);dst.sin_port=htons(s.destinationPort);
  if(_cancel.load()) {s.error=ECANCELED;break;}
  if(esp_timer_get_time()>=s.startUs+5000000) {s.error=ETIMEDOUT;break;}
  int ret=connect(fd,reinterpret_cast<sockaddr*>(&dst),sizeof(dst));
  if(ret!=0) {
   if(errno!=EINPROGRESS) { s.error=errno;break; }
   if(!waitReady(true,s.startUs+5000000)) break;
   int error=0;socklen_t size=sizeof(error);
   if(getsockopt(fd,SOL_SOCKET,SO_ERROR,&error,&size)!=0) { s.error=errno;break; } if(error) { s.error=error;break; }
  }
  s.connected=true;socklen_t size=sizeof(src);
  if(getsockname(fd,reinterpret_cast<sockaddr*>(&src),&size)!=0) {s.error=errno;break;} s.sourcePort=ntohs(src.sin_port);publish(s);
  uint8_t bytes[512];
  while(waitReady(false,deadline)) {
   if(_cancel.load()) {s.error=ECANCELED;break;}
   int n=recv(fd,bytes,sizeof(bytes),0); ++s.readCalls;
   if(n==0) break;
   if(n<0) {if(errno==EAGAIN||errno==EWOULDBLOCK||errno==EINTR) continue;s.error=errno;break;}
   for(int i=0;i<n;++i) { uint8_t expected=(s.recvBytes+i)%251; if(bytes[i]!=expected) {
    if(!s.mismatchCount) {s.firstMismatchOffset=s.recvBytes+i;s.expected=expected;s.got=bytes[i];} ++s.mismatchCount;
   }}
   s.recvBytes+=n;s.lastProgressUs=esp_timer_get_time();publish(s);
  }
 } while(false);
 if(fd>=0) {
  s.closeResult=close(fd);
  if(s.closeResult!=0) { s.closeError=errno; s.cleanupDebt=true; }
  else s.fdOpen=false;
 }
 // No retry or descriptor access after failed close: fd reuse/close state is unknown.
 // Debt retains the reservation and blocks driver teardown until reboot recovery.
 s.active=false;s.finished=true;s.endUs=esp_timer_get_time();publish(s);
}
}
