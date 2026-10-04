#include "../ota_readiness.h"
using MosaicoOta::appReady;
static_assert(!appReady(false, false, false, 0), "allocation failure must reject");
static_assert(!appReady(true, false, true, 1), "key allocation failure must reject");
static_assert(!appReady(true, true, false, 100), "serial begin/allocation failure must reject even with UI ready");
static_assert(!appReady(false, true, true, 100), "partial UI must reject");
static_assert(!appReady(true, true, true, 0), "no completed onRunning loop must reject");
static_assert(appReady(true, true, true, 1), "healthy initialized running app may pass");
