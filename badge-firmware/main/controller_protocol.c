#include "controller_protocol.h"

static void put16(uint8_t *p, uint16_t value) {
    p[0] = (uint8_t)value;
    p[1] = (uint8_t)(value >> 8);
}

static void put32(uint8_t *p, uint32_t value) {
    p[0] = (uint8_t)value;
    p[1] = (uint8_t)(value >> 8);
    p[2] = (uint8_t)(value >> 16);
    p[3] = (uint8_t)(value >> 24);
}

void controller_encode_state(uint8_t out[CONTROLLER_PACKET_SIZE], const controller_state_t *state) {
    out[0] = 'H';
    out[1] = 'B';
    out[2] = CONTROLLER_PROTOCOL_VERSION;
    out[3] = CONTROLLER_MESSAGE_STATE;
    put16(out + 4, state->sequence);
    put32(out + 6, state->uptime_ms);
    put16(out + 10, state->buttons);
    put16(out + 12, (uint16_t)state->accel_x_mg);
    put16(out + 14, (uint16_t)state->accel_y_mg);
    put16(out + 16, (uint16_t)state->accel_z_mg);
    put16(out + 18, state->punch_counter);
}
