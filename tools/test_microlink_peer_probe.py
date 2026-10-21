"""Execute extracted production DISCO PONG and lifecycle statements on RV32.
No firmware, cryptography, network, scheduler or hardware correctness claim.
"""
from pathlib import Path
import hashlib, os, re, struct, subprocess, sys, unittest
ROOT=Path(__file__).resolve().parents[1]
SOURCE=ROOT/'components/microlink-source/components/microlink/src/ml_wg_mgr.c'
RUN=ROOT/'.artifacts/private/mosaico/peer-pong-tests-001'
CLANG=Path('C:/Espressif/tools/esp-clang/esp-21.1.3_20260408/esp-clang/bin/clang++.exe')
def function(s,signature):
    start=s.index(signature); brace=s.index('{',start); depth=1; i=brace+1
    while depth:
        if s[i]=='{': depth+=1
        elif s[i]=='}': depth-=1
        i+=1
    return s[start:i]+'\n'
def fixture(s):
    probe=s[s.index('typedef struct {',s.index('/* Pending DISCO probe')):s.index('/* ============================================================================',s.index('/* Pending DISCO probe'))]
    lifecycle=''
    # Exact production lifecycle hook statements (absence in old source is intentional).
    for signature,name,idx in [('static int add_peer(', 'full_add', 'idx'),('static void remove_peer(', 'remove', 'idx')]:
        body=function(s,signature)
        hook='disco_cancel_peer_probes('+idx+');'
        lifecycle+='static void '+name+'(int idx) {'+(hook if hook in body else '')+'}\n'
    body=function(s,'static int add_peer(')
    lifecycle+='static void evict(int evict_idx) {'+('disco_cancel_peer_probes(evict_idx);' if 'disco_cancel_peer_probes(evict_idx);' in body else '')+'}\n'
    reset='p->tried_initial_handshake = false;' if 'p->tried_initial_handshake = false;' in body else ''
    lifecycle+='static void reset_one_shot(ml_peer_t *p) {'+reset+'}\n'
    registration=s.split('    /* Track pending probe */',1)[1].split('\n}\n',1)[0]
    helper=''
    if 'static void wg_direct_path_in_owner(' in s:
        helper=s[s.index('typedef struct {',s.index('/* Serialize PONG path promotion')):s.index('static int find_peer_by_key(')]
    protocol=(ROOT/'components/microlink-source/components/microlink/components/wireguard_lwip/src/wireguard.c').read_text(encoding='utf8')
    prefix=PREFIX.replace('static bool wireguard_expired(uint32_t created,uint32_t seconds){return uint32_t(now_ms-created)>=seconds*1000;}', 'static uint32_t wireguard_sys_now(){return now_ms;}'+function(protocol,'bool wireguard_expired('))
    header=(ROOT/'components/microlink-source/components/microlink/include/microlink.h').read_text(encoding='utf8')
    start=header.index('typedef struct {',header.index('/* Numeric, device-wide cumulative boot counters.'))
    decl=header[start:header.index('} microlink_peer_path_diag_t;',start)+len('} microlink_peer_path_diag_t;')]
    current=SOURCE.read_text(encoding='utf8')
    diag=current[current.index('static portMUX_TYPE ml_peer_path_diag_mux'):current.index('/* Pending DISCO probe tracking */')]
    cases=CASES
    if 'peer_path_count(&ml_peer_path_diag.foreign_sender)' in s and 'static int run_sequence(' not in cases:
        cases=cases.replace('static int run_case(', 'static int run_probe_sequence(')
        check="""static int run_case(int id){int r=run_probe_sequence(id);if(r)return r;microlink_peer_path_diag_t d{};microlink_peer_path_get_diagnostics(&d);
 if((id==0||id==2||id==9||id==11)&&d.probe_cancelled!=1)return 80;
 if((id==3||id==6)&&d.probe_stale!=1)return 81;
 if((id==7||id==11)&&d.foreign_sender!=1)return 82;
 return 0;}
"""
        cases=cases.replace('extern "C" {volatile int test_results',check+'extern "C" {volatile int test_results',1)
    return prefix+decl+diag+probe+helper+function(s,'static int find_peer_by_disco_key(')+lifecycle+'static void register_probe(microlink_t *ml,int peer_idx) {ml_peer_t *p=&ml->peers[peer_idx]; uint8_t txid[12]={7};'+registration+'}\n'+function(s,'static void process_disco_pong(')+cases
PREFIX=r'''
typedef unsigned char uint8_t;typedef unsigned short uint16_t;typedef unsigned int uint32_t;typedef unsigned long long uint64_t;typedef unsigned int size_t;typedef unsigned char u8_t;typedef unsigned short u16_t;typedef int err_t;
#define DISCO_TXID_LEN 12
#define ML_DISCO_TRUST_DURATION_MS 5000
#define ERR_OK 0
#define IPADDR_TYPE_V4 0
#define IP_SET_TYPE_VAL(a,b) ((void)0)
#define ip_2_ip4(x) (x)
#define ESP_LOGI(...) ((void)0)
#define ESP_LOGW(...) ((void)0)
#define NULL 0
typedef int portMUX_TYPE;
#define portMUX_INITIALIZER_UNLOCKED 0
static unsigned critical_calls;
#define portENTER_CRITICAL(x) (++critical_calls)
#define portEXIT_CRITICAL(x) (++critical_calls)
extern "C" void *memcpy(void*d,const void*s,size_t n){for(size_t i=0;i<n;++i)((uint8_t*)d)[i]=((const uint8_t*)s)[i];return d;}
extern "C" void *memset(void*d,int x,size_t n){for(size_t i=0;i<n;++i)((uint8_t*)d)[i]=x;return d;}
extern "C" int memcmp(const void*a,const void*b,size_t n){for(size_t i=0;i<n;++i)if(((const uint8_t*)a)[i]!=((const uint8_t*)b)[i])return 1;return 0;}
typedef struct{uint32_t ip;}ip_addr_t;
struct wireguard_keypair{bool valid;uint32_t sentinel;uint8_t sending_key[32],receiving_key[32];uint64_t counter;};
struct wireguard_handshake{bool valid,initiator;uint32_t local_index;};
struct wireguard_peer{bool valid,active;uint8_t public_key[32];ip_addr_t connect_ip,ip;u16_t connect_port,port;wireguard_keypair curr_keypair,prev_keypair,next_keypair;wireguard_handshake handshake;uint32_t last_initiation_tx;bool send_handshake;};
#define WIREGUARD_MAX_PEERS 2
#define REKEY_TIMEOUT 5
struct wireguard_device{wireguard_peer peers[2];};
struct netif{void*state;};static wireguard_device device;
static uint32_t now_ms=10000;
static bool wireguard_expired(uint32_t created,uint32_t seconds){return uint32_t(now_ms-created)>=seconds*1000;}

static uint32_t htonl(uint32_t x){return x;}
static void ip4_addr_set_u32(ip_addr_t*p,uint32_t x){p->ip=x;}
static uint32_t ip4_addr_get_u32(ip_addr_t*p){return p->ip;}
typedef struct{bool active;uint8_t public_key[32],disco_key[32];char hostname[32];uint64_t last_pong_recv_ms,trust_until_ms;uint32_t best_ip;uint16_t best_port;bool has_direct_path,tried_initial_handshake;int wg_peer_index;} ml_peer_t;
typedef struct {ml_peer_t peers[2];int peer_count;void*wg_netif;struct{bool connected;}derp;}microlink_t;
typedef struct {bool via_derp;uint32_t src_ip;uint16_t src_port;}ml_rx_packet_t;
static int connects,updates;static bool up;static uint32_t endpoint,session_ip;static uint16_t session_port;
static uint64_t ml_get_time_ms(){return 100;}
static int wireguardif_update_endpoint(netif*,u8_t,const ip_addr_t*p,u16_t){++updates;endpoint=p->ip;return 0;}
static int wireguardif_peer_is_up(netif*,u8_t,ip_addr_t*p,u16_t*q){if(p)p->ip=session_ip;if(q)*q=session_port;return up?0:-1;}
static int connect_error=0,owner_error=0;static bool in_owner=false;
static int wireguardif_connect(netif*n,u8_t i){++connects;if(connect_error)return -1;auto &p=((wireguard_device*)n->state)->peers[i];p.active=true;p.ip=p.connect_ip;p.port=p.connect_port;p.handshake={true,true,p.handshake.local_index+1};p.last_initiation_tx=now_ms;return 0;}
typedef struct{netif*netif;u8_t index;bool clear_active;} wg_peer_owner_ctx;
static void wg_peer_state_in_owner(void*){}
static int wireguardif_owner_call(void(*f)(void*),void*p){if(owner_error)return -1;in_owner=true;f(p);in_owner=false;return 0;}
'''
CASES=r'''
static microlink_t ml;static netif nf;static ml_rx_packet_t pkt;static uint8_t payload[14]={0,0,7};
static void setup(){memset(&ml_peer_path_diag,0,sizeof ml_peer_path_diag);ml_peer_path_diag.version=1;critical_calls=0;memset(&device,0,sizeof device);device.peers[0].valid=true;device.peers[0].public_key[0]=11;nf.state=&device;connect_error=owner_error=0;now_ms=10000;memset(&ml,0,sizeof ml);memset(pending_probes,0,sizeof pending_probes);ml.peer_count=1;ml.wg_netif=&nf;ml.peers[0].active=true;ml.peers[0].wg_peer_index=0;ml.peers[0].disco_key[0]=1;ml.peers[0].public_key[0]=11;pkt={false,42,1234};connects=updates=0;up=false;endpoint=session_ip=0;session_port=0;register_probe(&ml,0);}
static void pong(uint8_t sender){uint8_t key[32]={};key[0]=sender;device.peers[0].curr_keypair.valid=up;process_disco_pong(&ml,&pkt,key,payload,sizeof payload);if(device.peers[0].connect_ip.ip){updates=1;endpoint=device.peers[0].connect_ip.ip;}}
static bool untouched(){return !updates&&!connects&&!ml.peers[0].has_direct_path&&!ml.peers[0].best_ip&&ml.peers[0].public_key[0]==11;}
static int run_case(int id){setup();switch(id){
case 0:evict(0);ml.peers[0].disco_key[0]=2;pong(1);return untouched()?0:1;
case 1:pong(1);return updates&&connects==1&&ml.peers[0].best_ip==42?0:2;
case 2:remove(0);full_add(0);pong(1);return untouched()?0:3;
case 3:ml.peers[0].disco_key[0]=2;pong(1);return untouched()?0:4;
case 4:ml.derp.connected=true;pong(1);return connects==1&&updates&&ml.peers[0].best_ip==42&&ml.peers[0].tried_initial_handshake?0:5;
case 5:up=true;ml.derp.connected=true;pong(1);return !connects&&updates&&ml.peers[0].best_ip==42?0:6;
case 6:ml.peers[0].active=false;pong(1);return untouched()?0:7;
case 7:pong(2);if(!untouched()||!pending_probes[0].active)return 8;pong(1);return connects==1?0:9;
case 8:ml.peers[0].tried_initial_handshake=true;reset_one_shot(&ml.peers[0]);return ml.peers[0].tried_initial_handshake?10:0;
case 9:full_add(0);pong(1);if(!untouched())return 11;register_probe(&ml,0);pong(1);return connects==1?0:12;
case 10:ml.derp.connected=true;pong(1);if(connects!=1)return 13;ml.derp.connected=false;register_probe(&ml,0);pong(1);return connects==1?0:14;
case 11:evict(0);ml.peers[0].disco_key[0]=2;full_add(0);register_probe(&ml,0);pong(1);if(!untouched())return 15;pong(2);return connects==1&&ml.peers[0].best_ip==42&&ml.peers[0].disco_key[0]==2?0:16;
}return 99;}
extern "C" {volatile int test_results[12]={};}
extern "C" __attribute__((noinline,used)) void test_done(){for(;;){asm volatile("");}}
extern "C" void _start(){for(int i=0;i<12;++i)test_results[i]=run_case(i);test_done();}
'''
def execute(source,label,count=12):
    RUN.mkdir(parents=True,exist_ok=True);cpp=RUN/(label+'.cpp');cpp.write_text(fixture(source));elf=RUN/(label+'.elf')
    args=[str(CLANG),'--target=riscv32-unknown-elf','-march=rv32im','-mabi=ilp32','-O1','-Wno-missing-multilib','-fno-builtin','-fno-exceptions','-fno-rtti','-nostdinc++','-nostdlib',str(cpp),'--ld-path='+str(CLANG.parent/'ld.lld.exe'),'-Wl,-e,_start','-Wl,-Ttext=0x10000','-o',str(elf)]
    r=subprocess.run(args,capture_output=True,text=True,env={**os.environ,"TEMP":str(RUN),"TMP":str(RUN)});(RUN/(label+'-compile.log')).write_text(r.stdout+r.stderr);assert r.returncode==0,(label,r.stderr)
    sys.path.insert(0,str(ROOT/'.artifacts/tools/unicorn-2.1.4-private-001/package'))
    from unicorn import Uc,UC_ARCH_RISCV,UC_MODE_RISCV32
    from unicorn.riscv_const import UC_RISCV_REG_SP,UC_RISCV_REG_PC
    raw=elf.read_bytes();entry=struct.unpack_from('<I',raw,24)[0];phoff=struct.unpack_from('<I',raw,28)[0];phsize,phnum=struct.unpack_from('<HH',raw,42);uc=Uc(UC_ARCH_RISCV,UC_MODE_RISCV32);uc.mem_map(0,32*1024*1024)
    for i in range(phnum):
        kind,off,addr,_,n,_,_,_=struct.unpack_from('<8I',raw,phoff+i*phsize)
        if kind==1:uc.mem_write(addr,raw[off:off+n])
    shoff=struct.unpack_from('<I',raw,32)[0];shsize,shnum=struct.unpack_from('<HH',raw,46);symbols={}
    for i in range(shnum):
        sh=struct.unpack_from('<10I',raw,shoff+i*shsize)
        if sh[1]!=2:continue
        strings=struct.unpack_from('<10I',raw,shoff+sh[6]*shsize);text=raw[strings[4]:strings[4]+strings[5]]
        for pos in range(sh[4],sh[4]+sh[5],sh[9]):
            name,value,_,_,_,_=struct.unpack_from('<IIIBBH',raw,pos);symbols[text[name:].split(b'\0',1)[0].decode()]=value
    uc.reg_write(UC_RISCV_REG_SP,0x1fffff0);uc.emu_start(entry,symbols['test_done'],timeout=10000000,count=10000000)
    values=struct.unpack('<'+'i'*count,bytes(uc.mem_read(symbols['test_results'],4*count)))
    (RUN/(label+'-execution.log')).write_text(f'Production PONG + registration/lifecycle statements. Mock WG APIs, no crypto/network. Source SHA256={hashlib.sha256(source.encode()).hexdigest()} ELF SHA256={hashlib.sha256(raw).hexdigest()}\nresults={values}\n')
    assert uc.reg_read(UC_RISCV_REG_PC)==symbols['test_done'];return values
class PeerProbeTests(unittest.TestCase):
    def test_production_sequences(self):
        current=execute(SOURCE.read_text(encoding='utf8'),'current');self.assertEqual(current,(0,)*12)
        before=ROOT/'.artifacts/private/mosaico/peer-pong-before-001.c'
        if before.exists():
            old=execute(before.read_text(encoding='utf8'),'before');self.assertNotEqual(old[0],0);self.assertNotEqual(old[2],0);self.assertNotEqual(old[3],0);self.assertEqual(old[4],0);self.assertNotEqual(old[6],0);self.assertNotEqual(old[7],0)
    def test_lifecycle_order(self):
        s=SOURCE.read_text(encoding='utf8');add=function(s,'static int add_peer(');remove=function(s,'static void remove_peer(')
        self.assertLess(add.index('disco_cancel_peer_probes(evict_idx);'),add.index('wireguardif_remove_peer('))
        self.assertLess(add.index('disco_cancel_peer_probes(idx);'),add.index('memcpy(p->disco_key,'))
        self.assertLess(remove.index('disco_cancel_peer_probes(idx);'),remove.index('wireguardif_remove_peer('))
if __name__=='__main__':unittest.main()
