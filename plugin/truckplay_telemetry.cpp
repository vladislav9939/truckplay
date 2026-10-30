/**
 * TruckPlay telemetry plugin for ETS2 / ATS.
 *
 * Reads a handful of SCS SDK channels and writes them as JSON into
 * %LOCALAPPDATA%\TruckPlay\telemetry.json (atomic replace), so TruckPlay
 * can show a trip computer without any external telemetry servers.
 */

#define WIN32_LEAN_AND_MEAN
#include <windows.h>
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <cstdint>
#include <cmath>
#include <string>

#include "scssdk_telemetry.h"
#include "eurotrucks2/scssdk_eut2.h"
#include "eurotrucks2/scssdk_telemetry_eut2.h"
#include "amtrucks/scssdk_ats.h"
#include "amtrucks/scssdk_telemetry_ats.h"
#include "common/scssdk_telemetry_common_channels.h"
#include "common/scssdk_telemetry_truck_common_channels.h"
#include "common/scssdk_telemetry_trailer_common_channels.h"
#include "common/scssdk_telemetry_job_common_channels.h"
#include "common/scssdk_telemetry_common_configs.h"

static scs_log_t game_log = NULL;
static std::wstring g_dir;      /* ...\TruckPlay */
static std::wstring g_file;     /* ...\TruckPlay\telemetry.json */
static std::wstring g_tmp;      /* ...\TruckPlay\telemetry.json.tmp */
static DWORD g_last_write = 0;

struct state_t {
    bool  paused = true;
    float speed = 0.f, rpm = 0.f;
    scs_s32_t gear = 0, gear_disp = 0;
    bool  gear_ok = false, gear_disp_ok = false;
    float fuel = -1.f, fuel_cap = 0.f, fuel_range = -1.f, fuel_cons = 0.f;
    float adblue = -1.f, adblue_cap = 0.f;
    float odometer = -1.f, cruise = 0.f, air = -1.f;
    float wear[5] = {0.f, 0.f, 0.f, 0.f, 0.f};
    bool  wear_ok[5] = {false, false, false, false, false};
    float nav_dist = -1.f, nav_time = -1.f, nav_limit = -1.f;
    bool  trailer = false;

    char  cargo[160] = "";
    char  from_city[96] = "";
    char  to_city[96] = "";
    char  to_company[96] = "";
    float cargo_mass = -1.f, income = -1.f, planned_km = -1.f, delivery_min = -1.f;
} S;

/* ------------------------------------------------------------------ */

static void json_escape(const char *src, char *dst, size_t cap) {
    size_t o = 0;
    if (cap < 4) { if (cap) dst[0] = 0; return; }
    for (size_t i = 0; src[i] && o + 7 < cap; ++i) {
        unsigned char c = (unsigned char)src[i];
        if (c == '"' || c == '\\') { dst[o++] = '\\'; dst[o++] = (char)c; }
        else if (c == '\n' || c == '\r' || c == '\t') { dst[o++] = ' '; }
        else if (c < 0x20) { dst[o++] = ' '; }
        else { dst[o++] = (char)c; }
    }
    dst[o] = 0;
}

static uint64_t now_ms() {
    FILETIME ft;
    GetSystemTimeAsFileTime(&ft);
    ULARGE_INTEGER u;
    u.LowPart = ft.dwLowDateTime;
    u.HighPart = ft.dwHighDateTime;
    return (u.QuadPart - 116444736000000000ULL) / 10000ULL;
}

enum kind_t {
    K_SPEED = 1, K_RPM, K_GEAR, K_GEAR_DISP, K_FUEL, K_FUEL_RANGE, K_FUEL_CONS,
    K_ADBLUE, K_ODOMETER, K_CRUISE, K_AIR,
    K_WEAR_ENGINE, K_WEAR_TRANSMISSION, K_WEAR_CABIN, K_WEAR_CHASSIS, K_WEAR_WHEELS,
    K_NAV_DIST, K_NAV_TIME, K_NAV_LIMIT, K_TRAILER
};

static void SCSAPIFUNC on_channel(const scs_string_t /*name*/, const scs_u32_t /*index*/,
                                  const scs_value_t *const value, const scs_context_t context) {
    if (!value) return;
    const int k = (int)(intptr_t)context;
    switch (k) {
        case K_SPEED:        S.speed = value->value_float.value; break;
        case K_RPM:          S.rpm = value->value_float.value; break;
        case K_GEAR:         S.gear = value->value_s32.value; S.gear_ok = true; break;
        case K_GEAR_DISP:    S.gear_disp = value->value_s32.value; S.gear_disp_ok = true; break;
        case K_FUEL:         S.fuel = value->value_float.value; break;
        case K_FUEL_RANGE:   S.fuel_range = value->value_float.value; break;
        case K_FUEL_CONS:    S.fuel_cons = value->value_float.value; break;
        case K_ADBLUE:       S.adblue = value->value_float.value; break;
        case K_ODOMETER:     S.odometer = value->value_float.value; break;
        case K_CRUISE:       S.cruise = value->value_float.value; break;
        case K_AIR:          S.air = value->value_float.value; break;
        case K_WEAR_ENGINE:       S.wear[0] = value->value_float.value; S.wear_ok[0] = true; break;
        case K_WEAR_TRANSMISSION: S.wear[1] = value->value_float.value; S.wear_ok[1] = true; break;
        case K_WEAR_CABIN:        S.wear[2] = value->value_float.value; S.wear_ok[2] = true; break;
        case K_WEAR_CHASSIS:      S.wear[3] = value->value_float.value; S.wear_ok[3] = true; break;
        case K_WEAR_WHEELS:       S.wear[4] = value->value_float.value; S.wear_ok[4] = true; break;
        case K_NAV_DIST:     S.nav_dist = value->value_float.value; break;
        case K_NAV_TIME:     S.nav_time = value->value_float.value; break;
        case K_NAV_LIMIT:    S.nav_limit = value->value_float.value; break;
        case K_TRAILER:      S.trailer = value->value_bool.value != 0; break;
        default: break;
    }
}

static void copy_str(char *dst, size_t cap, const scs_value_t &v) {
    if (v.type == SCS_VALUE_TYPE_string && v.value_string.value) {
        strncpy(dst, v.value_string.value, cap - 1);
        dst[cap - 1] = 0;
    }
}

static float as_float(const scs_value_t &v) {
    if (v.type == SCS_VALUE_TYPE_float) return v.value_float.value;
    if (v.type == SCS_VALUE_TYPE_double) return (float)v.value_double.value;
    if (v.type == SCS_VALUE_TYPE_u32) return (float)v.value_u32.value;
    if (v.type == SCS_VALUE_TYPE_s32) return (float)v.value_s32.value;
    if (v.type == SCS_VALUE_TYPE_u64) return (float)v.value_u64.value;
    return 0.f;
}

static void SCSAPIFUNC on_configuration(const scs_event_t /*event*/, const void *const event_info,
                                        const scs_context_t /*context*/) {
    const scs_telemetry_configuration_t *cfg = (const scs_telemetry_configuration_t *)event_info;
    if (!cfg || !cfg->id) return;

    if (strcmp(cfg->id, SCS_TELEMETRY_CONFIG_truck) == 0) {
        for (const scs_named_value_t *a = cfg->attributes; a && a->name; ++a) {
            if (strcmp(a->name, SCS_TELEMETRY_CONFIG_ATTRIBUTE_fuel_capacity) == 0)
                S.fuel_cap = as_float(a->value);
            else if (strcmp(a->name, SCS_TELEMETRY_CONFIG_ATTRIBUTE_adblue_capacity) == 0)
                S.adblue_cap = as_float(a->value);
        }
    } else if (strcmp(cfg->id, SCS_TELEMETRY_CONFIG_job) == 0) {
        for (const scs_named_value_t *a = cfg->attributes; a && a->name; ++a) {
            if (strcmp(a->name, SCS_TELEMETRY_CONFIG_ATTRIBUTE_cargo) == 0)
                copy_str(S.cargo, sizeof S.cargo, a->value);
            else if (strcmp(a->name, SCS_TELEMETRY_CONFIG_ATTRIBUTE_cargo_mass) == 0)
                S.cargo_mass = as_float(a->value);
            else if (strcmp(a->name, SCS_TELEMETRY_CONFIG_ATTRIBUTE_source_city) == 0)
                copy_str(S.from_city, sizeof S.from_city, a->value);
            else if (strcmp(a->name, SCS_TELEMETRY_CONFIG_ATTRIBUTE_destination_city) == 0)
                copy_str(S.to_city, sizeof S.to_city, a->value);
            else if (strcmp(a->name, SCS_TELEMETRY_CONFIG_ATTRIBUTE_destination_company) == 0)
                copy_str(S.to_company, sizeof S.to_company, a->value);
            else if (strcmp(a->name, SCS_TELEMETRY_CONFIG_ATTRIBUTE_income) == 0)
                S.income = as_float(a->value);
            else if (strcmp(a->name, SCS_TELEMETRY_CONFIG_ATTRIBUTE_delivery_time) == 0)
                S.delivery_min = as_float(a->value);
            else if (strcmp(a->name, SCS_TELEMETRY_CONFIG_ATTRIBUTE_planned_distance_km) == 0)
                S.planned_km = as_float(a->value);
        }
    }
}

static void SCSAPIFUNC on_event(const scs_event_t event, const void *const /*info*/,
                                const scs_context_t /*context*/) {
    switch (event) {
        case SCS_TELEMETRY_EVENT_paused:  S.paused = true; break;
        case SCS_TELEMETRY_EVENT_started: S.paused = false; break;
        default: break;
    }
}

static void publish(bool force) {
    const DWORD now = GetTickCount();
    if (!force && now - g_last_write < 100) return;
    g_last_write = now;

    char cargo[352], from[224], to[224], comp[224];
    json_escape(S.cargo, cargo, sizeof cargo);
    json_escape(S.from_city, from, sizeof from);
    json_escape(S.to_city, to, sizeof to);
    json_escape(S.to_company, comp, sizeof comp);

    const int gear = S.gear_disp_ok ? S.gear_disp : S.gear;

    char json[6144];
    const int n = snprintf(
        json, sizeof json,
        "{\"t\":%llu,\"paused\":%d,\"speed\":%.3f,\"rpm\":%.1f,\"gear\":%d,"
        "\"fuel\":%.2f,\"fuel_cap\":%.2f,\"range\":%.1f,\"consumption\":%.2f,"
        "\"adblue\":%.2f,\"adblue_cap\":%.2f,\"odometer\":%.1f,\"cruise\":%.1f,\"air\":%.2f,"
        "\"wear\":{\"engine\":%.4f,\"transmission\":%.4f,\"cabin\":%.4f,"
        "\"chassis\":%.4f,\"wheels\":%.4f},"
        "\"nav\":{\"dist\":%.1f,\"time\":%.0f,\"limit\":%.0f},"
        "\"job\":{\"cargo\":\"%s\",\"mass\":%.0f,\"from\":\"%s\",\"to\":\"%s\","
        "\"company\":\"%s\",\"income\":%.0f,\"delivery\":%.0f,\"planned\":%.1f},"
        "\"trailer\":%d}",
        (unsigned long long)now_ms(), S.paused ? 1 : 0,
        S.speed, S.rpm, gear,
        S.fuel, S.fuel_cap, S.fuel_range, S.fuel_cons,
        S.adblue, S.adblue_cap, S.odometer, S.cruise, S.air,
        S.wear[0], S.wear[1], S.wear[2], S.wear[3], S.wear[4],
        S.nav_dist, S.nav_time, S.nav_limit,
        cargo, S.cargo_mass, from, to, comp, S.income, S.delivery_min, S.planned_km,
        S.trailer ? 1 : 0);

    if (n <= 0) return;

    HANDLE h = CreateFileW(g_tmp.c_str(), GENERIC_WRITE, 0, NULL, CREATE_ALWAYS,
                           FILE_ATTRIBUTE_NORMAL, NULL);
    if (h == INVALID_HANDLE_VALUE) return;
    DWORD written = 0;
    WriteFile(h, json, (DWORD)n, &written, NULL);
    CloseHandle(h);
    MoveFileExW(g_tmp.c_str(), g_file.c_str(), MOVEFILE_REPLACE_EXISTING);
}

static void SCSAPIFUNC on_frame_end(const scs_event_t /*event*/, const void *const /*info*/,
                                    const scs_context_t /*context*/) {
    publish(false);
}

/* ------------------------------------------------------------------ */

static void init_paths() {
    wchar_t buf[MAX_PATH];
    DWORD n = GetEnvironmentVariableW(L"LOCALAPPDATA", buf, MAX_PATH);
    if (n == 0 || n >= MAX_PATH) return;
    g_dir = std::wstring(buf) + L"\\TruckPlay";
    CreateDirectoryW(g_dir.c_str(), NULL);
    g_file = g_dir + L"\\telemetry.json";
    g_tmp = g_file + L".tmp";
}

SCSAPI_RESULT scs_telemetry_init(const scs_u32_t version, const scs_telemetry_init_params_t *const params) {
    if (version != SCS_TELEMETRY_VERSION_1_00 && version != SCS_TELEMETRY_VERSION_1_01)
        return SCS_RESULT_unsupported;

    const scs_telemetry_init_params_v101_t *p =
        (const scs_telemetry_init_params_v101_t *)params;
    if (!p) return SCS_RESULT_generic_error;

    game_log = p->common.log;
    init_paths();
    if (g_file.empty()) {
        if (game_log) game_log(SCS_LOG_TYPE_error, "TruckPlay: no LOCALAPPDATA");
        return SCS_RESULT_generic_error;
    }
    publish(true);

    p->register_for_event(SCS_TELEMETRY_EVENT_frame_end, on_frame_end, NULL);
    p->register_for_event(SCS_TELEMETRY_EVENT_configuration, on_configuration, NULL);
    p->register_for_event(SCS_TELEMETRY_EVENT_paused, on_event, NULL);
    p->register_for_event(SCS_TELEMETRY_EVENT_started, on_event, NULL);

    struct { const char *name; scs_value_type_t type; kind_t kind; } ch[] = {
        { SCS_TELEMETRY_TRUCK_CHANNEL_speed,                  SCS_VALUE_TYPE_float, K_SPEED },
        { SCS_TELEMETRY_TRUCK_CHANNEL_engine_rpm,             SCS_VALUE_TYPE_float, K_RPM },
        { SCS_TELEMETRY_TRUCK_CHANNEL_engine_gear,            SCS_VALUE_TYPE_s32,   K_GEAR },
        { SCS_TELEMETRY_TRUCK_CHANNEL_displayed_gear,         SCS_VALUE_TYPE_s32,   K_GEAR_DISP },
        { SCS_TELEMETRY_TRUCK_CHANNEL_fuel,                   SCS_VALUE_TYPE_float, K_FUEL },
        { SCS_TELEMETRY_TRUCK_CHANNEL_fuel_range,             SCS_VALUE_TYPE_float, K_FUEL_RANGE },
        { SCS_TELEMETRY_TRUCK_CHANNEL_fuel_average_consumption, SCS_VALUE_TYPE_float, K_FUEL_CONS },
        { SCS_TELEMETRY_TRUCK_CHANNEL_adblue,                 SCS_VALUE_TYPE_float, K_ADBLUE },
        { SCS_TELEMETRY_TRUCK_CHANNEL_odometer,               SCS_VALUE_TYPE_float, K_ODOMETER },
        { SCS_TELEMETRY_TRUCK_CHANNEL_cruise_control,         SCS_VALUE_TYPE_float, K_CRUISE },
        { SCS_TELEMETRY_TRUCK_CHANNEL_brake_air_pressure,     SCS_VALUE_TYPE_float, K_AIR },
        { SCS_TELEMETRY_TRUCK_CHANNEL_wear_engine,            SCS_VALUE_TYPE_float, K_WEAR_ENGINE },
        { SCS_TELEMETRY_TRUCK_CHANNEL_wear_transmission,      SCS_VALUE_TYPE_float, K_WEAR_TRANSMISSION },
        { SCS_TELEMETRY_TRUCK_CHANNEL_wear_cabin,             SCS_VALUE_TYPE_float, K_WEAR_CABIN },
        { SCS_TELEMETRY_TRUCK_CHANNEL_wear_chassis,           SCS_VALUE_TYPE_float, K_WEAR_CHASSIS },
        { SCS_TELEMETRY_TRUCK_CHANNEL_wear_wheels,            SCS_VALUE_TYPE_float, K_WEAR_WHEELS },
        { SCS_TELEMETRY_TRUCK_CHANNEL_navigation_distance,    SCS_VALUE_TYPE_float, K_NAV_DIST },
        { SCS_TELEMETRY_TRUCK_CHANNEL_navigation_time,        SCS_VALUE_TYPE_float, K_NAV_TIME },
        { SCS_TELEMETRY_TRUCK_CHANNEL_navigation_speed_limit, SCS_VALUE_TYPE_float, K_NAV_LIMIT },
        { SCS_TELEMETRY_TRAILER_CHANNEL_connected,            SCS_VALUE_TYPE_bool,  K_TRAILER },
    };
    for (unsigned i = 0; i < sizeof(ch) / sizeof(ch[0]); ++i) {
        p->register_for_channel(ch[i].name, SCS_U32_NIL, ch[i].type,
                                SCS_TELEMETRY_CHANNEL_FLAG_no_value,
                                on_channel, (scs_context_t)(intptr_t)ch[i].kind);
    }

    if (game_log) game_log(SCS_LOG_TYPE_message, "TruckPlay: telemetry plugin ready");
    return SCS_RESULT_ok;
}

SCSAPI_VOID scs_telemetry_shutdown(void) {
    g_last_write = 0;
    game_log = NULL;
}
