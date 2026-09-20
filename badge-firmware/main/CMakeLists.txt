idf_component_register(
  SRCS "main.c" "controller_protocol.c" "punch_detector.c"
  INCLUDE_DIRS "."
  REQUIRES bt driver esp_lcd nvs_flash
)
