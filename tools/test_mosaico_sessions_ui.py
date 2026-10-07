"""Native six-slot monitoring classifier/cache/lease tests, not all-thread enumeration."""
import re, subprocess, tempfile, unittest
from pathlib import Path
R=Path(__file__).resolve().parents[1]
V=R/'main/apps/app_codex_micro/view'
CPP=(V/'view_mosaico.cpp').read_text(encoding='utf8')
HDR=(V/'view_mosaico.h').read_text(encoding='utf8')

class SessionsUiTests(unittest.TestCase):
    def test_read_only_page_geometry_and_lifecycle(self):
        self.assertIn('Command = 0, History, Agent, OTA, Settings, Sessions',HDR)
        self.assertIn('Page::Command, Page::History, Page::OTA, Page::Sessions',CPP)
        self.assertIn('(index + (direction > 0 ? 1 : 3)) % 4',CPP)
        for i in range(6):
            x=i%2*224;y=70+i//2*108
            self.assertLessEqual(x+216,440);self.assertLessEqual(y+98,402)
            for dx in (-2,0,2):
                for dy in (-2,0,2):
                    self.assertGreaterEqual(20+x+dx,0);self.assertLessEqual(20+x+216+dx,480)
                    self.assertGreaterEqual(64+y+dy,0);self.assertLessEqual(64+y+98+dy,480)
            for xx,yy,w,h in ((10,12,52,50),(72,18,134,24),(72,52,134,20)):
                self.assertLessEqual(xx+w,216);self.assertLessEqual(yy+h,98)
        sessions=CPP.split('void CodexMicroView::initSessions()',1)[1].split('// All OTA widgets',1)[0]
        for forbidden in ('sendKey','requestJson','fetchHistory','lv_button_create','lv_timer_create','approveInstall','takeRequest'):
            self.assertNotIn(forbidden,sessions)
        self.assertIn('KNOWN %u/6  RUN %s WAIT %s ERR %s',sessions)
        self.assertIn('6 SLOTS / RUN -- WAIT -- ERR --',sessions)
        self.assertNotIn('age >',sessions) # No TTL that invents dead/stale sessions on a live handshake.
        for name in ('setPageForDebug','setInputSuppressed','wakeDisplay','lockDisplay','update'):
            start=CPP.index('CodexMicroView::'+name+'(');body=CPP[start:CPP.index('\n}',start)]
            self.assertIn('refreshSessionsLease()',body)
        self.assertIn('MosaicoSessions::setEnabled(false)',CPP)
        self.assertIn('refreshSessions(state)',CPP)
        self.assertIn('std::min<uint32_t>(60, _displaySettings.effectiveConfig.batteryTimeoutSeconds)',CPP)
        # Timeout consumption must not silently fall back to the persistent BASE.
        update=CPP.split('void CodexMicroView::update(',1)[1]
        self.assertNotIn('_displaySettings.config.batteryTimeoutSeconds',update)

    def test_actual_classifier_cache_and_ram_lease(self):
        compilers=sorted(Path('C:/Espressif/tools/riscv32-esp-elf').glob('*/riscv32-esp-elf/bin/riscv32-esp-elf-g++.exe'))
        if not compilers:self.skipTest('embedded compiler unavailable')
        methods=[]
        for name in ('refreshSessionsLease','refreshSessions'):
            start=CPP.index('void CodexMicroView::'+name+'(')
            body=CPP[start:CPP.index('\n}',start)+2]
            body=body.replace('MosaicoSessions::setEnabled','setLease').replace('MosaicoSessions::snapshot(next)','snapshot(next)')
            body=body.replace('GetHAL().millis()','now')
            methods.append('constexpr '+body)
        code='#include "'+(V/'session_status_model.h').as_posix()+'"\n'+r'''
#include <array>
using namespace mosaico_sessions_ui;
struct Light { uint32_t color=0;float brightness=1;uint8_t effect=0;float speed=0;uint32_t magic=0; };
struct CodexMicroState {
 std::array<Light,6> threads{};bool ready=true,connected=true,protocolReady=true;
 uint8_t knownMask=0;std::array<uint32_t,6> lastThreadStatusMs{};
 uint32_t revision=0,connectionGeneration=1;
};
namespace MosaicoSessions { struct Snapshot { CodexMicroState state{};bool ready=true,starting=false,failed=false; uint32_t lockedRefreshRevision=0; }; }
constexpr Light light(uint32_t c,uint8_t effect=1,float brightness=1) { return {c,brightness,effect,0,0}; }
constexpr bool classification() {
 if(classify(light(0xffffff))!=Status::Idle || classify(light(0xb0b0b0))!=Status::Idle)return false;
 if(classify(light(0x808080))!=Status::Unknown || classify(light(0xff00ff))!=Status::Unknown)return false;
 for(uint8_t effect : {1,2,4,5,6}) {
  if(classify(light(0x0000ff,effect))!=Status::Thinking || classify(light(0x00ff00,effect))!=Status::Complete ||
     classify(light(0xffa500,effect))!=Status::Wait || classify(light(0xff0000,effect))!=Status::Error)return false;
 }
 if(classify(light(0x65b6f0))!=Status::Thinking || classify(light(0x67e7ae))!=Status::Complete ||
    classify(light(0xe9c46a))!=Status::Wait || classify(light(0xe87575))!=Status::Error)return false;
 if(classify(light(0x0000ff,3))!=Status::Unknown || classify(light(0xffffff,7))!=Status::Unknown)return false;
 if(classify(light(0x0000ff,1,0))!=Status::Thinking)return false;
 if(classify(light(0x0000ff,0))!=Status::Unknown || classify(light(0))!=Status::Unknown ||
    classify(light(0,0))!=Status::Unassigned)return false;
 if(classify(light(0x0000ff,1,2))!=Status::Unknown || classify(light(0x0000ff,1,__builtin_nanf("")))!=Status::Unknown)return false;
 auto custom=light(0x0000ff);custom.magic=1;if(classify(custom)!=Status::Thinking)return false;
 custom.magic=0;custom.speed=std::numeric_limits<float>::infinity();if(classify(custom)!=Status::Unknown)return false;
 CodexMicroState s;s.threads[0]=light(0x0000ff);s.threads[1]=light(0xffa500);s.threads[2]=light(0xff0000);
 auto empty=counts(s,true);if(empty.known || empty.running || empty.waiting || empty.errors)return false;
 s.knownMask=7;auto c=counts(s,true);if(c.known!=3 || c.running!=1 || c.waiting!=1 || c.errors!=1)return false;
 CodexMicroState unknown;unknown.knownMask=63;
 for(auto& l:unknown.threads)l=light(0xff00ff);
 auto none=counts(unknown,true);if(none.known || none.running || none.waiting || none.errors)return false;
 auto noCount=counterLabel(none.running,none.known);if(noCount[0]!='-' || noCount[1]!='-')return false;
 unknown.threads[0]=light(0x0000ff);auto partial=counts(unknown,true);
 if(partial.known!=1 || partial.running!=1)return false;
 auto lower=counterLabel(partial.running,partial.known);if(lower[0]!='1' || lower[1]!='+' || lower[2]!='?')return false;
 auto zeroUnknown=counterLabel(partial.waiting,partial.known);if(zeroUnknown[0]!='-')return false;
 for(auto& l:unknown.threads){l=light(0xffffff);l.magic=0xffffffffU;}
 auto all=counts(unknown,true);if(all.known!=6 || all.running)return false;
 auto zeroExact=counterLabel(all.running,all.known);if(zeroExact[0]!='0' || zeroExact[1]!='/' || zeroExact[2]!='6')return false;
 s.connected=false;c=counts(s,true);if(c.live || c.known || c.running)return false;
 s.connected=true;s.protocolReady=false;if(counts(s,true).live)return false;
 s.protocolReady=true;s.connectionGeneration=0;if(counts(s,true).live)return false;
 s.connectionGeneration=1;if(counts(s,false).live)return false;
 return ageSeconds(1500,2000)==0 && ageSeconds(0x20,0xfffffff0U)==0 && ageSeconds(0x1000,0xfffffff0U)==4;
}
struct CodexMicroView {
 enum class Page { Command,History,Agent,OTA,Settings,Sessions };
 Page _page=Page::Sessions;bool _locked=false,_suppressed=false,_rotationFault=false,busy=false,lease=false,validSnapshot=true;
 CodexMicroState _sessionState{};MosaicoSessions::Snapshot _sessionBackend{},bank{};
 uint32_t _sessionsRevision=UINT32_MAX,_sessionsGeneration=UINT32_MAX,_sessionsAgeSecond=UINT32_MAX,now=1000;
 uint32_t _lockSessionsRevision=UINT32_MAX;
 uint8_t _sessionsKnownMask=0;bool _sessionsHadStatus=false;int renders=0,lockRenders=0;Counts rendered{};
 constexpr bool ready(){return true;}
 constexpr bool otaKeepAwake(){return busy || _page==Page::OTA;}
 constexpr void setLease(bool v){lease=v;}
 constexpr bool snapshot(MosaicoSessions::Snapshot& out){if(!validSnapshot)return false;out=bank;return true;}
 constexpr void renderSessions(){++renders;rendered=counts(_sessionState,_sessionBackend.ready && !_sessionBackend.failed && !_sessionBackend.starting);}
 constexpr void renderLockSessions(){++lockRenders;_lockSessionsRevision=_sessionBackend.lockedRefreshRevision;}
 constexpr void refreshSessionsLease();
 constexpr void refreshSessions(const CodexMicroState&);
};
'''+ '\n'.join(methods)+r'''
constexpr bool cacheAndLease() {
 CodexMicroView v;v.refreshSessionsLease();if(!v.lease)return false;
 for(int reason=0;reason<6;++reason) {
  v._locked=reason==0;v._suppressed=reason==1;v._rotationFault=reason==2;v.busy=reason==3;
  v._page=reason==4?CodexMicroView::Page::OTA:reason==5?CodexMicroView::Page::History:CodexMicroView::Page::Sessions;
  v.refreshSessionsLease();if(v.lease)return false;
 }
 v._locked=false;v._suppressed=false;v._rotationFault=false;v.busy=false;v._page=CodexMicroView::Page::Sessions;
 CodexMicroState supplied;supplied.knownMask=1;supplied.revision=4;supplied.threads[0]=light(0x0000ff);
 v.bank.state.knownMask=63;for(auto& l:v.bank.state.threads)l=light(0xff0000); // Module's previous-loop state is not mixed in.
 v.refreshSessions(supplied);if(v.rendered.running!=1 || v.rendered.errors || v.rendered.known!=1)return false;
 int renders=v.renders;v.refreshSessions(supplied);if(v.renders!=renders)return false;
 v.now=100000000;v.refreshSessions(supplied);
 if(v.rendered.running!=1 || !v.rendered.live || stale(true,1))return false; // Last-change age is not a TTL.
 v.validSnapshot=false;v.bank.ready=false;v.refreshSessions(supplied);if(!v.rendered.live)return false;
 supplied.connected=false;supplied.protocolReady=false;supplied.knownMask=0;supplied.revision=5;
 v.refreshSessions(supplied);if(v.rendered.live || v.rendered.known || !v._sessionsHadStatus)return false;
 supplied.connected=true;supplied.protocolReady=true;supplied.connectionGeneration=2;supplied.revision=6;
 v.refreshSessions(supplied);if(v.rendered.known || v.rendered.running)return false; // New generation needs fresh per-slot evidence.
 v.validSnapshot=true;v.bank.ready=true;v.bank.failed=true;supplied.knownMask=1;supplied.threads[0]=light(0x0000ff);
 v.refreshSessions(supplied);if(v.rendered.live || v.rendered.running)return false;
 v._locked=true;v.bank.lockedRefreshRevision=10;v.refreshSessions(supplied);if(v.lockRenders!=1)return false;
 v.refreshSessions(supplied);if(v.lockRenders!=1)return false;
 v.bank.lockedRefreshRevision=11;v.refreshSessions(supplied);if(v.lockRenders!=2)return false;
 v._locked=false;v.bank.lockedRefreshRevision=12;v.refreshSessions(supplied);if(v.lockRenders!=2)return false;
 return true;
}
static_assert(classification(),"default palette/brightness/effects/invalid floats and per-slot evidence are conservative");
static_assert(cacheAndLease(),"coherent supplied state, no fabricated zero, persistent live states and RAM-only leases");
'''
        with tempfile.TemporaryDirectory() as directory:
            source=Path(directory)/'sessions.cpp';source.write_text(code,encoding='utf8')
            result=subprocess.run([str(compilers[-1]),'-std=c++17','-fsyntax-only',str(source)],capture_output=True,text=True)
            log=R/'.artifacts/mosaico/sessions-ui-source-harness.log';log.write_text(result.stdout+result.stderr,encoding='utf8')
            self.assertEqual(result.returncode,0,result.stdout+result.stderr)

if __name__=='__main__':unittest.main()
