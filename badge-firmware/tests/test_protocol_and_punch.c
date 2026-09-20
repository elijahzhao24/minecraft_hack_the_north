#include <assert.h>
#include <math.h>
#include <stdint.h>

#include "../main/controller_protocol.h"
#include "../main/punch_detector.h"

int main(void) {
    controller_state_t state = {
        .sequence = 7, .uptime_ms = 1234, .buttons = 0x41,
        .accel_x_mg = 12, .accel_y_mg = -20, .accel_z_mg = 1001, .punch_counter = 3,
    };
    uint8_t encoded[CONTROLLER_PACKET_SIZE];
    controller_encode_state(encoded, &state);
    const uint8_t expected[CONTROLLER_PACKET_SIZE] = {
        'H','B',1,1,7,0,0xd2,0x04,0,0,0x41,0,12,0,0xec,0xff,0xe9,0x03,3,0
    };
    for (int i = 0; i < CONTROLLER_PACKET_SIZE; ++i) assert(encoded[i] == expected[i]);

    punch_detector_t detector;
    punch_detector_init(&detector);
    for (int i = 0; i < 100; ++i) punch_detector_observe_gravity(&detector, 0, 0, 1000);
    const float direction[3] = {1, 0, 0};
    punch_detector_set_calibration(&detector, direction, 800);
    assert(!punch_detector_sample(&detector, 300, 0, 1000, 500));
    assert(punch_detector_sample(&detector, 1200, 0, 1000, 1000));
    assert(!punch_detector_sample(&detector, 1200, 0, 1000, 1100));
    assert(!punch_detector_sample(&detector, -1400, 0, 1000, 2000));
    return 0;
}
