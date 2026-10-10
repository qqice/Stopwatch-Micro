"""Production poll AST fault replay + compiler syntax; no SDK/device/network.

The Python interpreter executes the parsed production poll, not a parallel
state-machine model. It is not native C execution or TLS/board acceptance.
Unsupported AST nodes fail the test rather than silently approximating them.
"""
import os
import re
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace as NS
from pycparser import c_parser, c_ast

ROOT = Path(__file__).resolve().parents[1]
BASE = ROOT / 'components/microlink-source/components/microlink'
SOURCE = (BASE / 'src/ml_derp.c').read_text(encoding='utf8')
POLL = SOURCE.split('static int poll_derp_read(', 1)[1].split('\n}\n', 1)[0]
POLL = 'static int poll_derp_read(' + POLL + '\n}\n'
STATE = (BASE / 'include/microlink_internal.h').read_text(encoding='utf8')
STATE = STATE.split(' * DERP Connection State', 1)[1].split('typedef struct {', 1)[1].split('} ml_derp_conn_t;', 1)[0]
CONSTANTS = dict(MBEDTLS_ERR_SSL_WANT_READ=-1, MBEDTLS_ERR_SSL_WANT_WRITE=-2,
                 MBEDTLS_ERR_SSL_TIMEOUT=-3, DERP_FRAME_RECV_PACKET=5,
                 WG_RX_HEADER_SHORT_READ=0, WG_RX_HEADER_RESUMED=1,
                 WG_RX_HEADER_TIMEOUT=2, WG_RX_DERP_READ_EOF=3, NULL=0)
PREAMBLE = '''typedef unsigned char uint8_t; typedef unsigned short uint16_t;
typedef unsigned int uint32_t; typedef unsigned long long uint64_t;
typedef long long int64_t; typedef unsigned int size_t; typedef int bool;
typedef int mbedtls_ssl_context; typedef int mbedtls_ssl_config;
typedef struct { STATE } ml_derp_conn_t;
typedef struct { ml_derp_conn_t derp; } microlink_t;
'''.replace('STATE', STATE)
DEFINES = '\n'.join('#define %s %s' % item for item in CONSTANTS.items())
PROTOTYPES = '''
long long esp_timer_get_time(void); uint64_t ml_get_time_ms(void);
int mbedtls_ssl_read(int *, uint8_t *, size_t);
void wireguardif_rx_count(unsigned); void vTaskDelay(unsigned);
void *ml_psram_malloc(size_t); void *malloc(size_t); void free(void *);
void *memcpy(void *, const void *, size_t);
void dispatch_derp_frame(microlink_t *, uint8_t, uint8_t *, uint8_t *, size_t);
#define pdMS_TO_TICKS(x) (x)
#define ESP_LOGW(...) ((void)0)
'''


def native_harness():
    """Export the unmodified production poll and lifecycle-reset statements."""
    resets = ''
    for production, helper in (('void ml_derp_disconnect(', 'reset_disconnect'),
                               ('esp_err_t ml_derp_connect(', 'reset_connect')):
        block = SOURCE.split(production, 1)[1].split(
            'memset(ml->derp.rx_header, 0, sizeof(ml->derp.rx_header));', 1)[0]
        block += 'memset(ml->derp.rx_header, 0, sizeof(ml->derp.rx_header));'
        block = block[block.index('    ml->derp.rx_header_have = 0;'):]
        resets += 'static void '+helper+'(microlink_t *ml) {\n'+block+'\n}\n'
    prefix = '''/* Generated from production source. No TLS/network/SDK operations. */
#include <stdint.h>
#include <stddef.h>
#include <stdbool.h>
#include <stdlib.h>
#include <string.h>
#include <assert.h>
#include <stdio.h>
typedef int mbedtls_ssl_context;
typedef int mbedtls_ssl_config;
typedef struct { STATE } ml_derp_conn_t;
typedef struct { ml_derp_conn_t derp; } microlink_t;
static int outstanding;
static void *tracked_malloc(size_t n) {
    void *p = malloc(n); if (p) ++outstanding; return p;
}
static void tracked_free(void *p) {
    if (p) { assert(outstanding > 0); --outstanding; } free(p);
}
#define malloc tracked_malloc
#define free tracked_free
'''.replace('STATE', STATE)
    definitions = '\n'.join('#define %s %s' % item for item in CONSTANTS.items() if item[0] != 'NULL')
    mocks = '''
typedef struct { int result; uint8_t bytes[64]; long long elapsed; } read_event;
static read_event events[64];
static unsigned head, tail, read_calls, counts[4], frames;
static uint8_t last_type, last_payload[64];
static size_t last_length;
static long long now_us;
static microlink_t client;
long long esp_timer_get_time(void) { return now_us; }
uint64_t ml_get_time_ms(void) { return (uint64_t)(now_us / 1000); }
void wireguardif_rx_count(unsigned field) { assert(field < 4); ++counts[field]; }
void vTaskDelay(unsigned ms) { now_us += (long long)ms * 1000; }
void *ml_psram_malloc(size_t length) { return tracked_malloc(length); }
int mbedtls_ssl_read(int *ssl, uint8_t *out, size_t remaining) {
    (void)ssl; assert(head < tail); ++read_calls;
    read_event *e = &events[head++]; now_us += e->elapsed;
    if (e->result > 0) {
        assert((size_t)e->result <= remaining);
        memcpy(out, e->bytes, (size_t)e->result);
    }
    return e->result;
}
void dispatch_derp_frame(microlink_t *ml, uint8_t type, uint8_t *key,
                         uint8_t *payload, size_t length) {
    (void)ml; (void)key; assert(length <= sizeof(last_payload));
    ++frames; last_type = type; last_length = length;
    if (length) memcpy(last_payload, payload, length);
    tracked_free(payload);
}
static void fixture(void) {
    assert(outstanding == 0); memset(&client, 0, sizeof(client));
    client.derp.connected = true; client.derp.sockfd = 1;
    memset(counts, 0, sizeof(counts)); frames = 0; last_length = 0;
    head = tail = read_calls = 0; now_us = 0;
}
static void push(int result, const void *bytes, long long elapsed) {
    assert(tail < 64); read_event *e = &events[tail++];
    e->result = result; e->elapsed = elapsed;
    if (result > 0) { assert(result <= 64); memcpy(e->bytes, bytes, (size_t)result); }
}
'''
    cases = r'''
int main(void) {
    const uint8_t h[5] = {7,0,0,0,0};
    const uint8_t body_h[5] = {7,0,0,0,3};
    fixture(); push(1,h,0); assert(poll_derp_read(&client)==0);
    push(4,h+1,0); assert(poll_derp_read(&client)==1);
    assert(frames==1 && last_type==7 && counts[0]==1 && counts[1]==1);
    assert(client.derp.rx_header_have==0 && client.derp.rx_header_first_us==0);

    fixture(); push(2,h,0); assert(poll_derp_read(&client)==0);
    for (unsigned i=0;i<4;++i) {
        push(MBEDTLS_ERR_SSL_TIMEOUT,NULL,100000);
        assert(poll_derp_read(&client)==0 && client.derp.rx_header_have==2);
    }
    push(3,h+2,0); assert(poll_derp_read(&client)==1);
    assert(counts[0]==1 && counts[1]==1 && counts[2]==0 && counts[3]==0);

    fixture(); now_us=20000000;
    for (int status=-1;status>=-3;--status) {
        push(status,NULL,6000000); assert(poll_derp_read(&client)==0);
    }
    assert(client.derp.rx_header_have==0 && counts[2]==0 && counts[3]==0);

    for (unsigned partial=0;partial<2;++partial) {
        for (int fatal=0;fatal>=-99;fatal-=99) {
            fixture();
            if (partial) { push(1,h,0); assert(poll_derp_read(&client)==0); }
            push(fatal,NULL,0); assert(poll_derp_read(&client)<0);
            assert(frames==0 && counts[3]==(unsigned)(fatal==0));
            reset_disconnect(&client); assert(client.derp.rx_header_have==0);
            assert(client.derp.rx_header_first_us==0);
            push(5,h,0); assert(poll_derp_read(&client)==1);
        }
    }
    fixture(); push(1,h,0); assert(poll_derp_read(&client)==0);
    now_us=5000001; assert(poll_derp_read(&client)<0);
    assert(read_calls==1 && counts[2]==1);
    fixture(); push(1,h,0); assert(poll_derp_read(&client)==0);
    now_us=4999999; push(4,h+1,2); assert(poll_derp_read(&client)<0);
    assert(counts[2]==1 && frames==0);

    fixture(); push(1,body_h,0); assert(poll_derp_read(&client)==0);
    now_us=4999000; push(4,body_h+1,0); push(-3,NULL,2000);
    assert(poll_derp_read(&client)<0 && outstanding==0 && frames==0);
    fixture(); push(1,body_h,0); assert(poll_derp_read(&client)==0);
    now_us=4999000; push(4,body_h+1,0); push(3,"abc",500000);
    assert(poll_derp_read(&client)<0 && outstanding==0 && frames==0 && counts[3]==0);
    fixture(); push(5,body_h,0); push(2,"ab",0); push(-3,NULL,0); push(1,"c",0);
    assert(poll_derp_read(&client)==1 && outstanding==0 && frames==1);
    assert(last_length==3 && memcmp(last_payload,"abc",3)==0 && counts[3]==0);
    for (int fatal=0;fatal>=-99;fatal-=99) {
        fixture(); push(5,body_h,0); push(1,"a",0); push(fatal,NULL,0);
        assert(poll_derp_read(&client)<0 && outstanding==0 && frames==0);
        assert(counts[3]==(unsigned)(fatal==0)); reset_disconnect(&client);
        push(5,h,0); assert(poll_derp_read(&client)==1);
        assert(counts[3]==(unsigned)(fatal==0));
    }

    const uint8_t too_large[5]={7,0,1,0,1};
    const uint8_t signed_large[5]={7,128,0,0,0};
    fixture(); push(5,too_large,0); assert(poll_derp_read(&client)<0);
    assert(outstanding==0 && frames==0);
    fixture(); push(5,signed_large,0); assert(poll_derp_read(&client)<0);
    assert(outstanding==0 && frames==0);

    fixture(); microlink_t other=client; const uint8_t other_h[5]={8,0,0,0,0};
    push(1,h,0); assert(poll_derp_read(&client)==0);
    push(2,other_h,0); assert(poll_derp_read(&other)==0);
    push(4,h+1,0); assert(poll_derp_read(&client)==1 && last_type==7);
    push(3,other_h+2,0); assert(poll_derp_read(&other)==1 && last_type==8);
    push(5,h,0); assert(poll_derp_read(&client)==1 && frames==3);

    fixture(); push(2,h,0); assert(poll_derp_read(&client)==0);
    now_us=9000000; reset_connect(&client);
    assert(client.derp.rx_header_have==0 && client.derp.rx_header_first_us==0);
    for (unsigned i=0;i<5;++i) assert(client.derp.rx_header[i]==0);
    push(5,other_h,0); assert(poll_derp_read(&client)==1 && last_type==8);
    assert(outstanding==0);
    puts("PASS production poll native faultshim: fragments, timeout, idle, EOF/error, shared-budget, oversize, frames, reset");
    return 0;
}
'''
    # Native libc already declares memcpy; Darwin may wrap it in a secure macro.
    native_prototypes = PROTOTYPES.replace('void *memcpy(void *, const void *, size_t);\n', '')
    return prefix + definitions + '\n' + native_prototypes + mocks + POLL + resets + cases


class Ptr:
    def __init__(self, data, offset=0): self.data, self.offset = data, offset
    def __add__(self, n): return Ptr(self.data, self.offset + n)
    def __getitem__(self, n): return self.data[self.offset + n]
    def __setitem__(self, n, val): self.data[self.offset + n] = val


class Returned(Exception):
    pass


class Continued(Exception):
    pass


class Replay:
    """Tiny strict interpreter for the actual poll's C AST and fault TLS BIO."""
    def __init__(self):
        text = re.sub(r'/\*.*?\*/', '', PREAMBLE + POLL, flags=re.S)
        self.ast = c_parser.CParser().parse(text).ext[-1].body
        self.ml = NS(derp=NS(connected=1, sockfd=1, ssl=0, rx_header=Ptr([0]*5),
                            rx_header_have=0, rx_header_first_us=0))
        self.now = 0
        self.events = []
        self.counts = [0]*4
        self.frames = []
        self.reads = 0
        self.live = []
    def read(self, ssl, out, remaining):
        self.reads += 1
        if not self.events: raise AssertionError('unexpected TLS read')
        event = self.events.pop(0)
        if isinstance(event, tuple):
            event, elapsed = event
            self.now += elapsed
        if isinstance(event, int): return event
        assert len(event) <= remaining, 'shim honors requested TLS size'
        for n, val in enumerate(event): out[n] = val
        return len(event)
    def allocate(self, length):
        p = Ptr([0]*length); self.live.append(p); return p
    def release(self, p): self.live.remove(p)
    def copy(self, dst, src, length):
        for n in range(length): dst[n] = src[n]
        return dst
    def dispatch(self, ml, kind, key, payload, length):
        self.frames.append((kind, bytes(payload[n] for n in range(length))))
        if payload: self.release(payload)
    def count(self, n): self.counts[n] += 1
    def delay(self, ms): self.now += ms*1000
    def poll(self, *events):
        self.events.extend(events)
        self.env = dict(CONSTANTS, ml=self.ml)
        self.env.update(esp_timer_get_time=lambda:self.now, ml_get_time_ms=lambda:self.now//1000,
                        mbedtls_ssl_read=self.read, wireguardif_rx_count=self.count,
                        ml_psram_malloc=self.allocate, malloc=self.allocate, free=self.release,
                        memcpy=self.copy, dispatch_derp_frame=self.dispatch,
                        vTaskDelay=self.delay, pdMS_TO_TICKS=lambda n:n, ESP_LOGW=lambda *a:None,
                        TAG=0)
        try: self.run(self.ast)
        except Returned as e: return e.args[0]
        raise AssertionError('production function fell through')
    def set(self, node, val):
        if isinstance(node, c_ast.ID): self.env[node.name] = val
        elif isinstance(node, c_ast.StructRef): setattr(self.eval(node.name), node.field.name, val)
        elif isinstance(node, c_ast.ArrayRef): self.eval(node.name)[self.eval(node.subscript)] = val
        else: raise AssertionError(type(node))
    def eval(self, n):
        if isinstance(n, c_ast.ID): return self.env[n.name]
        if isinstance(n, c_ast.Constant):
            if n.type == 'string': return n.value
            return int(re.sub('[uUlL]+$', '', n.value), 0)
        if isinstance(n, c_ast.StructRef): return getattr(self.eval(n.name), n.field.name)
        if isinstance(n, c_ast.ArrayRef): return self.eval(n.name)[self.eval(n.subscript)]
        if isinstance(n, c_ast.Cast): return self.eval(n.expr)
        if isinstance(n, c_ast.UnaryOp):
            v = self.eval(n.expr)
            if n.op == 'sizeof': return len(v.data)
            if n.op == '!': return int(not v)
            if n.op == '-': return -v
            if n.op == '&': return v
            raise AssertionError(n.op)
        if isinstance(n, c_ast.BinaryOp):
            a = self.eval(n.left)
            if n.op == '&&': return int(bool(a) and bool(self.eval(n.right)))
            if n.op == '||': return int(bool(a) or bool(self.eval(n.right)))
            b = self.eval(n.right)
            ops = {'+':lambda:a+b, '-':lambda:a-b, '/':lambda:a//b,
                   '<':lambda:a<b, '>':lambda:a>b, '<=':lambda:a<=b, '>=':lambda:a>=b,
                   '==':lambda:a==b, '!=':lambda:a!=b, '|':lambda:a|b, '<<':lambda:a<<b}
            return ops[n.op]()
        if isinstance(n, c_ast.TernaryOp):
            return self.eval(n.iftrue if self.eval(n.cond) else n.iffalse)
        if isinstance(n, c_ast.FuncCall):
            return self.eval(n.name)(*[self.eval(x) for x in (n.args.exprs if n.args else [])])
        raise AssertionError(type(n))
    def run(self, n):
        if isinstance(n, c_ast.Compound):
            for item in n.block_items or []: self.run(item)
        elif isinstance(n, c_ast.Decl):
            if isinstance(n.type, c_ast.ArrayDecl): val = Ptr([0]*self.eval(n.type.dim))
            else: val = self.eval(n.init) if n.init else 0
            self.env[n.name] = val
        elif isinstance(n, c_ast.If):
            branch = n.iftrue if self.eval(n.cond) else n.iffalse
            if branch: self.run(branch)
        elif isinstance(n, c_ast.Assignment):
            val = self.eval(n.rvalue)
            if n.op == '+=': val = self.eval(n.lvalue) + val
            else: assert n.op == '='
            self.set(n.lvalue, val)
        elif isinstance(n, c_ast.While):
            for _ in range(2000):
                if not self.eval(n.cond): break
                try: self.run(n.stmt)
                except Continued: continue
            else: raise AssertionError('unbounded production loop')
        elif isinstance(n, c_ast.Continue): raise Continued()
        elif isinstance(n, c_ast.Return): raise Returned(self.eval(n.expr))
        elif isinstance(n, c_ast.FuncCall): self.eval(n)
        else: raise AssertionError(type(n))


class PartialHeaderTests(unittest.TestCase):
    def test_fragments_and_idle(self):
        for parts in ((1,4), (2,3), (1,1,1,1,1)):
            r = Replay(); r.now = 20000000
            for status in (-1,-2,-3): self.assertEqual(r.poll(status),0)
            self.assertEqual(r.ml.derp.rx_header_have,0)
            header = bytes([7,0,0,0,0]); pos = 0
            for n in parts:
                self.assertEqual(r.poll(header[pos:pos+n]),int(pos+n == 5)); pos += n
                if pos < 5:
                    for status in (-3,-1,-2,-3): self.assertEqual(r.poll(status),0)
            self.assertEqual(r.frames,[(7,b'')]); self.assertFalse(r.live)
            self.assertEqual(r.counts,[len(parts)-1,1,0,0])
            self.assertEqual(r.ml.derp.rx_header_first_us,0)
    def test_eof_error_and_deadline(self):
        for partial in (False,True):
            for status in (0,-99):
                r=Replay()
                if partial: self.assertEqual(r.poll(b'\x07'),0)
                self.assertLess(r.poll(status),0); self.assertFalse(r.frames)
                self.assertEqual(r.counts[3],int(status == 0))
        r=Replay(); r.poll(b'\x07'); r.now=5000001
        self.assertLess(r.poll(),0); self.assertEqual(r.reads,1); self.assertEqual(r.counts[2],1)
        r=Replay(); r.poll(b'\x07'); r.now=4999999
        self.assertLess(r.poll((b'\0'*4,2)),0); self.assertEqual(r.counts[2],1)
    def test_connections_frames_oversize_bodyguard(self):
        a,b=Replay(),Replay(); a.poll(b'\x07'); b.poll(b'\x08\0')
        self.assertEqual(a.poll(b'\0'*4),1); self.assertEqual(b.poll(b'\0'*3),1)
        self.assertEqual(a.poll(b'\x09\0\0\0\0'),1)
        self.assertEqual(a.frames,[(7,b''),(9,b'')]); self.assertEqual(b.frames,[(8,b'')])
        for header in (b'\x07\0\x01\0\x01', b'\x07\x80\0\0\0'):
            r=Replay(); self.assertLess(r.poll(header),0); self.assertFalse(r.live)
        r=Replay(); self.assertEqual(r.poll(b'\x07\0\0\0\x03',b'ab',-3,b'c'),1)
        self.assertEqual(r.frames,[(7,b'abc')]); self.assertFalse(r.live)
        r=Replay(); self.assertEqual(r.poll(b'\x05\0\0\0\x21',b'k'*32+b'z'),1)
        self.assertEqual(r.frames,[(5,b'z')]); self.assertFalse(r.live)
        r=Replay(); r.poll(b'\x07'); r.now=4999000
        self.assertLess(r.poll(b'\0\0\0\x03',(-3,2000)),0)
        self.assertFalse(r.live); self.assertFalse(r.frames)
        r=Replay(); r.poll(b'\x07'); r.now=4999000
        self.assertLess(r.poll(b'\0\0\0\x03',(b'abc',500000)),0)
        self.assertFalse(r.live); self.assertFalse(r.frames); self.assertEqual(r.counts[3],0)
        for fatal in (0,-99):
            r=Replay(); self.assertLess(r.poll(b'\x07\0\0\0\x03',b'a',fatal),0)
            self.assertFalse(r.live); self.assertFalse(r.frames)
            self.assertEqual(r.counts[3],int(fatal == 0))
    def test_production_generation_resets(self):
        # Execute the actual reset assignments from both public lifecycle functions.
        for name in ('void ml_derp_disconnect(', 'esp_err_t ml_derp_connect('):
            block=SOURCE.split(name,1)[1].split('memset(ml->derp.rx_header, 0, sizeof(ml->derp.rx_header));',1)[0]
            block += 'memset(ml->derp.rx_header, 0, sizeof(ml->derp.rx_header));'
            reset=block[block.index('    ml->derp.rx_header_have = 0;'):]
            r=Replay(); r.poll(b'\x07\0'); r.now=9000000
            ast=c_parser.CParser().parse(re.sub(r'/\*.*?\*/', '', PREAMBLE+'void reset(microlink_t *ml) { '+reset+' }', flags=re.S)).ext[-1].body
            def clear(ptr, value, size):
                for i in range(size): ptr[i]=value
            r.env={'ml':r.ml,'memset':clear}; r.run(ast)
            self.assertEqual(r.ml.derp.rx_header.data,[0]*5)
            self.assertEqual(r.poll(b'\x09\0\0\0\0'),1); self.assertEqual(r.frames,[(9,b'')])
    def test_production_syntax(self):
        cc = os.environ.get('DERP_SYNTAX_CC') or next(iter(Path.home().glob(
            '.espressif/tools/riscv32-esp-elf/*/riscv32-esp-elf/bin/riscv32-esp-elf-gcc.exe')),None)
        if not cc: self.skipTest('riscv32 compiler unavailable; syntax NOT verified')
        with tempfile.TemporaryDirectory(prefix='derp-poll-syntax-') as d:
            p=Path(d)/'poll.c'; p.write_text(PREAMBLE+DEFINES+'\n'+PROTOTYPES+POLL,encoding='utf8')
            subprocess.run([str(cc),'-std=c11','-Wall','-Wextra','-Werror','-Wno-unused-function',
                            '-fsyntax-only',str(p)],check=True)
    def test_native_c_runtime(self):
        cc=os.environ.get('DERP_TEST_CC') or shutil.which('cc') or shutil.which('gcc') or shutil.which('clang')
        if not cc: self.skipTest('host compiler unavailable: native C runtime NOT run; AST replay only')
        with tempfile.TemporaryDirectory(prefix='derp-native-') as d:
            src=Path(d)/'poll.c'; exe=Path(d)/('poll.exe' if os.name=='nt' else 'poll')
            src.write_text(native_harness(),encoding='utf8')
            subprocess.run([cc,'-std=c11','-Wall','-Wextra','-Werror',str(src),'-o',str(exe)],check=True)
            subprocess.run([str(exe)],check=True)


if __name__ == '__main__':
    import sys
    if len(sys.argv)==3 and sys.argv[1]=='--export-native-harness':
        Path(sys.argv[2]).write_text(native_harness(),encoding='utf8')
    else: unittest.main()
