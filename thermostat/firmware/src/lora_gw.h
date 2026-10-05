// The thermostat as the site's LoRa gateway (config.h LORA_GATEWAY 1; radio/README.md): the
// same job as the plug-in gateway (radio/lora/src/main.cpp), plus the outdoor node's air
// temperature goes straight to the controller, so the heat-pump lockouts keep working with no
// internet.
#pragma once
#include <cstddef>
#include <cstdint>

void lora_gw_begin();
void lora_gw_loop();
// An MQTT message the gateway may want (hvac/<site>/<node>/cmd for one of its LoRa nodes).
bool lora_gw_on_mqtt(const char* topic, const uint8_t* payload, size_t len);
