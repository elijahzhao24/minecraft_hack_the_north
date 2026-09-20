#include <math.h>
#include <stdbool.h>
#include <stdio.h>
#include <string.h>

#include "controller_protocol.h"
#include "punch_detector.h"

#include "driver/gpio.h"
#include "driver/i2c.h"
#include "driver/spi_master.h"
#include "esp_check.h"
#include "esp_lcd_panel_io.h"
#include "esp_lcd_panel_ops.h"
#include "esp_lcd_panel_vendor.h"
#include "esp_heap_caps.h"
#include "esp_log.h"
#include "esp_mac.h"
#include "esp_rom_sys.h"
#include "esp_timer.h"
#include "host/ble_hs.h"
#include "host/util/util.h"
#include "led_strip.h"
#include "nimble/nimble_port.h"
#include "nimble/nimble_port_freertos.h"
#include "nvs.h"
#include "nvs_flash.h"
#include "os/os_mbuf.h"
#include "services/gap/ble_svc_gap.h"
#include "services/gatt/ble_svc_gatt.h"

#define LCD_MOSI 10
#define LCD_CLK 1
#define LCD_CS 2
#define LCD_DC 0
#define LCD_RST 4
#define I2C_SDA 5
#define I2C_SCL 6
#define HC165_DATA 7
#define HC165_LOAD 20
#define HC165_CLK 21
#define START_BUTTON 9
#define LED_DATA 3
#define ACCEL_ADDR 0x19

#define BUTTON_A (1u << 0)
#define BUTTON_B (1u << 1)
#define BUTTON_HOME (1u << 2)
#define BUTTON_DOWN (1u << 3)
#define BUTTON_LEFT (1u << 4)
#define BUTTON_RIGHT (1u << 5)
#define BUTTON_UP (1u << 6)
#define BUTTON_AUX1 (1u << 7)
#define BUTTON_START (1u << 8)

static const char *TAG = "badge_controller";
static const ble_uuid128_t SERVICE_UUID = BLE_UUID128_INIT(
    0x26, 0x20, 0x1e, 0x8f, 0x2c, 0x5a, 0x4b, 0x9c,
    0x19, 0x4d, 0x7a, 0x6f, 0x00, 0x10, 0x1e, 0x7b);
static const ble_uuid128_t STATE_UUID = BLE_UUID128_INIT(
    0x26, 0x20, 0x1e, 0x8f, 0x2c, 0x5a, 0x4b, 0x9c,
    0x19, 0x4d, 0x7a, 0x6f, 0x01, 0x10, 0x1e, 0x7b);

typedef enum { UI_ADVERTISING, UI_CONNECTED, UI_DISARMED, UI_CALIBRATE_STILL,
               UI_CALIBRATE_PUNCH, UI_PUNCH, UI_ERROR } ui_state_t;
typedef enum { CAL_IDLE, CAL_STILL, CAL_PUNCH } calibration_phase_t;

typedef struct {
    uint32_t magic;
    uint16_t version;
    uint16_t reserved;
    float direction[3];
    float threshold_mg;
} saved_calibration_t;

static controller_state_t g_state;
static portMUX_TYPE g_state_lock = portMUX_INITIALIZER_UNLOCKED;
static uint16_t g_state_handle;
static bool g_ble_connected;
static uint8_t g_own_addr_type;
static bool g_armed = true;
static punch_detector_t g_punch;
static calibration_phase_t g_cal_phase;
static uint32_t g_cal_started_ms;
static int g_cal_samples;
static ui_state_t g_ui = UI_ADVERTISING;
static uint32_t g_punch_flash_until;
static esp_lcd_panel_handle_t g_panel;
static led_strip_handle_t g_leds;

static uint32_t millis(void) {
    return (uint32_t)(esp_timer_get_time() / 1000ULL);
}

static esp_err_t accel_write(uint8_t reg, uint8_t value) {
    uint8_t bytes[2] = {reg, value};
    return i2c_master_write_to_device(I2C_NUM_0, ACCEL_ADDR, bytes, sizeof(bytes), pdMS_TO_TICKS(20));
}

static esp_err_t accel_read(uint8_t reg, uint8_t *out, size_t length) {
    return i2c_master_write_read_device(I2C_NUM_0, ACCEL_ADDR, &reg, 1, out, length, pdMS_TO_TICKS(20));
}

static esp_err_t accel_init(void) {
    i2c_config_t config = {
        .mode = I2C_MODE_MASTER,
        .sda_io_num = I2C_SDA,
        .scl_io_num = I2C_SCL,
        .sda_pullup_en = GPIO_PULLUP_ENABLE,
        .scl_pullup_en = GPIO_PULLUP_ENABLE,
        .master.clk_speed = 400000,
    };
    ESP_RETURN_ON_ERROR(i2c_param_config(I2C_NUM_0, &config), TAG, "i2c config");
    ESP_RETURN_ON_ERROR(i2c_driver_install(I2C_NUM_0, config.mode, 0, 0, 0), TAG, "i2c install");
    uint8_t who = 0;
    ESP_RETURN_ON_ERROR(accel_read(0x0f, &who, 1), TAG, "accelerometer WHO_AM_I read");
    if (who != 0x11) {
        ESP_LOGE(TAG, "accelerometer WHO_AM_I expected 0x11, got 0x%02x", who);
        return ESP_ERR_INVALID_RESPONSE;
    }
    ESP_RETURN_ON_ERROR(accel_write(0x20, 0x57), TAG, "accelerometer CTRL1");
    return accel_write(0x23, 0x80);
}

static bool accel_sample(int16_t *x, int16_t *y, int16_t *z) {
    uint8_t status = 0;
    if (accel_read(0x27, &status, 1) != ESP_OK || !(status & 0x08)) return false;
    uint8_t raw[6];
    uint8_t reg = 0x28 | 0x80;
    if (accel_read(reg, raw, sizeof(raw)) != ESP_OK) return false;
    *x = (int16_t)((int16_t)(raw[0] | (raw[1] << 8)) >> 4);
    *y = (int16_t)((int16_t)(raw[2] | (raw[3] << 8)) >> 4);
    *z = (int16_t)((int16_t)(raw[4] | (raw[5] << 8)) >> 4);
    return true;
}

static void buttons_init(void) {
    gpio_config_t outputs = {
        .pin_bit_mask = (1ULL << HC165_LOAD) | (1ULL << HC165_CLK),
        .mode = GPIO_MODE_OUTPUT,
    };
    ESP_ERROR_CHECK(gpio_config(&outputs));
    gpio_config_t inputs = {
        .pin_bit_mask = (1ULL << HC165_DATA) | (1ULL << START_BUTTON),
        .mode = GPIO_MODE_INPUT,
        .pull_up_en = GPIO_PULLUP_ENABLE,
    };
    ESP_ERROR_CHECK(gpio_config(&inputs));
    gpio_set_level(HC165_LOAD, 1);
    gpio_set_level(HC165_CLK, 0);
}

static uint16_t buttons_read(void) {
    uint16_t pressed = 0;
    gpio_set_level(HC165_LOAD, 0);
    esp_rom_delay_us(1);
    gpio_set_level(HC165_LOAD, 1);
    for (int bit = 0; bit < 8; ++bit) {
        if (gpio_get_level(HC165_DATA) == 0) pressed |= (uint16_t)(1u << bit);
        gpio_set_level(HC165_CLK, 1);
        esp_rom_delay_us(1);
        gpio_set_level(HC165_CLK, 0);
    }
    if (gpio_get_level(START_BUTTON) == 0) pressed |= BUTTON_START;
    return pressed;
}

static void display_fill(uint16_t color) {
    if (!g_panel) return;
    static uint16_t *stripe;
    if (!stripe) stripe = heap_caps_malloc(320 * 20 * sizeof(uint16_t), MALLOC_CAP_DMA);
    if (!stripe) return;
    for (int i = 0; i < 320 * 20; ++i) stripe[i] = color;
    for (int y = 0; y < 240; y += 20) esp_lcd_panel_draw_bitmap(g_panel, 0, y, 320, y + 20, stripe);
}

static esp_err_t display_init(void) {
    spi_bus_config_t bus = {
        .mosi_io_num = LCD_MOSI, .miso_io_num = -1, .sclk_io_num = LCD_CLK,
        .quadwp_io_num = -1, .quadhd_io_num = -1, .max_transfer_sz = 320 * 20 * 2,
    };
    ESP_RETURN_ON_ERROR(spi_bus_initialize(SPI2_HOST, &bus, SPI_DMA_CH_AUTO), TAG, "lcd spi bus");
    esp_lcd_panel_io_handle_t io;
    esp_lcd_panel_io_spi_config_t io_config = {
        .dc_gpio_num = LCD_DC, .cs_gpio_num = LCD_CS, .pclk_hz = 40000000,
        .lcd_cmd_bits = 8, .lcd_param_bits = 8, .spi_mode = 0, .trans_queue_depth = 8,
    };
    ESP_RETURN_ON_ERROR(esp_lcd_new_panel_io_spi(SPI2_HOST, &io_config, &io), TAG, "lcd io");
    esp_lcd_panel_dev_config_t panel_config = {
        .reset_gpio_num = LCD_RST, .rgb_ele_order = LCD_RGB_ELEMENT_ORDER_RGB, .bits_per_pixel = 16,
    };
    ESP_RETURN_ON_ERROR(esp_lcd_new_panel_st7789(io, &panel_config, &g_panel), TAG, "lcd panel");
    ESP_ERROR_CHECK(esp_lcd_panel_reset(g_panel));
    ESP_ERROR_CHECK(esp_lcd_panel_init(g_panel));
    ESP_ERROR_CHECK(esp_lcd_panel_invert_color(g_panel, true));
    ESP_ERROR_CHECK(esp_lcd_panel_swap_xy(g_panel, true));
    ESP_ERROR_CHECK(esp_lcd_panel_mirror(g_panel, true, false));
    ESP_ERROR_CHECK(esp_lcd_panel_disp_on_off(g_panel, true));
    return ESP_OK;
}

static void leds_init(void) {
    led_strip_config_t strip = {.strip_gpio_num = LED_DATA, .max_leds = 6, .led_model = LED_MODEL_WS2812,
                                .color_component_format = LED_STRIP_COLOR_COMPONENT_FMT_GRB};
    led_strip_rmt_config_t rmt = {.clk_src = RMT_CLK_SRC_DEFAULT, .resolution_hz = 10000000,
                                  .mem_block_symbols = 0, .flags.with_dma = false};
    ESP_ERROR_CHECK(led_strip_new_rmt_device(&strip, &rmt, &g_leds));
}

static void render_status(uint32_t now) {
    static ui_state_t previous = -1;
    static uint32_t next_led;
    ui_state_t state = g_ui;
    if (now < g_punch_flash_until) state = UI_PUNCH;
    if (state != previous) {
        previous = state;
        const uint16_t colors[] = {0x001f, 0x07ff, 0x780f, 0xfd20, 0xffe0, 0x07e0, 0xf800};
        display_fill(colors[state]);
    }
    if (!g_leds || now < next_led) return;
    next_led = now + 100;
    led_strip_clear(g_leds);
    static unsigned chase;
    if (state == UI_ADVERTISING) led_strip_set_pixel(g_leds, chase++ % 6, 0, 0, 80);
    else if (state == UI_CONNECTED) for (int i = 0; i < 6; ++i) led_strip_set_pixel(g_leds, i, 0, 40, 50);
    else if (state == UI_DISARMED) for (int i = 0; i < 6; ++i) led_strip_set_pixel(g_leds, i, 25, 0, 25);
    else if (state == UI_CALIBRATE_STILL || state == UI_CALIBRATE_PUNCH) led_strip_set_pixel(g_leds, chase++ % 6, 80, 35, 0);
    else if (state == UI_PUNCH) for (int i = 0; i < 6; ++i) led_strip_set_pixel(g_leds, i, 0, 100, 0);
    else if (state == UI_ERROR) for (int i = 0; i < 6; ++i) led_strip_set_pixel(g_leds, i, 80, 0, 0);
    led_strip_refresh(g_leds);
}

static bool load_calibration(void) {
    nvs_handle_t nvs;
    if (nvs_open("controller", NVS_READONLY, &nvs) != ESP_OK) return false;
    saved_calibration_t saved;
    size_t length = sizeof(saved);
    esp_err_t err = nvs_get_blob(nvs, "punch_cal", &saved, &length);
    nvs_close(nvs);
    if (err != ESP_OK || length != sizeof(saved) || saved.magic != 0x48425043 || saved.version != 1
            || !isfinite(saved.threshold_mg) || saved.threshold_mg < 300 || saved.threshold_mg > 4000) return false;
    punch_detector_set_calibration(&g_punch, saved.direction, saved.threshold_mg);
    return g_punch.calibrated;
}

static void save_calibration(const float direction[3], float threshold) {
    saved_calibration_t saved = {.magic = 0x48425043, .version = 1, .threshold_mg = threshold};
    memcpy(saved.direction, direction, sizeof(saved.direction));
    nvs_handle_t nvs;
    if (nvs_open("controller", NVS_READWRITE, &nvs) == ESP_OK) {
        nvs_set_blob(nvs, "punch_cal", &saved, sizeof(saved));
        nvs_commit(nvs);
        nvs_close(nvs);
    }
}

static int state_access(uint16_t conn, uint16_t attr, struct ble_gatt_access_ctxt *ctxt, void *arg) {
    uint8_t packet[CONTROLLER_PACKET_SIZE];
    controller_state_t snapshot;
    taskENTER_CRITICAL(&g_state_lock);
    snapshot = g_state;
    taskEXIT_CRITICAL(&g_state_lock);
    controller_encode_state(packet, &snapshot);
    return os_mbuf_append(ctxt->om, packet, sizeof(packet)) == 0 ? 0 : BLE_ATT_ERR_INSUFFICIENT_RES;
}

static const struct ble_gatt_svc_def GATT_SERVICES[] = {{
    .type = BLE_GATT_SVC_TYPE_PRIMARY,
    .uuid = &SERVICE_UUID.u,
    .characteristics = (struct ble_gatt_chr_def[]) {{
        .uuid = &STATE_UUID.u, .access_cb = state_access, .val_handle = &g_state_handle,
        .flags = BLE_GATT_CHR_F_READ | BLE_GATT_CHR_F_NOTIFY,
    }, {0}},
}, {0}};

static void advertise(void);

static int gap_event(struct ble_gap_event *event, void *arg) {
    switch (event->type) {
        case BLE_GAP_EVENT_CONNECT:
            if (event->connect.status == 0) g_ble_connected = true;
            else advertise();
            break;
        case BLE_GAP_EVENT_DISCONNECT:
            g_ble_connected = false;
            advertise();
            break;
        case BLE_GAP_EVENT_ADV_COMPLETE:
            advertise();
            break;
        default:
            break;
    }
    return 0;
}

static void advertise(void) {
    struct ble_hs_adv_fields fields = {0};
    fields.flags = BLE_HS_ADV_F_DISC_GEN | BLE_HS_ADV_F_BREDR_UNSUP;
    fields.uuids128 = (ble_uuid128_t *)&SERVICE_UUID;
    fields.num_uuids128 = 1;
    fields.uuids128_is_complete = 1;
    ble_gap_adv_set_fields(&fields);

    struct ble_hs_adv_fields response = {0};
    const char *name = ble_svc_gap_device_name();
    response.name = (uint8_t *)name;
    response.name_len = strlen(name);
    response.name_is_complete = 1;
    ble_gap_adv_rsp_set_fields(&response);
    struct ble_gap_adv_params params = {.conn_mode = BLE_GAP_CONN_MODE_UND, .disc_mode = BLE_GAP_DISC_MODE_GEN};
    ble_gap_adv_start(g_own_addr_type, NULL, BLE_HS_FOREVER, &params, gap_event, NULL);
}

static void ble_on_sync(void) {
    ble_hs_id_infer_auto(0, &g_own_addr_type);
    advertise();
}

static void ble_host_task(void *arg) {
    nimble_port_run();
    nimble_port_freertos_deinit();
}

static void ble_init(void) {
    uint8_t mac[6];
    esp_read_mac(mac, ESP_MAC_BT);
    char name[24];
    snprintf(name, sizeof(name), "HTN-Badge-%02X%02X", mac[4], mac[5]);
    ESP_ERROR_CHECK(nimble_port_init());
    ble_svc_gap_init();
    ble_svc_gatt_init();
    ble_svc_gap_device_name_set(name);
    ble_gatts_count_cfg(GATT_SERVICES);
    ble_gatts_add_svcs(GATT_SERVICES);
    ble_hs_cfg.sync_cb = ble_on_sync;
    nimble_port_freertos_init(ble_host_task);
}

static void notify_state(void) {
    taskENTER_CRITICAL(&g_state_lock);
    ++g_state.sequence;
    g_state.uptime_ms = millis();
    taskEXIT_CRITICAL(&g_state_lock);
    if (g_ble_connected && g_state_handle) ble_gatts_chr_updated(g_state_handle);
}

static void controller_task(void *arg) {
    uint16_t last_raw = 0, stable = 0, last_sent = 0;
    int same_count = 0;
    uint32_t start_pressed_at = 0, next_notify = 0;
    bool start_handled = false, home_was_down = false;
    float peak[3] = {0};
    float peak_magnitude = 0;
    for (;;) {
        uint32_t now = millis();
        uint16_t raw = buttons_read();
        if (raw == last_raw) same_count++; else same_count = 0;
        last_raw = raw;
        if (same_count >= 2) stable = raw;
        bool home = (stable & BUTTON_HOME) != 0;
        if (home && !home_was_down) g_armed = !g_armed;
        home_was_down = home;

        bool start = (stable & BUTTON_START) != 0;
        if (start && start_pressed_at == 0) start_pressed_at = now;
        if (start && !start_handled && now - start_pressed_at >= 1200) {
            start_handled = true;
            g_cal_phase = CAL_STILL;
            g_cal_started_ms = now;
            g_cal_samples = 0;
            g_punch.gravity_ready = false;
            peak_magnitude = 0;
            g_ui = UI_CALIBRATE_STILL;
        }
        if (!start) { start_pressed_at = 0; start_handled = false; }

        int16_t x, y, z;
        if (accel_sample(&x, &y, &z)) {
            taskENTER_CRITICAL(&g_state_lock);
            g_state.accel_x_mg = x; g_state.accel_y_mg = y; g_state.accel_z_mg = z;
            taskEXIT_CRITICAL(&g_state_lock);
            if (g_cal_phase == CAL_STILL) {
                punch_detector_observe_gravity(&g_punch, x, y, z);
                if (++g_cal_samples >= 100) {
                    g_cal_phase = CAL_PUNCH;
                    g_cal_started_ms = now;
                    g_ui = UI_CALIBRATE_PUNCH;
                }
            } else if (g_cal_phase == CAL_PUNCH) {
                float dynamic[3];
                float magnitude = punch_detector_dynamic(&g_punch, x, y, z, dynamic);
                if (magnitude > peak_magnitude) { peak_magnitude = magnitude; memcpy(peak, dynamic, sizeof(peak)); }
                if (magnitude >= 1200.0f) {
                    float threshold = fmaxf(700.0f, magnitude * 0.45f);
                    punch_detector_set_calibration(&g_punch, peak, threshold);
                    save_calibration(peak, threshold);
                    g_cal_phase = CAL_IDLE;
                    g_punch_flash_until = now + 500;
                } else if (now - g_cal_started_ms > 5000) {
                    g_cal_phase = CAL_IDLE;
                    g_ui = UI_ERROR;
                }
            } else if (g_armed && punch_detector_sample(&g_punch, x, y, z, now)) {
                taskENTER_CRITICAL(&g_state_lock);
                ++g_state.punch_counter;
                taskEXIT_CRITICAL(&g_state_lock);
                g_punch_flash_until = now + 180;
                notify_state();
            }
        }
        taskENTER_CRITICAL(&g_state_lock);
        g_state.buttons = stable;
        taskEXIT_CRITICAL(&g_state_lock);
        if (g_cal_phase == CAL_IDLE && g_ui != UI_ERROR) {
            g_ui = !g_armed ? UI_DISARMED : (g_ble_connected ? UI_CONNECTED : UI_ADVERTISING);
        }
        if (stable != last_sent || now >= next_notify) {
            last_sent = stable;
            next_notify = now + 40;
            notify_state();
        }
        render_status(now);
        vTaskDelay(pdMS_TO_TICKS(10));
    }
}

void app_main(void) {
    esp_err_t nvs_result = nvs_flash_init();
    if (nvs_result == ESP_ERR_NVS_NO_FREE_PAGES || nvs_result == ESP_ERR_NVS_NEW_VERSION_FOUND) {
        ESP_ERROR_CHECK(nvs_flash_erase());
        nvs_result = nvs_flash_init();
    }
    ESP_ERROR_CHECK(nvs_result);
    punch_detector_init(&g_punch);
    load_calibration();
    buttons_init();
    leds_init();
    if (display_init() != ESP_OK || accel_init() != ESP_OK) g_ui = UI_ERROR;
    ble_init();
    xTaskCreate(controller_task, "controller", 6144, NULL, 5, NULL);
    ESP_LOGI(TAG, "Hacker Badge Minecraft controller started");
}
