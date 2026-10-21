// Closed, read-only BQ27220 diagnostic model. No SDK or device dependencies.
#pragma once
#include <cstdint>
#include <cstddef>
#include <type_traits>
#include "mosaico_gauge_unit_tag.h"
namespace mosaico_gauge_status {
enum Valid : uint32_t {
    Soc=1U<<0, Remaining=1U<<1, Full=1U<<2, Design=1U<<3,
    Voltage=1U<<4, Current=1U<<5, Average=1U<<6, RawCoulomb=1U<<7,
    Cycles=1U<<8, Operation=1U<<9, Battery=1U<<10, Gauging=1U<<11,
    ModeValid=1U<<12, AllRaw=(1U<<12)-1,
    TxAck=1U<<16, EchoRead=1U<<17, DataRead=1U<<18
};
enum Phase : uint8_t { None=0, BootBefore=1, BootAfter=2, Explicit=3, PostExit=4 };
enum Mode : uint8_t { Unknown=0, Charging=1, Relax=2, Discharge=3 };
// Fixed-width, trivially copyable RAM value. validMask is transport validity,
// not cell-health, percentage accuracy or cross-register atomicity evidence.
struct Snapshot {
    uint64_t sampledUs;
    uint32_t validMask;
    uint16_t rawSoc, remainingMah, fullMah, designMah, voltageMv;
    int16_t currentMa, averageCurrentMa;
    uint16_t rawCoulombMah, cycleCount, operationStatus, batteryStatus, gaugingStatus;
    uint16_t rawEcho16;
    uint8_t phase, outcome, mode, reserved;
    mosaico_gauge_unit_tag::Tag units;
};
static_assert(std::is_trivial<Snapshot>::value && std::is_standard_layout<Snapshot>::value);
static_assert(sizeof(Snapshot)==80); // Native payload unchanged; appended RAM-only context.
constexpr uint8_t decodeMode(uint16_t gauging, uint16_t battery) {
    const bool macDsg=(gauging & 0x0040)!=0; // low-byte MAC DSG: discharge OR relax
    const bool battDsg=(battery & 0x0001)!=0;
    return macDsg ? (battDsg ? Discharge : Relax) : (battDsg ? Unknown : Charging);
}
constexpr const char* modeName(uint8_t mode) {
    return mode==Charging ? "charging" : mode==Relax ? "relax" : mode==Discharge ? "discharge" : "unknown";
}
constexpr bool decodeResponse(const uint8_t* bytes, size_t count, uint16_t& value) {
    value=0;
    if(!bytes || count!=4 || bytes[0]!=0x56 || bytes[1]!=0x00)return false;
    value=uint16_t(bytes[2]) | (uint16_t(bytes[3])<<8);
    return true;
}
// Transport returns success only for an exact-length complete read. There is
// no public command argument, checksum write, data-memory or security path.
template<class Transport> constexpr bool readGaugingStatus(Transport& bus, uint16_t& value,
                                                         uint16_t& echo, uint32_t& flags) {
    value=0; echo=0; flags=0;
    const uint8_t packet[]={0x00,0x56,0x00};
    if(!bus.transmit(packet,sizeof(packet)))return false;
    flags|=TxAck;
    // Same conservative completion delay and word shape as board-proven MAC
    // reads; exceeds TI minimum 66 us. Not a guessed alternate command.
    bus.delayMs(20);
    uint8_t bytes[2]{};
    if(bus.read(0x3e,bytes,sizeof(bytes))) {
        flags|=EchoRead; echo=uint16_t(bytes[0]) | (uint16_t(bytes[1])<<8);
    }
    // Independently retain returned data even if echo IO fails/mismatches.
    bytes[0]=bytes[1]=0;
    if(bus.read(0x40,bytes,sizeof(bytes))) {
        flags|=DataRead; value=uint16_t(bytes[0]) | (uint16_t(bytes[1])<<8);
    }
    return flags==(TxAck|EchoRead|DataRead) && echo==0x0056;
}
} // namespace mosaico_gauge_status
