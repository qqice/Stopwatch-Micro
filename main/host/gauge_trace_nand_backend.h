#pragma once
#include "gauge_discharge_trace_model.h"

namespace GaugeDischargeTrace::NandBackend {
using EmitLine=void(*)(const char*,void*);
// selected=false ONLY when the persistent policy is absent. All failures with
// a selected/pending/unknown policy are closed: never hide them with NVS fallback.
bool load(Blob& canonical,bool& selected);
bool save(const Blob& canonical,bool& selected);
// Explicitly confirmed, one-time asynchronous destructive format + migration.
// Caller additionally owns trace disarmed/nonempty/no-pending admission.
bool beginFormat(const Blob& snapshot);
bool busy();
bool retryable(); // Last load/save had a clean temporary resource refusal (busy/gate/OOM).
void status(EmitLine emit,void* context);
}
