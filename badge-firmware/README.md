# Hacker Badge Minecraft controller firmware

This replaces the 2026 Hacker Badge event firmware with an ESP-IDF BLE controller. Back up anything you want to keep before flashing; recovery uses **Start held while plugging in USB**.

## Controls

- D-pad: camera yaw/pitch
- Hold A: move forward
- Hold B: jump
- Aux1 side switch: sprint while moving
- HOME: arm/disarm gameplay input
- Long-press Start: hold still for one second, then perform one representative punch within five seconds
- Calibrated punch: one Minecraft attack

The screen and LEDs share the same status language: blue means advertising, cyan means connected, purple means disarmed, amber means calibration, green means a punch, and red means a hardware/calibration error. Punch calibration is stored in NVS.

## Build and flash

Install the badge-pinned ESP-IDF 5.5.3, then:

```sh
. ~/.espressif/tools/activate_idf_v5.5.3.sh
cd badge-firmware
idf.py set-target esp32c3
idf.py build
idf.py -p /dev/tty.usbmodemXXXX flash monitor
```

The firmware explicitly disables NimBLE host flow control due to the ESP-IDF 5.5.3 issue. Do not hold Start during a normal boot; it is GPIO9 and selects download mode at reset.

## Protocol

The badge advertises `HTN-Badge-XXXX` and service `7b1e1000-6f7a-4d19-9c4b-5a2c8f1e2026`. Characteristic `7b1e1001-6f7a-4d19-9c4b-5a2c8f1e2026` is read/notify and emits the fixed 20-byte little-endian packet documented in `docs/contracts.md`.

The pure-C protocol and classifier tests do not need ESP-IDF:

```sh
cc -std=c11 -Wall -Wextra -Werror -Imain tests/test_protocol_and_punch.c \
  main/controller_protocol.c main/punch_detector.c -lm -o /tmp/hmc-badge-tests
/tmp/hmc-badge-tests
```
