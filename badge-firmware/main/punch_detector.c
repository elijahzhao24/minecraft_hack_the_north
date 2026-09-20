#include "punch_detector.h"

#include <math.h>
#include <string.h>

void punch_detector_init(punch_detector_t *detector) {
    memset(detector, 0, sizeof(*detector));
}

void punch_detector_set_calibration(punch_detector_t *detector, const float direction[3], float threshold_mg) {
    float length = sqrtf(direction[0] * direction[0] + direction[1] * direction[1] + direction[2] * direction[2]);
    if (length < 1.0f || threshold_mg < 300.0f) return;
    for (int i = 0; i < 3; ++i) detector->direction[i] = direction[i] / length;
    detector->threshold_mg = threshold_mg;
    detector->calibrated = true;
}

void punch_detector_observe_gravity(punch_detector_t *detector, int16_t x, int16_t y, int16_t z) {
    const float sample[3] = {(float)x, (float)y, (float)z};
    if (!detector->gravity_ready) {
        memcpy(detector->gravity, sample, sizeof(sample));
        detector->gravity_ready = true;
        return;
    }
    for (int i = 0; i < 3; ++i) detector->gravity[i] = detector->gravity[i] * 0.98f + sample[i] * 0.02f;
}

float punch_detector_dynamic(const punch_detector_t *detector, int16_t x, int16_t y, int16_t z, float out[3]) {
    out[0] = (float)x - detector->gravity[0];
    out[1] = (float)y - detector->gravity[1];
    out[2] = (float)z - detector->gravity[2];
    return sqrtf(out[0] * out[0] + out[1] * out[1] + out[2] * out[2]);
}

bool punch_detector_sample(punch_detector_t *detector, int16_t x, int16_t y, int16_t z, uint32_t now_ms) {
    if (!detector->gravity_ready) {
        punch_detector_observe_gravity(detector, x, y, z);
        return false;
    }
    float dynamic[3];
    float magnitude = punch_detector_dynamic(detector, x, y, z, dynamic);
    if (magnitude < 450.0f) punch_detector_observe_gravity(detector, x, y, z);
    if (!detector->calibrated || now_ms - detector->last_punch_ms < 450) return false;
    float forward = dynamic[0] * detector->direction[0]
                  + dynamic[1] * detector->direction[1]
                  + dynamic[2] * detector->direction[2];
    if (forward < detector->threshold_mg || magnitude < detector->threshold_mg) return false;
    detector->last_punch_ms = now_ms;
    return true;
}
