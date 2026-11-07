#pragma once
void delta_test_delay(unsigned ticks);
static inline void vTaskDelay(unsigned ticks){delta_test_delay(ticks);}
