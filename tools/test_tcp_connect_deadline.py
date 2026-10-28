"""Exact production connect deadline + numeric snapshots, deterministic native clocks."""
import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path
from test_vpn_socket_bind import native_harness as vpn_harness, SOURCE
from test_derp_tx_backpressure import function, BASE


def native_harness():
    old = vpn_harness().replace('int main(void){', 'int vpn_main(void){')
    return old + r'''
static microlink_tcp_connect_diag_t snapshot(void){microlink_tcp_connect_diag_t d;microlink_tcp_get_last_connect(&d);return d;}
static void completed(int stage,int error,int max_ms){
 microlink_tcp_connect_diag_t d=snapshot();
 assert(d.peer_ip==0x64400002&&d.peer_port==80&&d.stage==stage&&d.error==error);
 assert(d.elapsed_ms<=(unsigned)max_ms&&now_us<=(int64_t)max_ms*1000);
 assert(!d.peer_snapshot_valid&&d.wg_index==-1&&!d.keypair_valid&&!d.peer_key_match_valid);
 assert(d.netif_index==(sockets?2:0));
}
int main(void){
 vpn_main();
 microlink_t m=reset();tunnel_ready=0;
 assert(!microlink_tcp_connect(&m,0x64400002,80,7000)&&errno==ETIMEDOUT&&!sockets);
 completed(ML_TCP_CONNECT_WG_WAIT,ETIMEDOUT,7000);
 microlink_tcp_connect_diag_t d=snapshot();assert(d.wg_wait_ms==7000&&!d.wg_ready_before&&!d.wg_ready_after&&!d.first_error&&!d.retry_error);
 unsigned id=d.request_id;
 /* Read-only snapshots: no timer/owner/socket activity, and returned copy cannot mutate it. */
 int dispatch=dispatch_calls;int64_t at=now_us;d.error=99;
 microlink_tcp_get_last_connect(NULL);d=snapshot();assert(d.error==ETIMEDOUT&&dispatch==dispatch_calls&&at==now_us);
 m=reset();tunnel_ready=0;assert(!microlink_tcp_connect(&m,0x64400002,80,0));
 completed(ML_TCP_CONNECT_WG_WAIT,ETIMEDOUT,15000);assert(now_us==15000000&&snapshot().request_id==id+1);
 /* Sub-tick budget never sleeps one tick beyond it or creates a socket. */
 m=reset();tunnel_ready=0;assert(!microlink_tcp_connect(&m,0x64400002,80,1)&&errno==ETIMEDOUT&&!sockets&&now_us==0);
 /* Handshake's own time consumes the shared deadline. */
 m=reset();tunnel_ready=0;handshake_us=7000000;assert(!microlink_tcp_connect(&m,0x64400002,80,7000)&&!sockets);completed(ML_TCP_CONNECT_WG_WAIT,ETIMEDOUT,7000);
 m=reset();async_connect=1;select_duration_us=20000000;
 assert(!microlink_tcp_connect(&m,0x64400002,80,7000)&&errno==ETIMEDOUT&&closed==2&&sockets==2&&pins==2&&binds==2&&now_us==7000000);
 completed(ML_TCP_CONNECT_RETRY,ETIMEDOUT,7000);assert(snapshot().first_error==ETIMEDOUT&&snapshot().retry_error==ETIMEDOUT&&snapshot().wg_ready_before&&snapshot().wg_ready_after);
 /* WG wait + TCP share 7s, not each their own 7s. */
 m=reset();tunnel_ready=0;ready_at_us=2000000;async_connect=1;select_duration_us=20000000;
 assert(!microlink_tcp_connect(&m,0x64400002,80,7000)&&closed==2&&sockets==2&&pins==2&&binds==2);assert(now_us==7000000&&snapshot().wg_wait_ms==2000&&snapshot().first_error==ETIMEDOUT&&snapshot().retry_error==ETIMEDOUT);
 /* First timeout consumes only half of the TCP phase, not the full request. */
 m=reset();async_connect=1;select_duration_us=20000000;first_select_only=1;
 microlink_tcp_socket_t *s=microlink_tcp_connect(&m,0x64400002,80,7000);
 assert(s&&now_us==3500000&&sockets==2&&pins==2&&binds==2&&closed==1&&!physical);
 completed(ML_TCP_CONNECT_SUCCESS,0,7000);assert(snapshot().first_error==ETIMEDOUT&&!snapshot().retry_error&&!handshakes&&!cmms);close(s->fd);free(s);
 /* A distinct second failure retains both errno values and closes both fds. */
 m=reset();async_connect=1;select_duration_us=20000000;first_select_only=2;
 assert(!microlink_tcp_connect(&m,0x64400002,80,7000)&&errno==ECONNREFUSED&&closed==2&&pins==2&&binds==2&&now_us==3500000);
 completed(ML_TCP_CONNECT_RETRY,ECONNREFUSED,7000);assert(snapshot().first_error==ETIMEDOUT&&snapshot().retry_error==ECONNREFUSED);
 /* WG preparation uses 2s; only the remaining 5s is split for TCP. */
 m=reset();tunnel_ready=0;ready_at_us=2000000;async_connect=1;select_duration_us=20000000;first_select_only=1;
 s=microlink_tcp_connect(&m,0x64400002,80,7000);assert(s&&now_us==4500000&&snapshot().wg_wait_ms==2000&&handshakes==1&&cmms==1&&pins==2&&binds==2);close(s->fd);free(s);
 /* Tiny TCP budgets deliberately remain single-attempt, never zero waits. */
 m=reset();async_connect=1;select_duration_us=20000000;
 assert(!microlink_tcp_connect(&m,0x64400002,80,1)&&errno==ETIMEDOUT&&closed==1&&sockets==1&&now_us==1000&&!snapshot().retry_error);
 m=reset();async_connect=1;select_duration_us=20000000;
 assert(!microlink_tcp_connect(&m,0x64400002,80,19)&&closed==1&&sockets==1&&now_us==19000);
 m=reset();async_connect=1;select_duration_us=20000000;
 assert(!microlink_tcp_connect(&m,0x64400002,80,20)&&closed==2&&sockets==2&&now_us==20000);
 /* A peer that actually went down still gets bounded preparation, never
  * a fixed delay past the request deadline or an unpinned fallback socket. */
 m=reset();connect_fail=1;peer_down_at_connect=1;
 assert(!microlink_tcp_connect(&m,0x64400002,80,1000)&&errno==ETIMEDOUT&&sockets==1&&closed==1&&now_us==1000000&&handshakes==1&&cmms==1);
 completed(ML_TCP_CONNECT_RETRY_WAIT,ETIMEDOUT,1000);assert(snapshot().first_error==ENETUNREACH&&!snapshot().retry_error&&!physical);
 /* Immediate retriable failures on an up peer need no backoff or rekey. */
 m=reset();connect_fail=1;s=microlink_tcp_connect(&m,0x64400002,80,7000);
 assert(s&&sockets==2&&closed==1&&pins==2&&binds==2&&now_us==0&&!flags_by_fd[2]&&!handshakes&&!cmms);
 completed(ML_TCP_CONNECT_SUCCESS,0,7000);assert(snapshot().first_error==ENETUNREACH&&!snapshot().retry_error);close(s->fd);free(s);
 /* Healthy established sessions cause zero extra handshake or CMM traffic. */
 m=reset();handshake_us=7000000;s=microlink_tcp_connect(&m,0x64400002,80,7000);
 assert(s&&!handshakes&&!cmms&&now_us==0&&sockets==1&&pins==1&&binds==1);close(s->fd);free(s);
 /* Retry errno retained independently; no Linux errno numeric assumptions. */
 m=reset();connect_fail=-1;assert(!microlink_tcp_connect(&m,0x64400002,80,7000)&&errno==ENETUNREACH&&closed==2);
 completed(ML_TCP_CONNECT_RETRY,ENETUNREACH,7000);assert(snapshot().retry_error==ENETUNREACH);
 /* SO_ERROR failure, select error, EINTR and restored blocking flags. */
 m=reset();async_connect=1;select_errno=ECONNABORTED;
 assert(!microlink_tcp_connect(&m,0x64400002,80,7000)&&errno==ECONNABORTED&&closed==1&&sockets==1);completed(ML_TCP_CONNECT_FIRST,ECONNABORTED,7000);
 m=reset();async_connect=1;select_error=1;select_errno=EBADF;
 assert(!microlink_tcp_connect(&m,0x64400002,80,7000)&&errno==EBADF&&closed==1);
 m=reset();async_connect=1;getsock_error=1;
 assert(!microlink_tcp_connect(&m,0x64400002,80,7000)&&errno==EIO&&closed==1);
 m=reset();async_connect=1;select_interrupt=1;select_duration_us=300000;
 s=microlink_tcp_connect(&m,0x64400002,80,7000);assert(s&&now_us==301000&&!flags_by_fd[1]&&errno==0);close(s->fd);free(s);
 m=reset();restore_error=1;assert(!microlink_tcp_connect(&m,0x64400002,80,7000)&&errno==EIO&&closed==1);
 m=reset();bind_fail=1;assert(!microlink_tcp_connect(&m,0x64400002,80,7000)&&errno==EADDRNOTAVAIL&&closed==1&&!connects);completed(ML_TCP_CONNECT_BIND,EADDRNOTAVAIL,7000);
 /* uint32 timeout multiplication must not wrap. */
 m=reset();async_connect=1;select_duration_us=16000000;s=microlink_tcp_connect(&m,0x64400002,80,UINT32_MAX);assert(s&&now_us==16000000);close(s->fd);free(s);
 puts("TCP connect exact production deadline/snapshot mock: PASS");return 0;
}
'''


class Tests(unittest.TestCase):
    def test_source_boundaries(self):
        s = SOURCE.read_text(encoding='utf8')
        self.assertNotIn('attempting connect anyway', s)
        self.assertIn('SO_ERROR', function(s, 'ml_tcp_connect_until'))
        self.assertIn('O_NONBLOCK', function(s, 'ml_tcp_connect_until'))
        self.assertNotIn('SO_SNDTIMEO', function(s, 'ml_tcp_connect_until'))
        getter = function(s, 'microlink_tcp_get_last_connect')
        for forbidden in ('socket(', 'owner_call', 'ml_wg', 'malloc', 'esp_timer'):
            self.assertNotIn(forbidden, getter)
        publish = function(s, 'ml_tcp_publish')
        self.assertNotIn('owner_call', publish)
        for forbidden in ('peer_count', '->peers', 'public_key', 'wireguard_device'):
            self.assertNotIn(forbidden, s)
        root = BASE.parents[3]
        serial = (root/'main/debug/serial_debug.cpp').read_text(encoding='utf8')
        output = serial.split('DBG TCP_CONNECT ',1)[1].split(');',1)[0]
        self.assertNotIn('%s', output)
        self.assertNotIn('char ', output)

    def test_native(self):
        cc = os.environ.get('VPN_TEST_CC') or shutil.which('cc') or shutil.which('gcc') or shutil.which('clang')
        if not cc:
            self.skipTest('native host compiler unavailable; export harness to compiler host')
        with tempfile.TemporaryDirectory() as d:
            p=Path(d)/'tcp.c';exe=Path(d)/('tcp.exe' if os.name=='nt' else 'tcp')
            p.write_text(native_harness(),encoding='utf8')
            subprocess.run([cc,'-std=c11','-Wall','-Wextra','-Werror','-Wno-unused-variable',str(p),'-o',str(exe)],check=True)
            subprocess.run([str(exe)],check=True)

    def test_syntax(self):
        cc = next(iter(Path.home().glob('.espressif/tools/riscv32-esp-elf/*/riscv32-esp-elf/bin/riscv32-esp-elf-gcc.exe')), None)
        if not cc:self.skipTest('syntax compiler unavailable')
        with tempfile.TemporaryDirectory() as d:
            p=Path(d)/'tcp.c';p.write_text(native_harness(),encoding='utf8')
            subprocess.run([str(cc),'-std=c11','-Wall','-Wextra','-Werror','-Wno-unused-variable','-fsyntax-only',str(p)],check=True)

if __name__ == '__main__':
    import sys
    if len(sys.argv)==3 and sys.argv[1]=='--export-native-harness':
        Path(sys.argv[2]).write_text(native_harness(),encoding='utf8')
    else:unittest.main()

