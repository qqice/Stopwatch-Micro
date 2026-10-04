/* SPDX-License-Identifier: MIT
 * BQ27220 nominal-capacity safety model, not cell characterization.
 * Register/command references: TI SLUUBD4A sections 2, 3.1 and 6.1.
 */
#pragma once
#include <cstdint>
#include <cstddef>
#include <cstring>

namespace mosaico_gauge {
constexpr uint16_t DeviceType = 0x0220;
constexpr uint16_t NominalMah = 65;
constexpr uint16_t FactoryMah = 3000;
constexpr uint16_t MaxReasonableFcc = 78;
enum class Field : uint16_t { InitialFcc = 0x929D, Design = 0x929F };
enum class State : uint8_t { Pending = 1, Committed, Restored, RolledBack, RollbackFailed, ExitFailed,
                             VerifiedSealedPrior3 }; // CRC-bound observed FINAL SEC3, expected prior SEC3.

// Explicit fixed layout: physical mAh units and board identity, no padding in
// the CRC region, no key material. The entire blob is committed atomically by NVS.
struct Journal {
    uint32_t magic;
    uint16_t version;
    uint16_t deviceType;
    uint8_t unitMah;
    uint8_t state;
    uint8_t changedFcc;
    uint8_t action; // 1 = nominal apply; 2 = explicit restore.
    uint8_t mac[6];
    uint16_t originalDesign;
    uint16_t originalFcc;
    uint16_t targetDesign;
    uint16_t targetFcc;
    uint16_t operationBefore;
    uint16_t lastDesign; // 0 while pending/unknown, otherwise confirmed readback.
    uint16_t lastFcc;
    uint32_t crc;
};
static_assert(offsetof(Journal, crc) == 32 && sizeof(Journal) == 36, "Gauge journal layout changed");

enum class AccessState : uint8_t { Pending = 1, Opened, Restored, Failed };
struct AccessJournal {
    uint32_t magic;
    uint16_t version;
    uint16_t deviceType;
    uint8_t unitMah;
    uint8_t state;
    uint8_t priorSecurity;
    uint8_t lastSecurity;
    uint8_t unsealAttempted;
    uint8_t unsealVerified;
    uint8_t fullAttempted;
    uint8_t fullVerified;
    uint8_t mac[6];
    uint16_t baseDesign;
    uint16_t baseFcc;
    uint16_t operationBefore;
    uint32_t crc;
};
static_assert(offsetof(AccessJournal, crc) == 28 && sizeof(AccessJournal) == 32, "Access journal layout changed");

inline uint32_t crc32(const void* data, size_t size)
{
    const auto* bytes = static_cast<const uint8_t*>(data);
    uint32_t crc = 0xFFFFFFFFU;
    for (size_t i = 0; i < size; ++i) {
        crc ^= bytes[i];
        for (int bit = 0; bit < 8; ++bit) crc = (crc >> 1) ^ ((crc & 1) ? 0xEDB88320U : 0U);
    }
    return ~crc;
}

inline void seal(Journal& journal) { journal.crc = crc32(&journal, offsetof(Journal, crc)); }
inline void seal(AccessJournal& journal) { journal.crc = crc32(&journal, offsetof(AccessJournal, crc)); }
inline bool reasonableFcc(uint16_t value) { return value > 0 && value <= MaxReasonableFcc; }
inline bool configExitAccepted(uint16_t operation)
{
    const unsigned security = (operation >> 1) & 3;
    // SEC3 after REINIT is a compatibility observation on this unit, not a
    // universal TI guarantee. Never accept SEALED while CFGUPDATE remains set.
    return (security == 1 || security == 3) && !(operation & 0x0401) && (operation & 0x0020);
}
inline bool capacityPolicy(uint16_t design, uint16_t fcc)
{
    return (design == NominalMah || design == FactoryMah) &&
           (reasonableFcc(fcc) || (design == FactoryMah && fcc == FactoryMah));
}

inline bool quietFull(uint16_t op, uint16_t soc, uint16_t mv, uint16_t temperature,
                      int16_t current, int16_t average)
{
    // Integer 0.1 K bounds strictly inside 10..45 C (283.15..318.15 K).
    return ((op >> 1) & 3) == 1 && !(op & 0x0401) && (op & 0x0020) &&
           soc >= 95 && soc <= 100 && mv >= 4050 && mv <= 4350 &&
           temperature >= 2832 && temperature <= 3181 &&
           current >= -3 && current <= 3 && average >= -3 && average <= 3;
}

inline bool quietAccess(uint16_t op, uint16_t soc, uint16_t mv, uint16_t temperature,
                        int16_t current, int16_t average)
{
    const unsigned security = (op >> 1) & 3;
    // Access OPEN may start in sealed/unsealed/full, but never undefined SEC0,
    // CFGUPDATE or CAL. Physical guard boundaries are unchanged.
    return security >= 1 && security <= 3 &&
           quietFull(static_cast<uint16_t>((op & ~0x0006U) | 0x0002U), soc, mv, temperature, current, average);
}

inline bool validAccessJournal(const AccessJournal& journal, const uint8_t mac[6])
{
    return journal.magic == 0x47414331U && journal.version == 1 && journal.deviceType == DeviceType &&
           journal.unitMah == 1 && journal.state >= static_cast<uint8_t>(AccessState::Pending) &&
           journal.state <= static_cast<uint8_t>(AccessState::Failed) &&
           journal.priorSecurity >= 1 && journal.priorSecurity <= 3 && journal.lastSecurity <= 3 &&
           journal.unsealAttempted <= 1 && journal.unsealVerified <= journal.unsealAttempted &&
           journal.fullAttempted <= 1 && journal.fullVerified <= journal.fullAttempted &&
           (!journal.fullVerified || journal.unsealVerified) && std::memcmp(journal.mac, mac, 6) == 0 &&
           (journal.baseDesign == NominalMah || journal.baseDesign == FactoryMah) &&
           (reasonableFcc(journal.baseFcc) || journal.baseFcc == FactoryMah) &&
           ((journal.operationBefore >> 1) & 3) == journal.priorSecurity && !(journal.operationBefore & 0x0401) &&
           journal.crc == crc32(&journal, offsetof(AccessJournal, crc));
}

inline bool failedDefaultAttempt(const AccessJournal& journal)
{
    // Persisted BEFORE sending words: a crash or missing confirmation never
    // authorizes replaying unknown keys on the next OPEN request.
    return (journal.unsealAttempted && !journal.unsealVerified) || (journal.fullAttempted && !journal.fullVerified);
}

inline bool recoveryState(uint8_t state)
{
    return state == static_cast<uint8_t>(State::Pending) || state == static_cast<uint8_t>(State::ExitFailed) ||
           state == static_cast<uint8_t>(State::RollbackFailed);
}

inline bool quietFullRestore(uint16_t op, uint16_t soc, uint16_t mv, uint16_t temperature,
                             int16_t current, int16_t average, bool ownedUnresolvedJournal)
{
    // Only the already-validated explicit restore path may admit CFGUPDATE.
    // No relaxation of security, CAL, INITCOMP or physical measurement bounds.
    if ((op & 0x0400) && !ownedUnresolvedJournal) return false;
    return quietFull(static_cast<uint16_t>(op & ~0x0400U), soc, mv, temperature, current, average);
}

inline uint8_t macChecksum(const uint8_t* bytes, size_t size)
{
    uint8_t sum = 0;
    for (size_t i = 0; i < size; ++i) sum = static_cast<uint8_t>(sum + bytes[i]);
    return static_cast<uint8_t>(255 - sum);
}

inline bool authenticatedMac(const uint8_t block[36], uint16_t selector, bool dm)
{
    const unsigned length = block[35];
    return length >= 6 && length <= 36 && (!dm || length == 36) &&
           block[0] == static_cast<uint8_t>(selector) && block[1] == static_cast<uint8_t>(selector >> 8) &&
           macChecksum(block, length - 2) == block[34];
}

inline bool authenticateDmResponse(uint8_t block[36], uint16_t requested)
{
    // Board-reference compatibility only: the locally verified BQ27220 MAC
    // selector is unreadable. Accept its narrow FF FF response, never any
    // arbitrary mismatched echo, and still bind the checksum to the actual
    // exclusively-owned ACKed outgoing selector + every returned payload byte.
    // TI SLUUBD4A 2.30 / TI E2E 615263 specify inclusion of selector in checksum;
    // FF FF itself is NOT asserted to be universal TI device behavior.
    if (block[0] == 0xFF && block[1] == 0xFF) {
        block[0] = static_cast<uint8_t>(requested);
        block[1] = static_cast<uint8_t>(requested >> 8);
    }
    return authenticatedMac(block, requested, true);
}

inline bool validJournal(const Journal& journal, const uint8_t mac[6])
{
    return journal.magic == 0x47433635U && journal.version == 1 && journal.deviceType == DeviceType &&
           journal.unitMah == 1 && journal.state >= static_cast<uint8_t>(State::Pending) &&
           journal.state <= static_cast<uint8_t>(State::VerifiedSealedPrior3) &&
           (journal.action == 1 || journal.action == 2) && journal.changedFcc <= 1 &&
           std::memcmp(journal.mac, mac, 6) == 0 && journal.targetDesign == NominalMah &&
           capacityPolicy(journal.originalDesign, journal.originalFcc) &&
           (journal.changedFcc ? (journal.originalDesign == FactoryMah && journal.originalFcc == FactoryMah &&
                                  journal.targetFcc == NominalMah) : journal.targetFcc == journal.originalFcc) &&
           ((journal.operationBefore >> 1) & 3) == 1 && !(journal.operationBefore & 0x0401) &&
           journal.crc == crc32(&journal, offsetof(Journal, crc));
}

inline bool allowedValue(const Journal& journal, Field field, uint16_t value)
{
    if (field == Field::Design) return value == NominalMah || value == journal.originalDesign;
    if (field == Field::InitialFcc && journal.changedFcc)
        return value == journal.targetFcc || value == journal.originalFcc;
    return false;
}

inline bool restorePairAllowed(const Journal& journal, uint16_t design, uint16_t fcc)
{
    // A interrupted transaction may expose any OLD/TARGET mixed DM pair.
    // A field we never changed remains learned/read-only within its safe range.
    return (design == journal.originalDesign || design == journal.targetDesign) &&
           (journal.changedFcc ? (fcc == journal.originalFcc || fcc == journal.targetFcc) : reasonableFcc(fcc));
}

inline bool selftest()
{
    if (crc32("123456789", 9) != 0xCBF43926U || !configExitAccepted(0xA6) || !configExitAccepted(0xA2) ||
        configExitAccepted(0x4A6) || configExitAccepted(0xA7) || configExitAccepted(0x86) ||
        configExitAccepted(0xA4) || !quietFull(0x22, 95, 4050, 2832, -3, 3) ||
        quietFull(0x26, 99, 4200, 3000, 0, 0) || quietFull(0x422, 99, 4200, 3000, 0, 0) ||
        quietFull(0x22, 94, 4200, 3000, 0, 0) || quietFull(0x22, 99, 4200, 3182, 0, 0) ||
        quietFull(0x22, 99, 4200, 3000, 4, 0) ||
        !quietFullRestore(0x422, 99, 4200, 3000, 0, 0, true) ||
        quietFullRestore(0x422, 99, 4200, 3000, 0, 0, false) ||
        quietFullRestore(0x426, 99, 4200, 3000, 0, 0, true) ||
        quietFullRestore(0x423, 99, 4200, 3000, 0, 0, true) ||
        !quietAccess(0xA6, 100, 4205, 3000, 0, 0) || quietAccess(0xA0, 100, 4205, 3000, 0, 0) ||
        quietAccess(0x4A6, 100, 4205, 3000, 0, 0) || quietAccess(0xA7, 100, 4205, 3000, 0, 0) ||
        !capacityPolicy(3000, 3000) ||
        !capacityPolicy(3000, 78) || capacityPolicy(65, 3000) || capacityPolicy(130, 130)) return false;
    Journal journal{};
    journal.magic = 0x47433635U; journal.version = 1; journal.deviceType = DeviceType;
    journal.unitMah = 1; journal.state = static_cast<uint8_t>(State::Pending); journal.action = 1;
    journal.changedFcc = 1; journal.originalDesign = FactoryMah; journal.originalFcc = FactoryMah;
    journal.targetDesign = NominalMah; journal.targetFcc = NominalMah; journal.operationBefore = 0x22;
    const uint8_t mac[6] = {1, 2, 3, 4, 5, 6};
    AccessJournal access{};
    access.magic = 0x47414331U; access.version = 1; access.deviceType = DeviceType; access.unitMah = 1;
    access.state = static_cast<uint8_t>(AccessState::Pending); access.priorSecurity = 3;
    access.operationBefore = 0xA6; access.baseDesign = FactoryMah; access.baseFcc = FactoryMah;
    std::memcpy(access.mac, mac, 6); seal(access);
    if (!validAccessJournal(access, mac) || failedDefaultAttempt(access)) return false;
    access.unsealAttempted = 1; seal(access);
    if (!validAccessJournal(access, mac) || !failedDefaultAttempt(access)) return false;
    access.unsealVerified = 1; access.fullAttempted = 1; seal(access);
    if (!failedDefaultAttempt(access)) return false;
    access.fullVerified = 1; seal(access);
    if (!validAccessJournal(access, mac) || failedDefaultAttempt(access)) return false;
    access.priorSecurity ^= 1;
    if (validAccessJournal(access, mac)) return false;
    std::memcpy(journal.mac, mac, 6); seal(journal);
    if (!validJournal(journal, mac) || !restorePairAllowed(journal, 65, 3000) ||
        !restorePairAllowed(journal, 3000, 65) || restorePairAllowed(journal, 130, 65) ||
        !recoveryState(static_cast<uint8_t>(State::ExitFailed)) || recoveryState(static_cast<uint8_t>(State::Committed)) ||
        !allowedValue(journal, Field::Design, 65) ||
        !allowedValue(journal, Field::InitialFcc, 3000) || allowedValue(journal, Field::Design, 130) ||
        allowedValue(journal, static_cast<Field>(0x9184), 65)) return false;
    journal.originalFcc ^= 1;
    if (validJournal(journal, mac)) return false;
    uint8_t block[36]{};
    block[0] = 0x9F; block[1] = 0x92; block[2] = 0; block[3] = 65; block[35] = 36;
    block[34] = macChecksum(block, 34);
    if (!authenticatedMac(block, 0x929F, true) || authenticatedMac(block, 0x929D, true)) return false;
    block[0] = 0xFF; block[1] = 0xFF;
    if (!authenticateDmResponse(block, 0x929F)) return false;
    block[0] = 0; block[1] = 0;
    if (authenticateDmResponse(block, 0x929F)) return false;
    block[0] = 0x9F; block[1] = 0x92;
    block[2] ^= 1;
    return !authenticateDmResponse(block, 0x929F);
}
} // namespace mosaico_gauge
