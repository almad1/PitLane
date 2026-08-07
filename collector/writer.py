"""
InfluxDB writer for Forza telemetry.

Writes two measurements:
  telemetry — one point per received packet (while is_race_on == 1)
  laps      — one point per completed lap, with aggregate stats

Also owns the LapTracker which manages session IDs and lap detection.
"""

import logging
import math
import os
import time
import uuid
from dataclasses import dataclass, field

from influxdb_client import InfluxDBClient, Point
from influxdb_client.client.write_api import SYNCHRONOUS

from parser import ForzaPacket

log = logging.getLogger(__name__)


def _safe_float(v: float) -> float | None:
    """Return v if it's a finite float, else None (skip the field)."""
    if isinstance(v, float) and (math.isnan(v) or math.isinf(v)):
        return None
    return float(v)


def _add_field(point: Point, name: str, value) -> None:
    """Add a field to the point, silently dropping NaN/Inf floats."""
    if isinstance(value, float):
        safe = _safe_float(value)
        if safe is not None:
            point.field(name, safe)
    else:
        point.field(name, value)


class TelemetryWriter:
    """Thin wrapper around InfluxDB client for writing Forza telemetry."""

    def __init__(self) -> None:
        self._url = os.environ.get("INFLUXDB_URL", "http://influxdb:8086")
        self._token = os.environ.get("INFLUXDB_TOKEN", "")
        self._org = os.environ.get("INFLUXDB_ORG", "pitlane")
        self._bucket = os.environ.get("INFLUXDB_BUCKET", "forza")
        self._client = InfluxDBClient(
            url=self._url, token=self._token, org=self._org
        )
        self._write_api = self._client.write_api(write_options=SYNCHRONOUS)

    # ── Connection management ────────────────────────────────────────────────

    def wait_for_influxdb(self, max_retries: int = 30, delay: float = 2.0) -> None:
        """Block until InfluxDB responds to a ping, or raise after timeout."""
        log.info("Waiting for InfluxDB at %s …", self._url)
        for attempt in range(1, max_retries + 1):
            try:
                if self._client.ping():
                    log.info("InfluxDB is ready (attempt %d)", attempt)
                    return
            except Exception as exc:  # noqa: BLE001
                log.debug("Ping failed (attempt %d/%d): %s", attempt, max_retries, exc)
            time.sleep(delay)
        raise RuntimeError(
            f"InfluxDB at {self._url} did not respond after {max_retries} attempts"
        )

    def close(self) -> None:
        self._write_api.close()
        self._client.close()

    # ── Write helpers ────────────────────────────────────────────────────────

    def write_telemetry(self, packet: ForzaPacket, session_id: str, race_on: bool = True) -> None:
        """Write a single telemetry point to InfluxDB."""
        p = (
            Point("telemetry")
            .tag("session_id", session_id)
            .tag("car_ordinal", str(packet.car_ordinal))
            .tag("car_class", packet.car_class_name)
            .tag("drivetrain", packet.drivetrain_name)
            .field("race_on", 1 if race_on else 0)
        )

        # ── Engine ──────────────────────────────────────────────────────────
        _add_field(p, "engine_rpm", packet.engine_rpm)
        _add_field(p, "engine_max_rpm", packet.engine_max_rpm)
        _add_field(p, "engine_idle_rpm", packet.engine_idle_rpm)

        # ── Motion ──────────────────────────────────────────────────────────
        _add_field(p, "speed_ms", packet.speed)
        _add_field(p, "speed_kmh", packet.speed_kmh)
        _add_field(p, "speed_mph", packet.speed_mph)
        _add_field(p, "accel_x", packet.accel_x)
        _add_field(p, "accel_y", packet.accel_y)
        _add_field(p, "accel_z", packet.accel_z)
        _add_field(p, "vel_x", packet.vel_x)
        _add_field(p, "vel_y", packet.vel_y)
        _add_field(p, "vel_z", packet.vel_z)
        _add_field(p, "g_lateral", packet.g_lateral)
        _add_field(p, "g_longitudinal", packet.g_longitudinal)
        _add_field(p, "g_vertical", packet.g_vertical)

        # ── Orientation ──────────────────────────────────────────────────────
        _add_field(p, "yaw", packet.yaw)
        _add_field(p, "pitch", packet.pitch)
        _add_field(p, "roll", packet.roll)

        # ── Power & fuel ─────────────────────────────────────────────────────
        _add_field(p, "power_w", packet.power)
        _add_field(p, "power_hp", packet.power_hp)
        _add_field(p, "torque_nm", packet.torque)
        _add_field(p, "torque_ftlb", packet.torque_ftlb)
        _add_field(p, "boost", packet.boost)
        _add_field(p, "fuel", packet.fuel)

        # ── Position ─────────────────────────────────────────────────────────
        _add_field(p, "pos_x", packet.pos_x)
        _add_field(p, "pos_y", packet.pos_y)
        _add_field(p, "pos_z", packet.pos_z)
        _add_field(p, "distance_traveled", packet.distance_traveled)

        # ── Tires ────────────────────────────────────────────────────────────
        _add_field(p, "tire_temp_fl", packet.tire_temp_fl)
        _add_field(p, "tire_temp_fr", packet.tire_temp_fr)
        _add_field(p, "tire_temp_rl", packet.tire_temp_rl)
        _add_field(p, "tire_temp_rr", packet.tire_temp_rr)
        _add_field(p, "tire_slip_ratio_fl", packet.tire_slip_ratio_fl)
        _add_field(p, "tire_slip_ratio_fr", packet.tire_slip_ratio_fr)
        _add_field(p, "tire_slip_ratio_rl", packet.tire_slip_ratio_rl)
        _add_field(p, "tire_slip_ratio_rr", packet.tire_slip_ratio_rr)
        _add_field(p, "tire_slip_angle_fl", packet.tire_slip_angle_fl)
        _add_field(p, "tire_slip_angle_fr", packet.tire_slip_angle_fr)
        _add_field(p, "tire_slip_angle_rl", packet.tire_slip_angle_rl)
        _add_field(p, "tire_slip_angle_rr", packet.tire_slip_angle_rr)
        _add_field(p, "tire_combined_slip_fl", packet.tire_combined_slip_fl)
        _add_field(p, "tire_combined_slip_fr", packet.tire_combined_slip_fr)
        _add_field(p, "tire_combined_slip_rl", packet.tire_combined_slip_rl)
        _add_field(p, "tire_combined_slip_rr", packet.tire_combined_slip_rr)

        # ── Suspension ───────────────────────────────────────────────────────
        _add_field(p, "norm_susp_fl", packet.norm_susp_fl)
        _add_field(p, "norm_susp_fr", packet.norm_susp_fr)
        _add_field(p, "norm_susp_rl", packet.norm_susp_rl)
        _add_field(p, "norm_susp_rr", packet.norm_susp_rr)
        _add_field(p, "susp_travel_m_fl", packet.susp_travel_m_fl)
        _add_field(p, "susp_travel_m_fr", packet.susp_travel_m_fr)
        _add_field(p, "susp_travel_m_rl", packet.susp_travel_m_rl)
        _add_field(p, "susp_travel_m_rr", packet.susp_travel_m_rr)

        # ── Wheels ───────────────────────────────────────────────────────────
        _add_field(p, "wheel_rot_speed_fl", packet.wheel_rot_speed_fl)
        _add_field(p, "wheel_rot_speed_fr", packet.wheel_rot_speed_fr)
        _add_field(p, "wheel_rot_speed_rl", packet.wheel_rot_speed_rl)
        _add_field(p, "wheel_rot_speed_rr", packet.wheel_rot_speed_rr)

        # ── Driver inputs (dash packets only) ────────────────────────────────
        if packet.has_dash:
            p.field("accel_input", packet.accel_input)
            p.field("brake_input", packet.brake_input)
            p.field("clutch_input", packet.clutch_input)
            p.field("handbrake_input", packet.handbrake_input)
            p.field("gear", packet.gear)
            p.field("steer", packet.steer)
            p.field("throttle_pct", packet.throttle_pct)
            p.field("brake_pct", packet.brake_pct)
            p.field("steer_norm", packet.steer_norm)

        # ── Race state ───────────────────────────────────────────────────────
        p.field("lap_number", packet.lap_number)
        p.field("race_position", packet.race_position)
        _add_field(p, "current_lap_time", packet.current_lap)
        _add_field(p, "current_race_time", packet.current_race_time)
        _add_field(p, "best_lap", packet.best_lap)
        _add_field(p, "last_lap", packet.last_lap)

        try:
            self._write_api.write(bucket=self._bucket, org=self._org, record=p)
        except Exception as exc:  # noqa: BLE001
            log.warning("Failed to write telemetry point: %s", exc)

    def write_lap(
        self,
        session_id: str,
        car_ordinal: str,
        car_class: str,
        lap_number: int,
        lap_time: float,
        best_lap: float,
        max_speed_kmh: float,
        distance_traveled: float,
        estimated: bool = False,
        dirty: bool = False,
        max_g_lat: float = 0.0,
        max_g_lon: float = 0.0,
        avg_throttle: float = 0.0,
        avg_brake: float = 0.0,
        drivetrain: str = "",
    ) -> None:
        """Write a completed-lap record to the laps measurement.

        estimated=True  — lap time inferred from current_lap timer (final lap)
        dirty=True      — a rewind was detected during this lap
        """
        p = (
            Point("laps")
            .tag("session_id", session_id)
            .tag("car_ordinal", car_ordinal)
            .tag("car_class", car_class)
            .tag("drivetrain", drivetrain)
            .tag("estimated", "true" if estimated else "false")
            .tag("dirty", "true" if dirty else "false")
            .field("lap_number", lap_number)
            .field("lap_time", lap_time)
            .field("best_lap", best_lap)
            .field("max_speed_kmh", max_speed_kmh)
            .field("distance_traveled", distance_traveled)
            .field("max_g_lat", max_g_lat)
            .field("max_g_lon", max_g_lon)
            .field("avg_throttle", avg_throttle)
            .field("avg_brake", avg_brake)
        )
        try:
            self._write_api.write(bucket=self._bucket, org=self._org, record=p)
            flags = []
            if estimated:
                flags.append("estimated")
            if dirty:
                flags.append("dirty")
            flag_str = f" ({', '.join(flags)})" if flags else ""
            log.info(
                "Lap %d complete%s — %.3f s (best: %.3f s)  maxG lat=%.2f lon=%.2f  avg thr=%.0f%% brk=%.0f%%",
                lap_number, flag_str, lap_time, best_lap,
                max_g_lat, max_g_lon, avg_throttle * 100, avg_brake * 100,
            )
        except Exception as exc:  # noqa: BLE001
            log.warning("Failed to write lap record: %s", exc)


@dataclass
class LapTracker:
    """Tracks session identity and detects lap completions."""

    session_id: str = ""
    is_active: bool = False
    prev_lap_number: int = 0
    prev_is_race_on: int = 0
    max_speed_kmh: float = 0.0

    # Cached from the most recent packet where race_on was true, so the
    # final-lap estimate below (see "Session end") isn't relying on whatever
    # is_race_on == 0 transitional packets happen to report in those fields.
    last_current_lap_time: float = 0.0
    last_distance_traveled: float = 0.0
    last_best_lap: float = -1.0
    last_car_ordinal: int = 0
    last_car_class: str = ""
    last_drivetrain: str = ""

    # Per-lap aggregates — reset at session start and after each lap write
    max_g_lat: float = 0.0        # peak |lateral G| this lap
    max_g_lon: float = 0.0        # peak |longitudinal G| this lap (braking + accel)
    throttle_sum: float = 0.0
    brake_sum: float = 0.0
    input_count: int = 0
    dirty: bool = False            # True if a rewind was detected this lap
    last_race_time: float = 0.0   # previous packet's current_race_time (for rewind check)

    def process(self, packet: ForzaPacket, writer: TelemetryWriter) -> str:
        """Update state, fire lap/session events, return current session_id.

        Returns the current session_id string so the caller can tag the
        telemetry write without re-deriving it.
        """
        race_on = bool(packet.is_race_on)
        was_active = self.is_active
        completed_normally = False

        # ── Session start ────────────────────────────────────────────────────
        if race_on and not self.is_active:
            self.session_id = str(uuid.uuid4())[:8]
            self.is_active = True
            was_active = True
            self.prev_lap_number = packet.lap_number
            self.max_speed_kmh = 0.0
            self.last_current_lap_time = 0.0
            self.max_g_lat = 0.0
            self.max_g_lon = 0.0
            self.throttle_sum = 0.0
            self.brake_sum = 0.0
            self.input_count = 0
            self.dirty = False
            self.last_race_time = 0.0
            log.info(
                "Session started — id=%s  car=%s (%s, PI %d)",
                self.session_id,
                packet.car_ordinal,
                packet.car_class_name,
                packet.car_performance_index,
            )

        self.prev_is_race_on = int(race_on)

        if not was_active:
            return self.session_id

        # ── Track max speed for lap aggregate ─────────────────────────────────
        if packet.speed_kmh > self.max_speed_kmh:
            self.max_speed_kmh = packet.speed_kmh

        if race_on and packet.has_dash:
            self.last_current_lap_time = packet.current_lap
            self.last_distance_traveled = packet.distance_traveled
            self.last_best_lap = packet.best_lap
            self.last_car_ordinal = packet.car_ordinal
            self.last_car_class = packet.car_class_name
            self.last_drivetrain = packet.drivetrain_name
            # Per-lap G-force peaks
            self.max_g_lat = max(self.max_g_lat, abs(packet.g_lateral))
            self.max_g_lon = max(self.max_g_lon, abs(packet.g_longitudinal))
            # Per-lap average inputs
            self.throttle_sum += packet.throttle_pct
            self.brake_sum += packet.brake_pct
            self.input_count += 1
            # Rewind detection: race_time jumping backward > 2 s means a rewind
            if self.last_race_time > 1.0 and packet.current_race_time < self.last_race_time - 2.0:
                self.dirty = True
                log.info("Rewind detected — session %s lap %d marked dirty", self.session_id, self.prev_lap_number)
            self.last_race_time = packet.current_race_time

        # ── Lap completion ────────────────────────────────────────────────────
        # Checked before the session-end branch below: Forza's very last
        # in-race packet can carry both the completed final lap AND
        # is_race_on == 0 in the same packet (crossing the finish line ends
        # the race). Marking the session inactive first would silently drop
        # that final lap.
        if (
            packet.has_dash
            and packet.lap_number > self.prev_lap_number
            and packet.last_lap > 0.5  # guard against spurious 0 at session start
        ):
            _avg_thr = self.throttle_sum / self.input_count if self.input_count > 0 else 0.0
            _avg_brk = self.brake_sum / self.input_count if self.input_count > 0 else 0.0
            writer.write_lap(
                session_id=self.session_id,
                car_ordinal=str(packet.car_ordinal),
                car_class=packet.car_class_name,
                drivetrain=packet.drivetrain_name,
                lap_number=self.prev_lap_number,
                lap_time=packet.last_lap,
                best_lap=packet.best_lap,
                max_speed_kmh=self.max_speed_kmh,
                distance_traveled=packet.distance_traveled,
                max_g_lat=self.max_g_lat,
                max_g_lon=self.max_g_lon,
                avg_throttle=_avg_thr,
                avg_brake=_avg_brk,
                dirty=self.dirty,
            )
            # Reset per-lap accumulators for the next lap
            self.max_speed_kmh = 0.0
            self.max_g_lat = 0.0
            self.max_g_lon = 0.0
            self.throttle_sum = 0.0
            self.brake_sum = 0.0
            self.input_count = 0
            self.dirty = False
            self.prev_lap_number = packet.lap_number
            self.last_current_lap_time = 0.0
            completed_normally = True

        # ── Session end ───────────────────────────────────────────────────────
        if not race_on and self.is_active:
            self.is_active = False
            log.info("Session ended — id=%s", self.session_id)

            # Forza appears to stop sending telemetry the instant you cross
            # the line on a race's final lap — no packet ever arrives with
            # that lap's LapNumber/LastLap update. current_lap is a live
            # running timer for the lap in progress, so its last known value
            # is a good stand-in for the real (unreported) final lap time.
            if not completed_normally and self.last_current_lap_time > 5.0:
                _avg_thr = self.throttle_sum / self.input_count if self.input_count > 0 else 0.0
                _avg_brk = self.brake_sum / self.input_count if self.input_count > 0 else 0.0
                writer.write_lap(
                    session_id=self.session_id,
                    car_ordinal=str(self.last_car_ordinal),
                    car_class=self.last_car_class,
                    drivetrain=self.last_drivetrain,
                    lap_number=self.prev_lap_number,
                    lap_time=self.last_current_lap_time,
                    best_lap=min(self.last_best_lap, self.last_current_lap_time)
                    if self.last_best_lap > 0
                    else self.last_current_lap_time,
                    max_speed_kmh=self.max_speed_kmh,
                    distance_traveled=self.last_distance_traveled,
                    max_g_lat=self.max_g_lat,
                    max_g_lon=self.max_g_lon,
                    avg_throttle=_avg_thr,
                    avg_brake=_avg_brk,
                    dirty=self.dirty,
                    estimated=True,
                )

        return self.session_id
