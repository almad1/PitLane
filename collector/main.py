"""
PitLane telemetry collector — entry point.

With network_mode: host the collector binds directly to the Mac's UDP port,
completely bypassing Docker Desktop's VPNKit proxy.  This means it never
loses packets on container restarts (VPNKit is not involved at all).

Live data is relayed to the dashboard by atomically writing a JSON file to
a shared tmpfs volume — no networking required for the live path.
"""

import json
import logging
import math
import os
import socket
import time

from parser import parse_packet
from writer import LapTracker, TelemetryWriter

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-8s  %(name)s — %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("pitlane")

RELAY_FILE = os.environ.get("DASHBOARD_RELAY_FILE", "")
RELAY_TMP  = RELAY_FILE + ".tmp" if RELAY_FILE else ""


def _sf(v) -> float:
    try:
        f = float(v)
        return f if math.isfinite(f) else 0.0
    except (TypeError, ValueError):
        return 0.0


def _write_relay(packet, session_id: str, race_on: bool) -> None:
    """Atomically write the latest packet as JSON to the shared relay file."""
    if not RELAY_FILE:
        return
    has_dash = getattr(packet, "has_dash", False)
    d = {
        "session_id": session_id,
        "race_on": 1 if race_on else 0,
        "speed_kmh":         _sf(packet.speed_kmh),
        "engine_rpm":        _sf(packet.engine_rpm),
        "engine_max_rpm":    _sf(packet.engine_max_rpm),
        "gear":              int(getattr(packet, "gear", 0) or 0),
        "throttle_pct":      _sf(packet.throttle_pct) if has_dash else 0.0,
        "brake_pct":         _sf(packet.brake_pct)    if has_dash else 0.0,
        "g_lateral":         _sf(packet.g_lateral),
        "g_longitudinal":    _sf(packet.g_longitudinal),
        "tire_temp_fl":      _sf(packet.tire_temp_fl),
        "tire_temp_fr":      _sf(packet.tire_temp_fr),
        "tire_temp_rl":      _sf(packet.tire_temp_rl),
        "tire_temp_rr":      _sf(packet.tire_temp_rr),
        "tire_combined_slip_fl": _sf(packet.tire_combined_slip_fl),
        "tire_combined_slip_fr": _sf(packet.tire_combined_slip_fr),
        "tire_combined_slip_rl": _sf(packet.tire_combined_slip_rl),
        "tire_combined_slip_rr": _sf(packet.tire_combined_slip_rr),
        "boost":             _sf(packet.boost),
        "fuel":              _sf(packet.fuel),
        "current_lap_time":  _sf(packet.current_lap),
        "best_lap":          _sf(packet.best_lap),
        "last_lap":          _sf(packet.last_lap),
        "lap_number":        int(getattr(packet, "lap_number", 0) or 0),
        "car_class":         getattr(packet, "car_class_name", "?"),
        "car_ordinal":       str(getattr(packet, "car_ordinal", "")),
        "drivetrain":        getattr(packet, "drivetrain_name", "?"),
        # Position + heading (for live track map)
        "pos_x":  _sf(packet.pos_x)  if has_dash else 0.0,
        "pos_z":  _sf(packet.pos_z)  if has_dash else 0.0,
        "pos_y":  _sf(packet.pos_y)  if has_dash else 0.0,
        # Suspension (dashboard tyre-grip tile)
        "norm_susp_fl": _sf(packet.norm_susp_fl),
        "norm_susp_fr": _sf(packet.norm_susp_fr),
        "norm_susp_rl": _sf(packet.norm_susp_rl),
        "norm_susp_rr": _sf(packet.norm_susp_rr),
        # ── Extended fields ──────────────────────────────────────────────────────
        # (yaw/pitch/roll, slip angles, race_time, clutch and handbrake were
        # dropped from the relay: shipped at 30 Hz, rendered by nothing. They
        # are still written to InfluxDB for the analysis raw table.)
        "race_position":      int(getattr(packet, "race_position",    0) or 0) if has_dash else 0,
        "power_kw":           _sf(packet.power)  / 1000.0                      if has_dash else 0.0,
        "torque_nm":          _sf(packet.torque)                               if has_dash else 0.0,
        "distance_m":         _sf(packet.distance_traveled)                    if has_dash else 0.0,
        "steer":              _sf(packet.steer_norm)                           if has_dash else 0.0,
        "susp_mm_fl":         _sf(packet.susp_travel_m_fl) * 1000.0,
        "susp_mm_fr":         _sf(packet.susp_travel_m_fr) * 1000.0,
        "susp_mm_rl":         _sf(packet.susp_travel_m_rl) * 1000.0,
        "susp_mm_rr":         _sf(packet.susp_travel_m_rr) * 1000.0,
        "tire_slip_ratio_fl": _sf(packet.tire_slip_ratio_fl),
        "tire_slip_ratio_fr": _sf(packet.tire_slip_ratio_fr),
        "tire_slip_ratio_rl": _sf(packet.tire_slip_ratio_rl),
        "tire_slip_ratio_rr": _sf(packet.tire_slip_ratio_rr),
        "wheel_rpm_fl":       abs(_sf(packet.wheel_rot_speed_fl)) * 60.0 / (2 * math.pi),
        "wheel_rpm_fr":       abs(_sf(packet.wheel_rot_speed_fr)) * 60.0 / (2 * math.pi),
        "wheel_rpm_rl":       abs(_sf(packet.wheel_rot_speed_rl)) * 60.0 / (2 * math.pi),
        "wheel_rpm_rr":       abs(_sf(packet.wheel_rot_speed_rr)) * 60.0 / (2 * math.pi),
        "car_pi":             int(getattr(packet, "car_performance_index", 0) or 0),
        "car_cylinders":      int(getattr(packet, "num_cylinders", 0) or 0),
    }
    try:
        with open(RELAY_TMP, "w") as f:
            json.dump(d, f, separators=(",", ":"))
        os.replace(RELAY_TMP, RELAY_FILE)  # atomic swap
    except OSError:
        pass


def main() -> None:
    udp_host = os.environ.get("UDP_HOST", "0.0.0.0")
    udp_port = int(os.environ.get("UDP_PORT", "5302"))

    writer = TelemetryWriter()
    writer.wait_for_influxdb()

    tracker = LapTracker()

    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF, 1024 * 1024)
    sock.bind((udp_host, udp_port))

    log.info("Listening on UDP %s:%d", udp_host, udp_port)
    if RELAY_FILE:
        log.info("Relay file: %s", RELAY_FILE)

    packet_count = 0
    last_menu_write = 0.0
    try:
        while True:
            data, addr = sock.recvfrom(65535)
            packet = parse_packet(data)

            if packet is None:
                log.debug("Dropped unknown packet (%d bytes) from %s", len(data), addr)
                continue

            session_id = tracker.process(packet, writer)

            if not packet.is_race_on:
                # Relay only — the dashboard needs the race_on=0 heartbeat for
                # its PAUSED badge. Writing these packets to InfluxDB tagged
                # with the *previous* session_id appended thousands of junk
                # points to the last race during a long menu idle, and no
                # analysis query filters them out.
                now = time.monotonic()
                if now - last_menu_write >= 1.0:
                    _write_relay(packet, session_id, race_on=False)
                    last_menu_write = now
                continue

            writer.write_telemetry(packet, session_id, race_on=True)
            _write_relay(packet, session_id, race_on=True)

            packet_count += 1
            if packet_count % 300 == 1:
                log.info(
                    "[%s] Lap %d | %.1f km/h | RPM %.0f | Gear %d",
                    session_id,
                    packet.lap_number,
                    packet.speed_kmh,
                    packet.engine_rpm,
                    packet.gear,
                )

    except KeyboardInterrupt:
        log.info("Shutting down …")
    finally:
        sock.close()
        writer.close()


if __name__ == "__main__":
    main()
