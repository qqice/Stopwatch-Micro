"""Compile actual pure coordinator with constexpr fault injection; never touches hardware.

Uses native g++/clang++ when available, otherwise installed ESP cross compiler.
No linking, target build, serial operations, or model reimplementation.
"""
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
HARNESS = r'''
#include "main/hal/mosaico/mosaico_fcc_limit_repair_model.h"
using namespace mosaico_fcc_limit;
struct Fake {
    int step=0, failAt=0, unseal=0, full=0, config=0, fcc=0, exits=0, seals=0;
    int reads=0, verifies=0, mode=0, dc=0, sealWires=0;
    bool forceExitFail=false, unreadableAfterExit=false;
    bool cold=false, grantCold=true;
    bool violation=false, durable=false, loseAck=false, stickySave=false, lagSeen=false;
    bool saveBroken=false;
    Journal disk{};
    Security sec{3,false,false,true};
    Dm dm{0x0020,65,100};
    Quiet q{0x0026,95,4050,65,100,2832,-3,3};
    constexpr bool tick() { return ++step != failAt; }
    constexpr Load journalLoad(const char* key, Journal& j) {
        if ((key[4]=='b')!=cold) violation=true;
        if (!tick()) return Load::Invalid;
        j=disk; return durable ? Load::Present : Load::Absent;
    }
    constexpr bool journalSaveReadback(const char* key, const Journal& j) {
        if ((key[4]=='b')!=cold) violation=true;
        bool ok=tick();
        if (!ok && stickySave) saveBroken=true;
        if (saveBroken) return false;
        if(ok || loseAck) {disk=j; durable=true;}
        return ok;
    }
    constexpr bool identity(Identity& i) {
        i.device=0x0220; i.mac[0]=42;
        i.authorizedColdProfile=cold && grantCold; i.coldOriginalConfig=0x0020;
        i.historicalNominalProof=i.historicalAccessProof=true;
        if(mode==1) i.device=0x9999;
        if(mode==2) i.historicalAccessProof=false;
        return tick();
    }
    constexpr bool quietSnapshot(Quiet& out) { out=q; return tick(); }
    constexpr bool observedSecurity(Security& out) {
        out=sec; return tick() && !(unreadableAfterExit && exits);
    }
    constexpr bool authorized(unsigned bit) {
        if (!durable || !(disk.intents & bit) || disk.mac[0]!=42) violation=true;
        return tick();
    }
    constexpr bool sendUnsealPair() {
        ++unseal; bool ok=authorized(Unseal);
        if(ok || loseAck) sec.sec=2;
        return ok;
    }
    constexpr bool sendFullPair() {
        ++full; bool ok=authorized(Full);
        if(ok || loseAck) sec.sec=1;
        return ok;
    }
    constexpr bool dmSnapshot(Dm& out) {
        ++reads; out=dm;
        if(mode==3 && reads==2) ++out.dc;
        return tick();
    }
    constexpr bool enterCfg() {
        bool ok=authorized(Enter); if(ok || loseAck) sec.cfg=true; return ok;
    }
    constexpr bool waitCfg() { return tick(); }
    constexpr bool writeConfigOnly(uint16_t value) {
        ++config;
        if (sec.sec!=1 || !sec.cfg || (dm.dc!=65 && !(cold && dm.dc==3000)) || !disk.backupValid ||
            (value!=disk.before.config && value!=disk.target.config)) violation=true;
        bool ok=authorized(value==disk.before.config ? RestoreConfig : Config);
        if(ok || loseAck) dm.config=value;
        if(mode==4) { sec.sec=3; sec.cfg=false; } // unexpected auto seal
        return ok;
    }
    constexpr bool writeFccOnly(uint16_t value) {
        ++fcc;
        if(sec.sec!=1 || !sec.cfg || (dm.dc!=65 && !(cold && dm.dc==3000)) || !disk.backupValid ||
            (value!=disk.before.fcc && value!=disk.target.fcc)) violation=true;
        bool ok=authorized(value==disk.before.fcc ? RestoreFcc : Fcc);
        if(ok || loseAck) dm.fcc=value;
        return ok;
    }
    constexpr bool writeDcOnly(uint16_t value) {
        ++dc;
        if(!cold || sec.sec!=1 || !sec.cfg || !disk.backupValid ||
            (value!=disk.before.dc && value!=disk.target.dc)) violation=true;
        bool ok=authorized(value==disk.before.dc ? RestoreDc : Dc);
        if(ok || loseAck) dm.dc=value;
        return ok;
    }
    constexpr bool exitCfg() {
        ++exits; bool ok=tick() && !forceExitFail; if(ok || loseAck) {
            sec.cfg=false; q.standardFcc=dm.fcc; q.standardDc=dm.dc; // REINIT publishes lagged mirror
        }
        if(mode==5) sec.sec=3; // exit may auto seal; no re-unseal
        if(mode==7) sec.cal=true;
        return ok;
    }
    constexpr bool reseal() {
        ++seals; // cleanup API attempt, not a wire command
        Security fresh{};
        if(!observedSecurity(fresh) || !normal(fresh) || fresh.sec<1 || fresh.sec>3)
            return false;
        ++sealWires;
        bool ok=tick(); if(ok || loseAck) sec.sec=3;
        return ok;
    }
    constexpr bool verifyTarget(Dm target, bool afterExit) {
        ++verifies;
        if(!afterExit && sec.cfg && q.standardFcc==100) lagSeen=true;
        if(afterExit && mode==6) {
            // Sealed DM denied: use independently persisted FA target proof.
            const unsigned required=Unseal|Full|Enter|Config|TargetVerified;
            Identity fresh{};
            return tick() && identity(fresh) && fresh.device==0x0220 &&
                durable && disk.schema==1 && disk.state==State::Pending &&
                disk.mac[0]==fresh.mac[0] &&
                disk.backupValid && disk.fullAccessVerified && same(disk.target,target) &&
                (disk.intents & required)==required &&
                (disk.before.fcc==65 || (disk.intents & Fcc)) &&
                (disk.before.dc==65 || (disk.intents & Dc)) &&
                q.standardDc==65 && q.standardFcc==65 && sec.sec==3 && normal(sec);
        }
        Dm first{}, second{};
        if (!dmSnapshot(first) || !dmSnapshot(second) || !same(first,target) ||
            !same(second,target)) return false;
        return tick() && (!afterExit || (q.standardDc==65 && q.standardFcc==65 &&
            sec.sec==3 && normal(sec)));
    }
};
constexpr Result run(Fake& f) { Coordinator<Fake> c(f); return c.run(); }
constexpr bool bounded(const Fake& f) {
    return !f.violation && f.unseal<=1 && f.full<=1 && f.config<=2 && f.fcc<=2 && f.dc<=(f.cold ? 2 : 0) &&
        f.exits==(f.unseal ? 1 : 0) && f.seals==(f.unseal ? 1 : 0) && f.sealWires<=f.seals;
}
constexpr Result runCold(Fake& f) {
    Coordinator<Fake> c(f,Mode::ColdFactory); return c.run();
}
constexpr Fake factory() {
    Fake f; f.cold=true; f.dm.dc=f.dm.fcc=3000;
    f.q.standardDc=f.q.standardFcc=3000;
    f.q.soc=12; f.q.mv=3500; f.q.current=-130; f.q.average=130;
    return f;
}
constexpr bool coldFactory() {
    Fake base=factory();
    if(runCold(base)!=Result::Verified || !bounded(base) || base.dc!=1 ||
        base.fcc!=1 || base.config!=1 || !(base.disk.intents & Dc)) return false;
    for(int point=1;point<=base.step;++point) {
        for(int behavior=0;behavior<3;++behavior) {
            Fake f=factory(); f.failAt=point; f.loseAck=behavior==1; f.stickySave=behavior==2;
            if(runCold(f)==Result::Verified || !bounded(f)) return false;
            if(f.durable && f.disk.state!=State::Verified) {
                f.failAt=0; int oldKeys=f.unseal, oldWrites=f.dc+f.fcc+f.config;
                if(runCold(f)!=Result::Rejected || f.unseal!=oldKeys ||
                    f.dc+f.fcc+f.config!=oldWrites) return false;
            }
        }
    }
    // A previous boot's authenticated Verified is allowed only with fresh grant.
    Fake prior=factory(); prior.durable=true; prior.disk.state=State::Verified;
    if(runCold(prior)!=Result::Verified || !bounded(prior)) return false;
    for(int fault=0;fault<9;++fault) {
        Fake f=factory();
        if(fault==0) f.grantCold=false;
        if(fault==1) {f.durable=true; f.disk.state=State::Pending;}
        if(fault==2) {f.durable=true; f.disk.state=State::Failed;}
        if(fault==3) f.q.current=131;
        if(fault==4) f.q.mv=3499;
        if(fault==5) f.dm.dc=65;
        if(fault==6) f.dm.config=0x0021;
        if(fault==7) f.dm.config|=limitBit;
        if(fault==8) f.dm.fcc=100;
        const Result result=runCold(f);
        if(result==Result::Verified || !bounded(f) || f.dc || f.fcc || f.config)
            return false;
        if(fault<5 && (result!=Result::Rejected || f.unseal || f.full)) return false;
    }
    Fake denied=factory(); denied.mode=6;
    if(runCold(denied)!=Result::Verified || !bounded(denied)) return false;
    Fake upper=factory(); upper.q.mv=4350; upper.q.temperatureDeciK=3181;
    upper.q.current=130; upper.q.average=-130;
    return runCold(upper)==Result::Verified && bounded(upper);
}
constexpr bool success() {
    for(int dmFcc : {65,100}) {
        Fake f; f.dm.fcc=dmFcc;
        if(run(f)!=Result::Verified || !bounded(f) || f.verifies!=2 ||
            f.fcc!=(dmFcc==100 ? 1 : 0) || f.disk.state!=State::Verified ||
            f.dm.dc!=65 || f.dm.config!=0x0120) return false;
    }
    return true;
}
constexpr bool standardLag() {
    Fake f;
    return f.q.standardFcc==100 && run(f)==Result::Verified && f.lagSeen &&
        f.q.standardFcc==65 && f.verifies==2 && bounded(f);
}
constexpr bool durableProof() {
    Fake base; base.mode=6;
    if(run(base)!=Result::Verified || !(base.disk.intents & TargetVerified)) return false;
    const int operations=base.step;
    // Missing durable target proof cannot authenticate a sealed-denied result.
    base.disk.state=State::Pending;
    if(!base.verifyTarget(base.dm,true)) return false;
    base.disk.intents &= ~TargetVerified;
    if(base.verifyTarget(base.dm,true)) return false;
    for(int point=1;point<=operations;++point) {
        Fake f; f.mode=6; f.failAt=point;
        if(run(f)==Result::Verified || !bounded(f)) return false;
    }
    return true;
}
constexpr bool injections() {
    Fake base; if(run(base)!=Result::Verified) return false;
    for(int point=1;point<=base.step;++point) {
        for(int behavior=0;behavior<3;++behavior) {
            Fake f; f.failAt=point; f.loseAck=behavior==1; f.stickySave=behavior==2;
            if(run(f)==Result::Verified || !bounded(f)) return false;
            // Simulated reboot after any durable Pending/Failed/success record:
            // even an unresolved journal cannot cause another key or write.
            if(f.durable) {
                f.failAt=0; int keys=f.unseal, writes=f.config+f.fcc;
                if(run(f)!=Result::Rejected || f.unseal!=keys || f.config+f.fcc!=writes)
                    return false;
            }
        }
    }
    return true;
}
constexpr bool rejects() {
    for(int mode=1;mode<=2;++mode) {
        Fake f; f.mode=mode;
        if(run(f)!=Result::Rejected || f.step>5 || f.durable || !bounded(f)) return false;
    }
    for(int field=0;field<10;++field) {
        Fake f;
        if(field==0) f.q.soc=94;
        if(field==1) f.q.mv=4049;
        if(field==2) f.q.mv=4351;
        if(field==3) f.q.temperatureDeciK=2831;
        if(field==4) f.q.temperatureDeciK=3182;
        if(field==5) f.q.current=4;
        if(field==6) f.q.average=-4;
        if(field==7) f.q.operation|=0x0400;
        if(field==8) f.q.standardDc=100;
        if(field==9) f.q.standardFcc=65;
        if(run(f)!=Result::Rejected || f.durable || !bounded(f)) return false;
    }
    for(int field=0;field<5;++field) {
        Fake f;
        if(field==0) f.dm.config|=limitBit;
        if(field==1) f.dm.fcc=66;
        if(field==2) f.dm.dc=100;
        if(field==3) f.mode=3;
        if(field==4) f.dm.config=0x2000;
        if(run(f)!=Result::Failed || f.config || f.fcc || !bounded(f)) return false;
    }
    return true;
}
constexpr bool guardedSeal() {
    for(int scenario=0;scenario<4;++scenario) {
        Fake f;
        if(scenario==0) f.forceExitFail=true;
        if(scenario==1) f.unreadableAfterExit=true;
        if(scenario==2) { f.forceExitFail=true; f.unreadableAfterExit=true; }
        if(scenario==3) f.mode=7;
        if(run(f)!=Result::Failed || !bounded(f) || f.sealWires || f.seals!=1 ||
            f.disk.state!=State::Failed) return false;
        if(scenario==0 && !f.sec.cfg) return false; // never fabricate CFG exit
        int oldKeys=f.unseal+f.full;
        if(run(f)!=Result::Rejected || f.unseal+f.full!=oldKeys) return false;
    }
    // Unknown SEC and CAL also prohibit a wire Seal independently of CFG.
    for(int scenario=0;scenario<2;++scenario) {
        Fake f; f.sec.cfg=false;
        if(scenario==0) f.sec.sec=0;
        if(scenario==1) f.sec.cal=true;
        if(f.reseal() || f.seals!=1 || f.sealWires) return false;
    }
    return true;
}
constexpr bool automaticSeal() {
    Fake f; f.mode=4;
    if(run(f)!=Result::Failed || f.config!=1 || f.fcc || !bounded(f)) return false;
    Fake e; e.mode=5;
    return run(e)==Result::Verified && bounded(e);
}
// Power loss at every persisted prefix: all records categorically reject replay.
constexpr bool powerLoss() {
    for(unsigned flags=0;flags<1024;++flags) {
        Fake f; f.durable=true; f.disk.intents=flags;
        if(run(f)!=Result::Rejected || f.unseal || f.full || f.config || f.fcc) return false;
    }
    return true;
}
static_assert(coldFactory(), "authorized boot-only factory profile and bounded failure matrix");
static_assert(success(), "success/skip field path");
static_assert(standardLag(), "standard FCC lag in CFG until exit REINIT is allowed");
static_assert(durableProof(), "sealed-denied own durable target proof and failed persistence");
static_assert(injections(), "every operation failure, lost ACK, sticky NVS failure");
static_assert(rejects(), "scope and quiet gates");
static_assert(guardedSeal(), "failed exit or unreadable/CAL/unknown status prohibits wire Seal");
static_assert(automaticSeal(), "never reopen automatically sealed gauge");
static_assert(powerLoss(), "durable record prevents cold replay");
'''


class RepairCoordinatorTests(unittest.TestCase):
    def test_actual_coordinator_fault_matrix(self):
        compiler = shutil.which('g++') or shutil.which('clang++')
        if not compiler:
            found = sorted(Path('C:/Espressif/tools/riscv32-esp-elf').glob(
                '*/riscv32-esp-elf/bin/riscv32-esp-elf-g++.exe'))
            compiler = str(found[-1]) if found else None
        self.assertIsNotNone(compiler, 'C++ compiler required; do not silently skip')
        with tempfile.TemporaryDirectory(prefix='fcc-limit-model-') as temp:
            source = Path(temp) / 'test.cpp'
            source.write_text('#include <initializer_list>\n' + HARNESS, encoding='utf8')
            result = subprocess.run([compiler, '-std=c++17', '-Wall', '-Wextra',
                '-Werror', '-fsyntax-only', '-I', str(ROOT), str(source)],
                capture_output=True, text=True, timeout=60)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)


if __name__ == '__main__':
    unittest.main()
