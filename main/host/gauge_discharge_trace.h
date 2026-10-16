#pragma once
namespace GaugeDischargeTrace {
using EmitLine=void(*)(const char* line,void* context);
// All requests are RAM-only; service owns sampling/NVS. False means busy/not ready/refused.
void service();
bool armConfirm(); // Refuses old data/corrupt storage; no implicit overwrite.
bool stop();
bool clearConfirm(); // Explicitly queues empty, disarmed record; no erase.
void status(EmitLine emit,void* context);
void exportRows(EmitLine emit,void* context); // Header + <=64 bounded JSON lines; does not clear.
}
