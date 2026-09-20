#pragma once

#include <stddef.h>
#include <stdint.h>

#define CONTROLLER_PACKET_SIZE 20
#define CONTROLLER_PROTOCOL_VERSION 1
#define CONTROLLER_MESSAGE_STATE 1

typedef struct {
    uint16_t sequence;
    uint32_t uptime_ms;
    uint16_t buttons;
    int16_t accel_x_mg;
    int16_t accel_y_mg;
    int16_t accel_z_mg;
    uint16_t punch_counter;
} controller_state_t;

void controller_encode_state(uint8_t out[CONTROLLER_PACKET_SIZE], const controller_state_t *state);
