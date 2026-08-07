"""
Forza Horizon telemetry packet parser.

Supports two UDP payload sizes:
  232 bytes — "Sled" format (Forza Motorsport 7 / base FH format)
  311 bytes — "Car Dash" format (Forza Horizon 4/5/6, adds position, inputs, lap data)

Forza's Data Out setting must be pointed at this host's IP on UDP_PORT (default 5300).
In-game: Settings → HUD and Gameplay → Data Out → ON, and set the IP/port.
"""

import math
import struct
from dataclasses import dataclass, field
from typing import Optional

# ── Struct format strings ────────────────────────────────────────────────────
# Little-endian. The four WheelOnRumbleStrip fields are int32, not float32 —
# confirmed by capturing and manually decoding a real Forza Horizon 6 packet
# (treating them as floats doesn't shift any byte offsets, since both are
# 4 bytes, but it does corrupt those four fields' values).
SLED_FMT = "<iI27f4i20f5i"

# Classic "Car Dash" format (311 bytes) used by older Forza titles.
DASH_FMT_311 = "<iI27f4i20f5i17fH6B3b"

# Forza Horizon 6 sends 13 extra bytes beyond the classic Car Dash format:
# three unidentified int32 fields inserted right after NumCylinders (values
# observed constant during a session, e.g. (38, 0, 0) — likely a track/region
# ID) plus one trailing padding byte. Discovered by capturing a raw packet
# from the game and brute-force diffing candidate offsets against known-sane
# values (speed matching velocity magnitude, throttle/gear/boost in range).
DASH_FMT_324 = "<iI27f4i20f5i3i17fH6B3b1x"

SLED_SIZE = struct.calcsize(SLED_FMT)        # 232
DASH_SIZE_311 = struct.calcsize(DASH_FMT_311)  # 311
DASH_SIZE_324 = struct.calcsize(DASH_FMT_324)  # 324

CAR_CLASS_MAP = {0: "D", 1: "C", 2: "B", 3: "A", 4: "S1", 5: "S2", 6: "X"}
DRIVETRAIN_MAP = {0: "FWD", 1: "RWD", 2: "AWD"}


@dataclass
class ForzaPacket:
    # ── Sled fields (232 bytes, always present) ──────────────────────────────
    is_race_on: int
    timestamp_ms: int
    # Engine
    engine_max_rpm: float
    engine_idle_rpm: float
    engine_rpm: float
    # Linear acceleration (m/s²)
    accel_x: float
    accel_y: float
    accel_z: float
    # Velocity (m/s)
    vel_x: float
    vel_y: float
    vel_z: float
    # Angular velocity (rad/s)
    ang_vel_x: float
    ang_vel_y: float
    ang_vel_z: float
    # Orientation (rad)
    yaw: float
    pitch: float
    roll: float
    # Normalised suspension travel [0–1]
    norm_susp_fl: float
    norm_susp_fr: float
    norm_susp_rl: float
    norm_susp_rr: float
    # Tire slip ratio
    tire_slip_ratio_fl: float
    tire_slip_ratio_fr: float
    tire_slip_ratio_rl: float
    tire_slip_ratio_rr: float
    # Wheel rotation speed (rad/s)
    wheel_rot_speed_fl: float
    wheel_rot_speed_fr: float
    wheel_rot_speed_rl: float
    wheel_rot_speed_rr: float
    # Wheel on rumble strip (0 or 1)
    wheel_on_rumble_fl: int
    wheel_on_rumble_fr: int
    wheel_on_rumble_rl: int
    wheel_on_rumble_rr: int
    # Wheel in puddle depth (0–1)
    wheel_in_puddle_fl: float
    wheel_in_puddle_fr: float
    wheel_in_puddle_rl: float
    wheel_in_puddle_rr: float
    # Surface rumble
    surface_rumble_fl: float
    surface_rumble_fr: float
    surface_rumble_rl: float
    surface_rumble_rr: float
    # Tire slip angle (rad)
    tire_slip_angle_fl: float
    tire_slip_angle_fr: float
    tire_slip_angle_rl: float
    tire_slip_angle_rr: float
    # Tire combined slip
    tire_combined_slip_fl: float
    tire_combined_slip_fr: float
    tire_combined_slip_rl: float
    tire_combined_slip_rr: float
    # Suspension travel (m)
    susp_travel_m_fl: float
    susp_travel_m_fr: float
    susp_travel_m_rl: float
    susp_travel_m_rr: float
    # Car identity
    car_ordinal: int
    car_class: int
    car_performance_index: int
    drivetrain_type: int
    num_cylinders: int

    # ── Forza Horizon-specific fields (unidentified, present when len == 324) ─
    # Observed constant during a session (e.g. 38, 0, 0) — likely a track or
    # region identifier. Not currently surfaced on the dashboard.
    unknown_1: int = 0
    unknown_2: int = 0
    unknown_3: int = 0

    # ── Car Dash fields (present when len == 311 or 324) ─────────────────────
    pos_x: float = 0.0
    pos_y: float = 0.0
    pos_z: float = 0.0
    speed: float = 0.0          # m/s
    power: float = 0.0          # watts
    torque: float = 0.0         # N·m
    tire_temp_fl: float = 0.0   # °F
    tire_temp_fr: float = 0.0
    tire_temp_rl: float = 0.0
    tire_temp_rr: float = 0.0
    boost: float = 0.0
    fuel: float = 0.0           # 0.0 = empty, 1.0 = full
    distance_traveled: float = 0.0  # m
    best_lap: float = -1.0      # s; −1 means no best lap yet
    last_lap: float = -1.0      # s
    current_lap: float = 0.0    # s
    current_race_time: float = 0.0
    lap_number: int = 0
    race_position: int = 0
    accel_input: int = 0        # 0–255
    brake_input: int = 0        # 0–255
    clutch_input: int = 0       # 0–255
    handbrake_input: int = 0    # 0–255
    gear: int = 0               # 0 = neutral/reverse, 1–8 = forward gears
    steer: int = 0              # −127 to 127
    norm_driving_line: int = 0
    norm_ai_brake_diff: int = 0

    has_dash: bool = field(default=False, compare=False, repr=False)

    # ── Derived properties ───────────────────────────────────────────────────

    @property
    def speed_kmh(self) -> float:
        return self.speed * 3.6

    @property
    def speed_mph(self) -> float:
        return self.speed * 2.23694

    @property
    def power_hp(self) -> float:
        return self.power / 745.7

    @property
    def torque_ftlb(self) -> float:
        return self.torque * 0.7376

    @property
    def throttle_pct(self) -> float:
        return self.accel_input / 255.0 * 100.0

    @property
    def brake_pct(self) -> float:
        return self.brake_input / 255.0 * 100.0

    @property
    def steer_norm(self) -> float:
        return self.steer / 127.0

    @property
    def g_lateral(self) -> float:
        return self.accel_x / 9.81

    @property
    def g_longitudinal(self) -> float:
        return self.accel_z / 9.81

    @property
    def g_vertical(self) -> float:
        return self.accel_y / 9.81

    @property
    def car_class_name(self) -> str:
        return CAR_CLASS_MAP.get(self.car_class, "?")

    @property
    def drivetrain_name(self) -> str:
        return DRIVETRAIN_MAP.get(self.drivetrain_type, "?")


def parse_packet(data: bytes) -> Optional[ForzaPacket]:
    """Parse a raw UDP payload into a ForzaPacket.

    Returns None for unknown sizes or malformed data; the caller should
    discard these silently.
    """
    n = len(data)
    if n == SLED_SIZE:
        try:
            vals = struct.unpack(SLED_FMT, data)
        except struct.error:
            return None
        return ForzaPacket(*vals)
    elif n == DASH_SIZE_311:
        try:
            vals = struct.unpack(DASH_FMT_311, data)
        except struct.error:
            return None
        # Classic format has no unknown_1/2/3 — splice in zeros for those slots.
        pkt = ForzaPacket(*vals[:58], 0, 0, 0, *vals[58:85])
        pkt.has_dash = True
        return pkt
    # Forza Horizon 6's actual format (324 bytes). Also handles titles that
    # send even more trailing bytes by parsing just the first 324.
    elif n >= DASH_SIZE_324:
        try:
            vals = struct.unpack_from(DASH_FMT_324, data, 0)
        except struct.error:
            return None
        pkt = ForzaPacket(*vals)
        pkt.has_dash = True
        return pkt
    return None
