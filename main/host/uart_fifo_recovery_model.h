/* SPDX-License-Identifier: MIT */
#pragma once
#include <cstddef>
#include <cstdint>
namespace UartFifoRecovery {
constexpr uint16_t WakeThreshold=16;
constexpr int FullThreshold=15; // SDK LL: RXFULL asserts above this value.
constexpr std::size_t QueueCapacity=32;
// Conservative nominal burst bound: FIFO batches plus one trailing timeout,
// one wake event per burst. Errors/floods still fail closed; not a timing proof.
constexpr std::size_t burstEvents(std::size_t bytes) {
    return (bytes+WakeThreshold-1)/WakeThreshold+2;
}
constexpr std::size_t RecoveryFrameEvents=3*burstEvents(33)+burstEvents(25);
static_assert(RecoveryFrameEvents<QueueCapacity,"unchanged 3x33 preamble and ping fit even without a 60 ms consumer");
}
