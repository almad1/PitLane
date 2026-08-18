"""Byte-level tests for the Forza UDP packet parser.

A byte-offset regression here corrupts every downstream number in the stack,
so these build real struct payloads for all three supported sizes and check
both raw fields and the derived properties the dashboard actually displays.
"""

import struct

import parser as fz


def _sled_values(**over):
    """58 values matching SLED_FMT, distinctive enough to catch offset slips."""
    v = {
        "is_race_on": 1,
        "timestamp_ms": 123456,
        "engine_max_rpm": 8500.0,
        "engine_idle_rpm": 900.0,
        "engine_rpm": 4321.0,
        "accel_x": 4.905,        # 0.5 G lateral
        "accel_y": 0.0,
        "accel_z": -9.81,        # -1.0 G longitudinal
        "vel_x": 10.0, "vel_y": 0.0, "vel_z": 20.0,
        "ang_vel_x": 0.1, "ang_vel_y": 0.2, "ang_vel_z": 0.3,
        "yaw": 1.0, "pitch": 0.05, "roll": -0.05,
        "norm_susp_fl": 0.4, "norm_susp_fr": 0.5,
        "norm_susp_rl": 0.6, "norm_susp_rr": 0.7,
        "tire_slip_ratio_fl": 0.01, "tire_slip_ratio_fr": 0.02,
        "tire_slip_ratio_rl": 0.03, "tire_slip_ratio_rr": 0.04,
        "wheel_rot_speed_fl": 90.0, "wheel_rot_speed_fr": 91.0,
        "wheel_rot_speed_rl": 92.0, "wheel_rot_speed_rr": 93.0,
        "wheel_on_rumble_fl": 0, "wheel_on_rumble_fr": 1,
        "wheel_on_rumble_rl": 0, "wheel_on_rumble_rr": 1,
        "wheel_in_puddle_fl": 0.0, "wheel_in_puddle_fr": 0.0,
        "wheel_in_puddle_rl": 0.0, "wheel_in_puddle_rr": 0.0,
        "surface_rumble_fl": 0.0, "surface_rumble_fr": 0.0,
        "surface_rumble_rl": 0.0, "surface_rumble_rr": 0.0,
        "tire_slip_angle_fl": 0.11, "tire_slip_angle_fr": 0.12,
        "tire_slip_angle_rl": 0.13, "tire_slip_angle_rr": 0.14,
        "tire_combined_slip_fl": 0.21, "tire_combined_slip_fr": 0.22,
        "tire_combined_slip_rl": 0.23, "tire_combined_slip_rr": 0.24,
        "susp_travel_m_fl": 0.031, "susp_travel_m_fr": 0.032,
        "susp_travel_m_rl": 0.033, "susp_travel_m_rr": 0.034,
        "car_ordinal": 4257,
        "car_class": 0,          # D
        "car_performance_index": 500,
        "drivetrain_type": 0,    # FWD
        "num_cylinders": 4,
    }
    v.update(over)
    return v


_SLED_ORDER = list(_sled_values().keys())


def _dash_extra(**over):
    """Fields appended by the Car Dash formats, in struct order."""
    v = {
        "pos_x": 1500.5, "pos_y": 134.0, "pos_z": -2200.25,
        "speed": 25.0,           # m/s -> 90 km/h
        "power": 150000.0,       # W  -> 150 kW
        "torque": 320.0,
        "tire_temp_fl": 180.0, "tire_temp_fr": 181.0,
        "tire_temp_rl": 182.0, "tire_temp_rr": 183.0,
        "boost": 8.5,
        "fuel": 0.75,
        "distance_traveled": 5432.1,
        "best_lap": 61.5, "last_lap": 62.5, "current_lap": 30.25,
        "current_race_time": 200.0,
        "lap_number": 3,
        "race_position": 2,
        "accel_input": 255, "brake_input": 0, "clutch_input": 0,
        "handbrake_input": 0,
        "gear": 4,
        "steer": -64,
        "norm_driving_line": 0, "norm_ai_brake_diff": 0,
    }
    v.update(over)
    return v


_DASH_ORDER = list(_dash_extra().keys())


def _pack_sled(**over):
    sv = _sled_values(**over)
    return struct.pack(fz.SLED_FMT, *[sv[k] for k in _SLED_ORDER])


def _pack_dash_324(sled_over=None, **dash_over):
    sv = _sled_values(**(sled_over or {}))
    dv = _dash_extra(**dash_over)
    vals = [sv[k] for k in _SLED_ORDER] + [38, 0, 0] + [dv[k] for k in _DASH_ORDER]
    return struct.pack(fz.DASH_FMT_324, *vals)


def _pack_dash_311(sled_over=None, **dash_over):
    sv = _sled_values(**(sled_over or {}))
    dv = _dash_extra(**dash_over)
    vals = [sv[k] for k in _SLED_ORDER] + [dv[k] for k in _DASH_ORDER]
    return struct.pack(fz.DASH_FMT_311, *vals)


# ── Format sizes are load-bearing: a struct edit that changes them breaks
#    packet recognition entirely. ────────────────────────────────────────────
def test_format_sizes():
    assert fz.SLED_SIZE == 232
    assert fz.DASH_SIZE_311 == 311
    assert fz.DASH_SIZE_324 == 324


def test_sled_roundtrip():
    pkt = fz.parse_packet(_pack_sled())
    assert pkt is not None
    assert pkt.has_dash is False
    assert pkt.is_race_on == 1
    assert pkt.engine_rpm == 4321.0
    assert pkt.car_ordinal == 4257
    assert pkt.num_cylinders == 4
    # Last floats before the trailing ints — catches off-by-one-field slips.
    assert abs(pkt.susp_travel_m_rr - 0.034) < 1e-6
    assert abs(pkt.tire_combined_slip_fl - 0.21) < 1e-6


def test_dash_324_roundtrip():
    pkt = fz.parse_packet(_pack_dash_324())
    assert pkt is not None and pkt.has_dash is True
    assert abs(pkt.pos_x - 1500.5) < 1e-3
    assert abs(pkt.pos_z - (-2200.25)) < 1e-3
    assert pkt.lap_number == 3
    assert pkt.race_position == 2
    assert pkt.gear == 4
    assert pkt.steer == -64
    assert abs(pkt.best_lap - 61.5) < 1e-6
    assert pkt.unknown_1 == 38  # FH6 mystery fields decoded, not skipped


def test_dash_311_roundtrip():
    pkt = fz.parse_packet(_pack_dash_311())
    assert pkt is not None and pkt.has_dash is True
    # Classic format has no unknown_1/2/3; the splice must leave zeros there
    # and keep every following field aligned.
    assert pkt.unknown_1 == 0
    assert pkt.gear == 4
    assert abs(pkt.fuel - 0.75) < 1e-6


def test_oversize_packet_parses_first_324_bytes():
    pkt = fz.parse_packet(_pack_dash_324() + b"\x00" * 12)
    assert pkt is not None and pkt.has_dash is True
    assert pkt.lap_number == 3


def test_unknown_sizes_rejected():
    assert fz.parse_packet(b"") is None
    assert fz.parse_packet(b"\x00" * 100) is None
    assert fz.parse_packet(b"\x00" * 233) is None   # sled + 1
    assert fz.parse_packet(b"\x00" * 320) is None   # between 311 and 324


def test_derived_properties():
    pkt = fz.parse_packet(_pack_dash_324())
    assert abs(pkt.speed_kmh - 90.0) < 1e-6         # 25 m/s
    assert abs(pkt.throttle_pct - 100.0) < 1e-6     # accel_input 255
    assert abs(pkt.brake_pct) < 1e-6
    assert abs(pkt.steer_norm - (-64 / 127.0)) < 1e-6
    assert abs(pkt.g_lateral - 0.5) < 1e-3          # accel_x 4.905
    assert abs(pkt.g_longitudinal - (-1.0)) < 1e-3  # accel_z -9.81
    assert pkt.car_class_name == "D"
    assert pkt.drivetrain_name == "FWD"


def test_unmapped_enum_values():
    pkt = fz.parse_packet(_pack_sled(car_class=9, drivetrain_type=7))
    assert pkt.car_class_name == "?"
    assert pkt.drivetrain_name == "?"
