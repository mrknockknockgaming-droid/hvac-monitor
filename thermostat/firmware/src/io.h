// The thermostat's own hardware: room sensor, output switches, state sensing, watchdog.
#pragma once
#include "tstat_core.h"

struct Sensed {                 // what the equipment terminals actually see (24 VAC present)
    bool ok = false;
    bool Y = false, Y2 = false, W = false, G = false, OB = false;
};

bool io_begin();                // Wire + the MCP23017; all outputs off
bool io_outputs(const tstat::Outputs& o);   // false if the expander didn't take it
void io_watchdog();             // toggle the TPS3823's WDI: call every loop (< 1.6 s)
Sensed io_sense();
// Room temperature (F, with ROOM_OFFSET_F) and humidity; NAN after 3 failed reads in a row,
// which makes the controller turn everything off.
void io_room(double& temp_f, double& rh);
