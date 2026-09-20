#pragma once

#include <stdbool.h>
#include <stdint.h>

typedef struct {
    float direction[3];
    float threshold_mg;
    float gravity[3];
    uint32_t last_punch_ms;
    bool calibrated;
    bool gravity_ready;
} punch_detector_t;

void punch_detector_init(punch_detector_t *detector);
void punch_detector_set_calibration(punch_detector_t *detector, const float direction[3], float threshold_mg);
void punch_detector_observe_gravity(punch_detector_t *detector, int16_t x, int16_t y, int16_t z);
float punch_detector_dynamic(const punch_detector_t *detector, int16_t x, int16_t y, int16_t z, float out[3]);
bool punch_detector_sample(punch_detector_t *detector, int16_t x, int16_t y, int16_t z, uint32_t now_ms);
