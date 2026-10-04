// Pure compile-time checks: status cache/progress loads must be lock-free on S31.
#include <atomic>
#include <cstdint>
static_assert(std::atomic<bool>::is_always_lock_free, "pending/active must not hide a lock");
static_assert(std::atomic<uint32_t>::is_always_lock_free, "progress must not hide a lock");
static_assert(std::atomic<const void*>::is_always_lock_free, "partition cache must not hide a lock");