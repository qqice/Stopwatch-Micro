"""RV32 production recorder + exact DERP dial-loop extraction; no device/network.
Mocks libc/RTOS/socket APIs, not SDK, DNS, scheduling or firmware validation.
"""
from pathlib import Path
import unittest
from unittest.mock import patch
import test_microlink_peer_probe as rv32

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / 'components/microlink-source/components/microlink/src/ml_derp.c'
HEADER = ROOT / 'components/microlink-source/components/microlink/include/microlink.h'
RUN = ROOT / 'artifacts/derp-candidates-tests'

PREFIX = r'''
typedef unsigned char uint8_t; typedef unsigned short uint16_t;
typedef unsigned uint32_t; typedef int int32_t;
typedef unsigned long long uint64_t; typedef unsigned size_t;
typedef int portMUX_TYPE;
#define NULL 0
#define AF_UNSPEC 0
#define AF_INET 2
#define AF_INET6 10
#define SOCK_STREAM 1
#define AI_NUMERICHOST 4
#define SOL_SOCKET 1
#define SO_SNDTIMEO 2
#define SO_RCVTIMEO 3
#define EAI_SYSTEM -11
#define ESP_FAIL -1
#define portMUX_INITIALIZER_UNLOCKED 0
static int locks, bad, errno, now_ms=100;
#define CONFIG_IDF_TARGET_ESP32S31 1
#define MALLOC_CAP_SPIRAM 1
#define MALLOC_CAP_8BIT 2
static bool fail_alloc;
static int alloc_calls;
static unsigned allocation_memory[1024];
static void* heap_caps_calloc(unsigned n,unsigned size,unsigned caps){
 if(locks||n!=1||size>sizeof(allocation_memory)||caps!=3)++bad;
 ++alloc_calls;errno=996;return fail_alloc?NULL:allocation_memory;
}
#define portENTER_CRITICAL(x) (++locks)
#define portEXIT_CRITICAL(x) (--locks, errno=999)
extern "C" void *memcpy(void*d,const void*s,size_t n){for(size_t i=0;i<n;++i)((uint8_t*)d)[i]=((const uint8_t*)s)[i];return d;}
extern "C" void *memset(void*d,int x,size_t n){for(size_t i=0;i<n;++i)((uint8_t*)d)[i]=x;return d;}
static int strcmp(const char*a,const char*b){while(*a&&*a==*b){++a;++b;}return *a-*b;}
static int snprintf(char*out,size_t,const char*,int){out[0]='4';out[1]='4';out[2]='3';out[3]=0;return 3;}
static uint64_t ml_get_time_ms(){if(locks)++bad;errno=998;return now_ms;}
static uint16_t ntohs(uint16_t x){return (x>>8)|(x<<8);}
struct sockaddr {uint16_t family;};
struct sockaddr_in {uint16_t sin_family,sin_port;uint8_t sin_addr[4];};
struct sockaddr_in6 {uint16_t sin6_family,sin6_port;uint32_t flow;uint8_t sin6_addr[16];};
struct timeval {int tv_sec,tv_usec;};
struct addrinfo {int ai_flags,ai_family,ai_socktype; sockaddr*ai_addr;unsigned ai_addrlen;addrinfo*ai_next;};
static sockaddr_in a4={AF_INET,0xbb01,{1,2,3,4}};
static sockaddr_in6 a6={AF_INET6,0xbb01,0,{0x20,1,0xd,0xb8,0,0,0,0,0,0,0,0,0,0,0,7}};
static addrinfo answer, next_answer;
static int operations[16],operation_count;
static void operation(int value){if(operation_count<16)operations[operation_count++]=value;else ++bad;}
static int mode, connects, closes, failure_errno, lookup_calls, socket_calls;
static int derp_trace_stage,derp_trace_fd;
static int ml_getaddrinfo(const char*,const char*,const addrinfo*h,addrinfo**r){
 if(locks)++bad;++lookup_calls;now_ms+=3;
 if(mode==1){*r=NULL;return -2;} // ordinary EAI leaves stale errno
 if(mode==2){*r=NULL;errno=77;return EAI_SYSTEM;}
 answer={};answer.ai_family=h->ai_family==AF_INET6?AF_INET6:AF_INET;
 answer.ai_addr=answer.ai_family==AF_INET?(sockaddr*)&a4:(sockaddr*)&a6;
 answer.ai_addrlen=answer.ai_family==AF_INET?sizeof(a4):sizeof(a6);
 if(mode==11){next_answer={};next_answer.ai_family=AF_INET6;next_answer.ai_addr=(sockaddr*)&a6;next_answer.ai_addrlen=sizeof(a6);answer.ai_next=&next_answer;}
 *r=&answer;return 0;
}
static int ml_socket(int,int,int){if(locks)++bad;++socket_calls;now_ms+=2;errno=55;
 int fd=mode>=11?7+socket_calls:8;operation(100+fd);return mode==3?-1:fd;}
static int ml_setsockopt(int,int,int,const void*,unsigned){if(locks)++bad;errno=444;return 0;}
static int ml_connect(int,const sockaddr*,unsigned);
static void ml_close_sock(int fd){if(locks)++bad;++closes;operation(300+fd);errno=666;}
static void ml_freeaddrinfo(addrinfo*){if(locks)++bad;operation(400);errno=667;}
static void derp_trace_failure(void*,int){failure_errno=errno;errno=668;}
'''

CASES = r'''
static int ml_connect(int fd,const sockaddr*,unsigned){
 if(locks)++bad;++connects;operation(200+fd);
 if(derp_candidate_store && (!derp_candidate_count||derp_candidate_store->ring[(derp_candidate_head+derp_candidate_count-1)%16].stage!=MICROLINK_DERP_CONNECT_BEGIN))++bad;
 now_ms+=13;errno=88;return mode==4||((mode==11||mode==12)&&connects==1)?-1:0;
}
static void reset(){
 operation_count=0;
 derp_candidate_head=derp_candidate_count=derp_candidate_seq=derp_candidate_dropped=derp_candidate_attempts=0;
 derp_candidate_store=NULL;derp_candidate_allocation_attempted=false;derp_candidate_allocation_failures=0;fail_alloc=false;alloc_calls=0;
 derp_candidate_has_failure=false;mode=connects=closes=failure_errno=lookup_calls=socket_calls=bad=locks=0;now_ms=100;errno=123;
}
static int run_case(int id){
 reset();microlink_derp_candidate_t e{},f{};microlink_derp_candidate_stats_t stats{};
 if(id<=5){
  mode=id;const char*hosts[3]={NULL,NULL,NULL};int families[3]={AF_INET,AF_INET6,AF_UNSPEC};
  hosts[id==5?1:0]=id==5?"2001:db8::7":"1.2.3.4";
  int sock=dial(hosts,families);
  microlink_derp_candidate_get_stats(&stats);
  if(bad||locks||stats.attempts!=1||stats.pending!=(id==1||id==2?1:id==3?2:4))return 1;
  if(!microlink_derp_candidate_peek(&e)||e.stage!=1||e.attempt_id!=1||e.region!=901||e.node_index!=2||e.port!=443||e.elapsed_ms!=3)return 2;
  if(id==1||id==2){
   if(sock!=-1||e.result!=(id==1?-2:EAI_SYSTEM)||e.errno_valid!=(id==2)||e.error!=(id==2?77:0)||socket_calls)return 3;
  }else{
   if(!microlink_derp_candidate_consume(e.seq)||!microlink_derp_candidate_peek(&e)||e.stage!=2||e.result!=(id==3?-1:8)||e.error!=(id==3?55:0)||!e.errno_valid||e.elapsed_ms!=2)return 4;
   if(strcmp(e.candidate,hosts[id==5?1:0]))return 28;
   if(e.family!=(id==5?6:4)||e.source!=(id==5?2:1)||e.address[0]!=(id==5?0x20:1)||e.address[id==5?15:3]!=(id==5?7:4))return 5;
   if(id==3){if(connects||failure_errno!=55)return 6;}
   else {
    microlink_derp_candidate_consume(e.seq);microlink_derp_candidate_peek(&e);
    if(e.stage!=3||e.errno_valid||e.error||e.elapsed_ms)return 7;
    microlink_derp_candidate_consume(e.seq);microlink_derp_candidate_peek(&e);
    if(e.stage!=4||e.result!=(id==4?-1:0)||e.error!=(id==4?88:0)||!e.errno_valid||e.elapsed_ms!=13)return 8;
    if(id==4&&(failure_errno!=88||closes!=1))return 9;
   }
  }
  bool failed=microlink_derp_candidate_get_last_failure(&f);
  if(failed!=(id>=1&&id<=4))return 10;
  return 0;
 }
 if(id==6){
  derp_candidate_begin_attempt(); // pending BEGIN survives without a result; null and sequence safety
  if(microlink_derp_candidate_peek(NULL)||microlink_derp_candidate_get_last_failure(NULL))return 11;
  microlink_derp_candidate_get_stats(NULL);
  derp_candidate_note(1,901,2,443,3,"relay.test",AF_UNSPEC,NULL,3,0,0,false,100);
  if(!microlink_derp_candidate_peek(&e)||e.family||e.candidate_len!=10||e.stage!=3||e.errno_valid)return 12;
  if(microlink_derp_candidate_consume(e.seq+1)||!microlink_derp_candidate_peek(&f)||f.seq!=e.seq)return 13;
  derp_candidate_note(1,901,2,443,3,"relay.test",AF_UNSPEC,NULL,4,0,0,true,100);
  if(!microlink_derp_candidate_consume(e.seq)||microlink_derp_candidate_consume(e.seq)||!microlink_derp_candidate_peek(&f)||f.seq!=e.seq+1||f.stage!=4)return 14;
  if(!microlink_derp_candidate_consume(f.seq)||microlink_derp_candidate_peek(&e))return 29;
  return 0;
 }
 if(id==7){
  derp_candidate_begin_attempt(); // full ring drops new events, but failure snapshot keeps updating
  for(int i=0;i<19;++i)derp_candidate_note(1,1,2,443,3,"x",AF_UNSPEC,NULL,4,-1,40+i,true,100);
  microlink_derp_candidate_get_stats(&stats);
  if(stats.pending!=16||stats.produced!=19||stats.dropped!=3)return 15;
  if(!microlink_derp_candidate_get_last_failure(&e)||e.seq!=19||e.error!=58)return 16;
  for(unsigned i=1;i<=16;++i){if(!microlink_derp_candidate_peek(&e)||e.seq!=i||!microlink_derp_candidate_consume(i))return 17;}
  if(microlink_derp_candidate_peek(&e))return 18;
  derp_candidate_note(2,1,2,443,3,"x",AF_UNSPEC,NULL,4,0,0,true,100);
  microlink_derp_candidate_get_last_failure(&e);
  microlink_derp_candidate_get_stats(&stats);
  return e.seq==19&&e.error==58&&stats.produced==20&&stats.dropped==3&&stats.pending==1?0:19;
 }
 if(id==8){
  derp_candidate_begin_attempt();
  char host[160];for(unsigned i=0;i<159;++i)host[i]='a';host[159]=0;
  errno=321;derp_candidate_note(1,1,2,443,3,host,AF_UNSPEC,NULL,1,-2,666,false,100);
  if(errno!=321)return 20;
  microlink_derp_candidate_peek(&e);
  if(e.candidate_len!=127||!e.candidate_truncated||e.candidate[127]||e.candidate[126]!='a'||e.errno_valid||e.error)return 21;
  errno=322;if(derp_candidate_begin_attempt()!=2||errno!=322)return 22;
  return bad||locks?23:0;
 }
 if(id==9){
  microlink_derp_candidate_get_stats(&stats);
  if(stats.capacity||stats.allocation_failures||stats.pending||alloc_calls)return 24;
  fail_alloc=true;
  const char*hosts[3]={"1.2.3.4",NULL,NULL};int families[3]={AF_INET,AF_INET6,AF_UNSPEC};
  if(dial(hosts,families)!=8||dial(hosts,families)!=8||connects!=2||lookup_calls!=2||socket_calls!=2)return 25;
  microlink_derp_candidate_get_stats(&stats);
  if(bad||locks||alloc_calls!=1||stats.capacity||stats.allocation_failures!=1||stats.produced!=8||stats.dropped!=8||stats.pending||stats.attempts!=2)return 26;
  if(microlink_derp_candidate_peek(&e)||microlink_derp_candidate_get_last_failure(&e))return 27;
  return 0;
 }
 if(id==10){
  const char*hosts[3]={NULL,NULL,"relay.test"};int families[3]={AF_INET,AF_INET6,AF_UNSPEC};
  if(dial(hosts,families)!=8)return 30;
  if(!microlink_derp_candidate_peek(&e)||e.family||e.source!=3||strcmp(e.candidate,"relay.test"))return 31;
  if(alloc_calls!=1||bad||locks)return 32;
  return 0;
 }
 if(id==11||id==12){
  // Linked answers from one hostname lookup, or mapv4 -> mapv6 fallback.
  mode=id;
  const char*hosts[3]={id==12?"1.2.3.4":NULL,id==12?"2001:db8::7":NULL,id==11?"relay.test":NULL};
  int families[3]={AF_INET,AF_INET6,AF_UNSPEC};
  if(dial(hosts,families)!=9||bad||locks||closes!=1||connects!=2||socket_calls!=2||lookup_calls!=(id==11?1:2)||failure_errno!=88)return 33;
  microlink_derp_candidate_get_stats(&stats);
  if(stats.attempts!=1||stats.produced!=(id==11?7:8)||stats.dropped)return 34;
  int expected_ops_linked[6]={108,208,308,109,209,400};
  int expected_ops_sources[7]={108,208,308,400,109,209,400};
  int count=id==11?6:7;
  if(operation_count!=count)return 35;
  for(int i=0;i<count;++i)if(operations[i]!=(id==11?expected_ops_linked[i]:expected_ops_sources[i]))return 36;
  int answer_index=0;
  for(unsigned seq=1;seq<=stats.produced;++seq){
   if(!microlink_derp_candidate_peek(&e)||e.seq!=seq||e.attempt_id!=1||e.region!=901||e.node_index!=2||e.port!=443)return 37;
   int stage=id==11?(seq==1?1:((seq-2)%3)+2):((seq-1)%4)+1;
   if(seq==5)answer_index=1;
   int source=id==11?3:answer_index+1;
   if(e.stage!=stage||e.source!=source||strcmp(e.candidate,id==11?"relay.test":answer_index?"2001:db8::7":"1.2.3.4"))return 38;
   if(stage==1){
    if(e.result||e.error||e.errno_valid||e.family!=(id==11?0:answer_index?6:4)||e.elapsed_ms!=3)return 39;
    for(int j=0;j<16;++j)if(e.address[j])return 40;
   }else{
    if(e.family!=(answer_index?6:4)||e.address[0]!=(answer_index?0x20:1)||e.address[answer_index?15:3]!=(answer_index?7:4))return 41;
    for(int j=0;j<16;++j){
     uint8_t expected=answer_index?a6.sin6_addr[j]:(j<4?a4.sin_addr[j]:0);
     if(e.address[j]!=expected)return 49;
    }
    if(stage==2&&(e.result!=(answer_index?9:8)||e.error||!e.errno_valid||e.elapsed_ms!=2))return 42;
    if(stage==3&&(e.result||e.error||e.errno_valid||e.elapsed_ms))return 43;
    if(stage==4&&(e.result!=(answer_index?0:-1)||e.error!=(answer_index?0:88)||!e.errno_valid||e.elapsed_ms!=13))return 44;
   }
   if(!microlink_derp_candidate_consume(seq))return 45;
  }
  if(microlink_derp_candidate_peek(&e)||!microlink_derp_candidate_get_last_failure(&f)||f.seq!=4||f.result!=-1||f.error!=88||f.family!=4||f.address[0]!=1||f.address[3]!=4||f.source!=(id==11?3:1))return 46;
  return 0;
 }
 if(id==13){
  // Explicit "none" is skipped, not passed to getaddrinfo; no invented DNS fallback.
  mode=13;const char*hosts[3]={"none","2001:db8::7",NULL};int families[3]={AF_INET,AF_INET6,AF_UNSPEC};
  if(dial(hosts,families)!=8||lookup_calls!=1||socket_calls!=1||connects!=1||closes||bad)return 47;
  if(!microlink_derp_candidate_peek(&e)||e.source!=2||e.family!=6||strcmp(e.candidate,"2001:db8::7"))return 48;
  return 0;
 }
 return 99;
}
extern "C" {volatile int test_results[14]={};}
extern "C" __attribute__((noinline,used)) void test_done(){for(;;){asm volatile("");}}
extern "C" void _start(){for(int i=0;i<14;++i)test_results[i]=run_case(i);test_done();}
'''

def fixture(source):
    header = HEADER.read_text(encoding='utf8')
    decl = header[header.index('#define MICROLINK_DERP_CANDIDATE_VERSION'):header.index('/* Cached device-wide DERP stack')]
    recorder = source[source.index('/* Candidate recorder:'):source.index('/* Single DERP TLS/BIO owner;')]
    body = rv32.function(source, 'static esp_err_t derp_connect_once(')
    loop = body[body.index('    int sock = -1;'):body.index('    if (sock < 0) {')]
    dial = ('static int dial(const char **hosts,int *families){void*ml=NULL;'
            'uint32_t candidate_attempt=derp_candidate_begin_attempt();'
            'uint16_t actual_region=901,candidate_node=2;int derp_port=443;'
            + loop + 'return sock;}')
    # Connect stub must see the production recorder; dial requires its declaration.
    return PREFIX + decl + recorder + dial + CASES

class CandidateTests(unittest.TestCase):
    def test_production_rv32(self):
        with patch.object(rv32, 'RUN', RUN), patch.object(rv32, 'fixture', fixture):
            results = rv32.execute(SOURCE.read_text(encoding='utf8'), 'candidate-production', count=14)
        log = RUN / 'candidate-production-execution.log'
        log.write_text(log.read_text().replace(
            'Production PONG + registration/lifecycle statements. Mock WG APIs, no crypto/network.',
            'Production DERP recorder and exact dial-loop statements. Mock allocation/RTOS/DNS/socket APIs; no device/network/SDK validation.'), encoding='utf8')
        self.assertEqual(results, (0,)*14)

    def test_source_binding(self):
        source = SOURCE.read_text(encoding='utf8')
        body = rv32.function(source, 'static esp_err_t derp_connect_once(')
        for call, capture, stage in [
            ('int dns_ret = ml_getaddrinfo(', 'int dns_errno = errno;', 'MICROLINK_DERP_LOOKUP_RESULT'),
            ('int candidate = ml_socket(', 'int socket_errno = errno;', 'MICROLINK_DERP_SOCKET_RESULT'),
            ('int tcp_ret = ml_connect(', 'int tcp_errno = errno;', 'MICROLINK_DERP_CONNECT_RESULT')]:
            tail = body[body.index(call):]
            self.assertLess(tail.index(capture), tail.index('derp_candidate_note('))
            self.assertLess(tail.index(stage), tail.index('derp_trace_failure('))
            self.assertEqual(tail.split('\n', 2)[1].strip(), capture)
        self.assertLess(body.index('MICROLINK_DERP_CONNECT_BEGIN'), body.index('int tcp_ret = ml_connect('))
        self.assertLess(body.index('MICROLINK_DERP_CONNECT_RESULT'), body.index('ml_close_sock(candidate)'))
        self.assertIn('if (!hosts[h] || strcmp(hosts[h], "none") == 0) continue;', body)
        self.assertIn('if (!selected_node->ipv4[0] || !selected_node->ipv6[0])', body)
        self.assertIn('candidate_node = (uint16_t)candidate;', body)
        self.assertIn('actual_region = selected_node ? region : ML_DERP_REGION;', body)
        self.assertIn('uint16_t candidate_node = UINT16_MAX;', body)
        self.assertIn('dns_errno_valid = dns_ret == EAI_SYSTEM;', body)
        recorder = source[source.index('/* Candidate recorder:'):source.index('/* Single DERP TLS/BIO owner;')]
        self.assertNotIn('observer_', recorder.lower())
        self.assertTrue(rv32.function(source, 'static void derp_candidate_note(').rstrip().endswith('errno = saved_errno;\n}'))

if __name__ == '__main__':
    unittest.main()
