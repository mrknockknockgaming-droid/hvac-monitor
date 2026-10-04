#include "settings.h"
#include <Preferences.h>

static Preferences prefs;

void settingsBegin() { prefs.begin("hvac", false); }

// Missing keys return the default without asking NVS, which would log "nvs_get_blob ... NOT_FOUND".
float setGetF(const char* k, float d)       { return prefs.isKey(k) ? prefs.getFloat(k, d) : d; }
void  setPutF(const char* k, float v)       { prefs.putFloat(k, v); }
bool  setGetB(const char* k, bool d)        { return prefs.isKey(k) ? prefs.getBool(k, d) : d; }
void  setPutB(const char* k, bool v)        { prefs.putBool(k, v); }
uint32_t setGetU(const char* k, uint32_t d) { return prefs.isKey(k) ? prefs.getUInt(k, d) : d; }
void  setPutU(const char* k, uint32_t v)    { prefs.putUInt(k, v); }
void  setClear(const char* k)               { if (prefs.isKey(k)) prefs.remove(k); }

void Cal::load() {
  offset = setGetF((name + ".o").c_str(), 0.0f);
  scale  = setGetF((name + ".s").c_str(), 1.0f);
}
void Cal::save() {
  setPutF((name + ".o").c_str(), offset);
  setPutF((name + ".s").c_str(), scale);
}
void Cal::reset() {
  offset = 0.0f; scale = 1.0f;
  setClear((name + ".o").c_str());
  setClear((name + ".s").c_str());
}
