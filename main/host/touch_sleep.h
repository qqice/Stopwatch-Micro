/* SPDX-License-Identifier: MIT */
#pragma once
#include "touch_sleep_model.h"
#include <cstddef>
namespace TouchSleep {
bool request120(bool locked,bool safeView,bool asyncBusy);
void off();
void wakeRequested(); // HAL wake intent only; no I2C/callback work.
bool blocksTouch();
bool active();
void service(bool locked,bool conflict); // Existing app_main only; one bounded action per loop.
class OtaAdmission {
 bool allowed_=false;
public:
 OtaAdmission();~OtaAdmission();
 OtaAdmission(const OtaAdmission&)=delete;OtaAdmission& operator=(const OtaAdmission&)=delete;
 explicit operator bool() const {return allowed_;}
};
struct Snapshot {Model model;bool supported=false,reserved=false;uint32_t otaInFlight=0;};
Snapshot snapshot();
void status(char* out,std::size_t capacity);
}
