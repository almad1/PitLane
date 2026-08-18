"""
PitLane custom dashboard — FastAPI backend.

Live telemetry path (low-latency, ~30 fps):
  collector writes /relay/latest.json  →  dashboard reads it  →  SSE → browser

Historical path:
  GET /api/laps  — queries InfluxDB for completed lap records
"""

import asyncio
import json
import logging
import math
import os
import time
from contextlib import asynccontextmanager
from typing import Any

from fastapi import FastAPI, Request
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from influxdb_client import InfluxDBClient

log = logging.getLogger("dashboard")
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s — %(message)s")

INFLUX_URL    = os.environ.get("INFLUXDB_URL",    "http://influxdb:8086")
INFLUX_TOKEN  = os.environ.get("INFLUXDB_TOKEN",  "")
INFLUX_ORG    = os.environ.get("INFLUXDB_ORG",    "pitlane")
INFLUX_BUCKET = os.environ.get("INFLUXDB_BUCKET", "forza")
RELAY_FILE    = os.environ.get("RELAY_FILE", "/relay/latest.json")
LAYOUT_FILE   = os.environ.get("LAYOUT_FILE", "/data/layout.json")

STALE_SECS = 5.0

_VERSION_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "VERSION")

def _read_version() -> str:
    try:
        with open(_VERSION_FILE) as f:
            return f.read().strip()
    except OSError:
        return "unknown"

# ── In-memory live state ───────────────────────────────────────────────────────
_telemetry: dict = {}
_last_recv: float = 0.0


async def _relay_reader():
    """Poll the shared relay file at ~30 Hz and update in-memory state."""
    global _telemetry, _last_recv
    while True:
        try:
            with open(RELAY_FILE) as f:
                _telemetry = json.load(f)
            _last_recv = time.monotonic()
        except (FileNotFoundError, json.JSONDecodeError):
            pass
        except Exception as exc:
            log.debug("Relay read error: %s", exc)
        await asyncio.sleep(0.033)


@asynccontextmanager
async def lifespan(app: FastAPI):
    asyncio.create_task(_relay_reader())
    log.info("Relay file reader started: %s", RELAY_FILE)
    yield


app = FastAPI(title="PitLane Dashboard", lifespan=lifespan)

# ── SSE live stream ────────────────────────────────────────────────────────────

@app.get("/api/live/stream")
async def live_stream(request: Request):
    """Server-Sent Events: pushes latest telemetry at ~30 fps."""
    async def generate():
        while True:
            if await request.is_disconnected():
                break
            stale = (time.monotonic() - _last_recv) > STALE_SECS
            payload = {"is_live": False} if stale else {**_telemetry, "is_live": True}
            yield f"data: {json.dumps(payload)}\n\n"
            await asyncio.sleep(0.033)

    return StreamingResponse(
        generate(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )

# ── Snapshot endpoint ──────────────────────────────────────────────────────────

@app.get("/api/live")
async def live_snapshot():
    stale = (time.monotonic() - _last_recv) > STALE_SECS
    if stale:
        return JSONResponse({"is_live": False})
    return JSONResponse({**_telemetry, "is_live": True})

# ── Lap history ────────────────────────────────────────────────────────────────

def _query(flux: str) -> list[dict[str, Any]]:
    with InfluxDBClient(url=INFLUX_URL, token=INFLUX_TOKEN, org=INFLUX_ORG) as client:
        tables = client.query_api().query(flux)
        return [record.values for table in tables for record in table.records]


def _fetch_laps(limit: int) -> list:
    flux = f"""
from(bucket: "{INFLUX_BUCKET}")
  |> range(start: -30d)
  |> filter(fn: (r) => r._measurement == "laps")
  |> filter(fn: (r) => r._field == "lap_time" or r._field == "lap_number" or r._field == "max_speed_kmh")
  |> pivot(rowKey: ["_time"], columnKey: ["_field"], valueColumn: "_value")
  |> sort(columns: ["_time"], desc: true)
  |> limit(n: {limit})
"""
    rows = _query(flux)
    return [
        {
            "session_id":    row.get("session_id", ""),
            "lap_number":    row.get("lap_number"),
            "lap_time":      row.get("lap_time"),
            "max_speed_kmh": row.get("max_speed_kmh"),
            "estimated":     row.get("estimated", "false"),
            "dirty":         row.get("dirty", "false"),
        }
        for row in rows
    ]


@app.get("/api/sessions")
async def sessions_list():
    """Aggregate all laps into per-session summaries, newest first."""
    def _fetch():
        flux = f"""
from(bucket: "{INFLUX_BUCKET}")
  |> range(start: -365d)
  |> filter(fn: (r) => r._measurement == "laps")
  |> pivot(rowKey: ["_time"], columnKey: ["_field"], valueColumn: "_value")
  |> sort(columns: ["_time"])
"""
        rows = _query(flux)
        sessions: dict = {}
        for row in rows:
            sid = row.get("session_id", "")
            if not sid:
                continue
            if sid not in sessions:
                sessions[sid] = {
                    "session_id":   sid,
                    "car_ordinal":  row.get("car_ordinal", ""),
                    "car_class":    row.get("car_class", "?"),
                    "drivetrain":   row.get("drivetrain", ""),
                    "started":      str(row.get("_time", "")),
                    "ended":        str(row.get("_time", "")),
                    "lap_times":    [],
                    "speeds":       [],
                    "g_lats":       [],
                    "g_lons":       [],
                    "max_distance": 0.0,
                }
            s = sessions[sid]
            if row.get("drivetrain"):
                s["drivetrain"] = row["drivetrain"]
            s["ended"] = str(row.get("_time", ""))
            lt = row.get("lap_time")
            if lt and float(lt) > 0:
                s["lap_times"].append(float(lt))
            spd = row.get("max_speed_kmh")
            if spd and float(spd) > 0:
                s["speeds"].append(float(spd))
            gl = row.get("max_g_lat")
            if gl:
                s["g_lats"].append(float(gl))
            gn = row.get("max_g_lon")
            if gn:
                s["g_lons"].append(float(gn))
            dt = row.get("distance_traveled")
            if dt:
                s["max_distance"] = max(s["max_distance"], float(dt))

        result = []
        for s in sessions.values():
            valid = [t for t in s["lap_times"] if t > 0]
            result.append({
                "session_id":  s["session_id"],
                "car_class":   s["car_class"],
                "car_ordinal": s["car_ordinal"],
                "drivetrain":  s["drivetrain"],
                "started":     s["started"],
                "ended":       s["ended"],
                "lap_count":   len(s["lap_times"]),
                "best_lap":    min(valid) if valid else None,
                "avg_lap":     sum(valid) / len(valid) if valid else None,
                "top_speed":   max(s["speeds"]) if s["speeds"] else None,
                "max_g":       max(max(s["g_lats"], default=0), max(s["g_lons"], default=0)),
                "lap_times":         s["lap_times"],   # for sparkline
                "total_distance_km": round(s["max_distance"] / 1000, 2),
            })
        result.sort(key=lambda s: s["ended"], reverse=True)
        return result

    try:
        return JSONResponse(await run_in_threadpool(_fetch))
    except Exception as exc:
        log.warning("Sessions query failed: %s", exc)
        return JSONResponse([])


@app.get("/api/sessions/{session_id}/laps")
async def session_laps(session_id: str):
    """All laps for a single session, in lap order."""
    def _fetch():
        flux = f"""
from(bucket: "{INFLUX_BUCKET}")
  |> range(start: -365d)
  |> filter(fn: (r) => r._measurement == "laps")
  |> filter(fn: (r) => r.session_id == "{session_id}")
  |> pivot(rowKey: ["_time"], columnKey: ["_field"], valueColumn: "_value")
  |> sort(columns: ["_time"])
"""
        rows = _query(flux)
        return [
            {
                "lap_number":       int(row.get("lap_number") or 0),
                "lap_time":         row.get("lap_time"),
                "max_speed_kmh":    row.get("max_speed_kmh"),
                "max_g_lat":        row.get("max_g_lat"),
                "max_g_lon":        row.get("max_g_lon"),
                "avg_throttle":     row.get("avg_throttle"),
                "avg_brake":        row.get("avg_brake"),
                "distance_m":       row.get("distance_traveled"),
                "estimated":        row.get("estimated", "false"),
                "dirty":            row.get("dirty", "false"),
            }
            for row in rows
        ]

    try:
        return JSONResponse(await run_in_threadpool(_fetch))
    except Exception as exc:
        log.warning("Session laps query failed: %s", exc)
        return JSONResponse([])


# ── Telemetry analysis helpers ─────────────────────────────────────────────────

def _lerp(xs: list, ys: list, x: float) -> float:
    """Linear interpolation at x given sorted xs and corresponding ys."""
    if not xs:
        return 0.0
    if x <= xs[0]:
        return ys[0]
    if x >= xs[-1]:
        return ys[-1]
    lo, hi = 0, len(xs) - 1
    while lo < hi - 1:
        mid = (lo + hi) // 2
        if xs[mid] <= x:
            lo = mid
        else:
            hi = mid
    span = xs[hi] - xs[lo]
    if span == 0:
        return ys[lo]
    return ys[lo] + (x - xs[lo]) / span * (ys[hi] - ys[lo])


def _detect_contacts(points: list) -> list:
    """Detect wall/car contacts from G-spike analysis.

    Looks for lateral+longitudinal G above 4 G with tyre contact (combined slip > 0.05)
    or any spike above 6 G (hard impact, tyres may briefly leave ground on landing).
    Nearby spikes are merged into a single event (keeps the peak).
    """
    raw = []
    for i, pt in enumerate(points):
        g_total = math.sqrt(pt["gL"] ** 2 + pt["gN"] ** 2)
        if g_total > 4.0:
            avg_slip = (pt.get("sl0", 1.0) + pt.get("sl1", 1.0)
                        + pt.get("sl2", 1.0) + pt.get("sl3", 1.0)) / 4
            if avg_slip > 0.05 or g_total > 6.0:
                raw.append({"idx": i, "g": round(g_total, 2)})

    merged: list = []
    for c in raw:
        if merged and c["idx"] - merged[-1]["idx"] < 5:
            if c["g"] > merged[-1]["g"]:
                merged[-1] = c
        else:
            merged.append(c)
    return merged


def _detect_jumps(points: list) -> list:
    """Detect airborne intervals from tyre combined-slip analysis.

    All four tyres must have combined slip < 0.05 (free-spinning / no road load)
    for at least 2 consecutive sampled points (~0.3 s at 6 Hz) to count.
    """
    jumps: list = []
    air_start: int | None = None
    for i, pt in enumerate(points):
        airborne = all(
            pt.get(f"sl{k}", 1.0) < 0.05 for k in range(4)
        )
        if airborne and air_start is None:
            air_start = i
        elif not airborne and air_start is not None:
            if i - air_start >= 2:
                jumps.append({"start_idx": air_start, "end_idx": i - 1})
            air_start = None
    return jumps


@app.get("/api/sessions/{session_id}/track")
async def session_track(session_id: str):
    """Return ~6 Hz downsampled telemetry for the session with event detection."""

    def _fetch():
        fields = [
            "pos_x", "pos_y", "pos_z", "speed_kmh",
            "g_lateral", "g_longitudinal", "throttle_pct", "brake_pct", "steer_norm",
            "lap_number",
            "tire_combined_slip_fl", "tire_combined_slip_fr",
            "tire_combined_slip_rl", "tire_combined_slip_rr",
            "vel_y", "distance_traveled",
        ]
        field_filter = " or ".join(f'r._field == "{f}"' for f in fields)

        flux = f"""
from(bucket: "{INFLUX_BUCKET}")
  |> range(start: -365d)
  |> filter(fn: (r) => r._measurement == "telemetry")
  |> filter(fn: (r) => r.session_id == "{session_id}")
  |> filter(fn: (r) => {field_filter})
  |> pivot(rowKey: ["_time"], columnKey: ["_field"], valueColumn: "_value")
  |> filter(fn: (r) => exists r.pos_x and exists r.pos_z)
  |> sort(columns: ["_time"])
"""
        rows = _query(flux)
        # Downsample ~6Hz (Flux sample() requires _value column absent after pivot)
        if len(rows) > 200:
            rows = rows[::10]

        def fv(row, key, default=0.0):
            v = row.get(key)
            return float(v) if v is not None else default

        points = []
        for row in rows:
            x, z = fv(row, "pos_x"), fv(row, "pos_z")
            if x == 0.0 and z == 0.0:
                continue
            points.append({
                "x":   x,
                "y":   fv(row, "pos_y"),
                "z":   z,
                "spd": fv(row, "speed_kmh"),
                "thr": fv(row, "throttle_pct"),
                "brk": fv(row, "brake_pct"),
                "str": fv(row, "steer_norm"),
                "gL":  fv(row, "g_lateral"),
                "gN":  fv(row, "g_longitudinal"),
                "sl0": fv(row, "tire_combined_slip_fl", 1.0),
                "sl1": fv(row, "tire_combined_slip_fr", 1.0),
                "sl2": fv(row, "tire_combined_slip_rl", 1.0),
                "sl3": fv(row, "tire_combined_slip_rr", 1.0),
                "vy":  fv(row, "vel_y"),
                "lap": int(fv(row, "lap_number", 0)),
                "dist": fv(row, "distance_traveled"),
            })

        if not points:
            return {"points": [], "contacts": [], "jumps": [], "bounds": {}, "laps": []}

        xs = [p["x"] for p in points]
        ys = [p["y"] for p in points]
        zs = [p["z"] for p in points]
        bounds = {
            "minX": min(xs), "maxX": max(xs),
            "minY": min(ys), "maxY": max(ys),
            "minZ": min(zs), "maxZ": max(zs),
        }

        contacts = _detect_contacts(points)
        jumps    = _detect_jumps(points)

        lap_groups: dict[int, list] = {}
        for i, pt in enumerate(points):
            lap_groups.setdefault(pt["lap"], []).append(i)

        laps_sorted = sorted(lap_groups.keys())

        return {
            "points":     points,
            "contacts":   contacts,
            "jumps":      jumps,
            "bounds":     bounds,
            "laps":       laps_sorted,
            "lap_ranges": {str(k): [v[0], v[-1]] for k, v in lap_groups.items()},
        }

    try:
        return JSONResponse(await run_in_threadpool(_fetch))
    except Exception as exc:
        log.warning("Track query failed: %s", exc)
        return JSONResponse({"points": [], "contacts": [], "jumps": [], "bounds": {}, "laps": []})


@app.get("/api/sessions/{session_id}/compare")
async def session_compare(session_id: str, laps: str = ""):
    """Return distance-aligned telemetry for lap comparison (up to 5 laps)."""
    if not laps:
        return JSONResponse([])

    try:
        lap_numbers = [int(x.strip()) for x in laps.split(",") if x.strip()]
    except ValueError:
        return JSONResponse([])

    lap_numbers = lap_numbers[:5]

    def _fetch():
        fields = [
            "speed_kmh", "throttle_pct", "brake_pct", "steer_norm",
            "distance_traveled", "lap_number", "pos_x", "pos_z", "pos_y",
        ]
        field_filter = " or ".join(f'r._field == "{f}"' for f in fields)
        results = []

        for lap in lap_numbers:
            flux = f"""
from(bucket: "{INFLUX_BUCKET}")
  |> range(start: -365d)
  |> filter(fn: (r) => r._measurement == "telemetry")
  |> filter(fn: (r) => r.session_id == "{session_id}")
  |> filter(fn: (r) => {field_filter})
  |> pivot(rowKey: ["_time"], columnKey: ["_field"], valueColumn: "_value")
  |> filter(fn: (r) => r.lap_number == {lap} and exists r.pos_x and exists r.distance_traveled)
  |> sort(columns: ["_time"])
"""
            rows = _query(flux)
            if not rows:
                continue

            dists, speeds, thrs, brks, strs = [], [], [], [], []
            for row in rows:
                d = row.get("distance_traveled")
                if d is None:
                    continue
                dists.append(float(d))
                speeds.append(float(row.get("speed_kmh") or 0))
                thrs.append(float(row.get("throttle_pct") or 0))
                brks.append(float(row.get("brake_pct") or 0))
                strs.append(float(row.get("steer_norm") or 0))

            if not dists:
                continue

            start_d = min(dists)
            dists = [d - start_d for d in dists]
            max_d = max(dists) or 1.0

            N = 300
            data = []
            for i in range(N):
                sd = i * max_d / (N - 1) if N > 1 else 0.0
                data.append({
                    "d":   round(sd, 1),
                    "spd": round(_lerp(dists, speeds, sd), 1),
                    "thr": round(_lerp(dists, thrs, sd), 1),
                    "brk": round(_lerp(dists, brks, sd), 1),
                    "str": round(_lerp(dists, strs, sd), 3),
                })

            results.append({"lap": lap, "max_dist": round(max_d, 1), "data": data})

        return results

    try:
        return JSONResponse(await run_in_threadpool(_fetch))
    except Exception as exc:
        log.warning("Compare query failed: %s", exc)
        return JSONResponse([])


@app.get("/api/version")
async def version():
    return JSONResponse({"version": _read_version()})


# ── Dashboard layout persistence ──────────────────────────────────────────────
# Stored server-side so a customised layout follows the user to any browser or
# machine. The browser also keeps a localStorage copy for instant first paint.

@app.get("/api/layout")
async def get_layout():
    def _read():
        try:
            with open(LAYOUT_FILE) as f:
                return json.load(f)
        except (OSError, ValueError):
            return {}
    return JSONResponse(await run_in_threadpool(_read))


@app.post("/api/layout")
async def save_layout(request: Request):
    try:
        data = await request.json()
    except ValueError:
        return JSONResponse({"ok": False, "error": "invalid JSON"}, status_code=400)

    if not isinstance(data, dict) or "layout" not in data:
        return JSONResponse({"ok": False, "error": "expected {layout, hidden}"}, status_code=400)

    def _write():
        os.makedirs(os.path.dirname(LAYOUT_FILE), exist_ok=True)
        tmp = LAYOUT_FILE + ".tmp"
        with open(tmp, "w") as f:
            json.dump(data, f)
        os.replace(tmp, LAYOUT_FILE)   # atomic — never leaves a half-written file

    try:
        await run_in_threadpool(_write)
    except OSError as exc:
        log.warning("Layout save failed: %s", exc)
        return JSONResponse({"ok": False, "error": str(exc)}, status_code=500)
    return JSONResponse({"ok": True})


@app.delete("/api/layout")
async def reset_layout():
    def _rm():
        try:
            os.remove(LAYOUT_FILE)
        except OSError:
            pass
    await run_in_threadpool(_rm)
    return JSONResponse({"ok": True})


@app.get("/api/analysis/telemetry")
async def analysis_telemetry(session_id: str = "", minutes: int = 5):
    """Return ~10 Hz time-series telemetry for the driver-inputs / speed-engine / G-force charts.

    If session_id is given, returns that session's full telemetry (downsampled).
    Otherwise returns the most recent `minutes` of data across all sessions.
    """
    def _fetch():
        fields = [
            "throttle_pct", "brake_pct", "clutch_pct",
            "speed_kmh", "engine_rpm", "engine_max_rpm",
            "g_lateral", "g_longitudinal",
        ]
        field_filter = " or ".join(f'r._field == "{f}"' for f in fields)

        if session_id:
            sid_filter = f'|> filter(fn: (r) => r.session_id == "{session_id}")'
            range_str  = "start: -365d"
        else:
            sid_filter = ""
            range_str  = f"start: -{minutes}m"

        flux = f"""
from(bucket: "{INFLUX_BUCKET}")
  |> range({range_str})
  |> filter(fn: (r) => r._measurement == "telemetry")
  {sid_filter}
  |> filter(fn: (r) => {field_filter})
  |> pivot(rowKey: ["_time"], columnKey: ["_field"], valueColumn: "_value")
  |> sort(columns: ["_time"])
"""
        rows = _query(flux)

        # Thin to ≤1200 points (~2 min at 10 Hz)
        step = max(1, len(rows) // 1200)
        rows = rows[::step]

        def fv(row, key, default=0.0):
            v = row.get(key)
            return float(v) if v is not None else default

        points = []
        t0 = None
        for row in rows:
            ts = row.get("_time")
            if ts is None:
                continue
            t_s = ts.timestamp() if hasattr(ts, "timestamp") else float(str(ts)[:10])
            if t0 is None:
                t0 = t_s
            points.append({
                "t":   round(t_s - t0, 2),
                "thr": round(fv(row, "throttle_pct"), 1),
                "brk": round(fv(row, "brake_pct"), 1),
                "clt": round(fv(row, "clutch_pct"), 1),
                "spd": round(fv(row, "speed_kmh"), 1),
                "rpm": round(fv(row, "engine_rpm")),
                "mrpm": round(fv(row, "engine_max_rpm")),
                "gL":  round(fv(row, "g_lateral"), 3),
                "gN":  round(fv(row, "g_longitudinal"), 3),
            })
        return {"points": points, "duration": round(points[-1]["t"], 1) if points else 0}

    try:
        return JSONResponse(await run_in_threadpool(_fetch))
    except Exception as exc:
        log.warning("Analysis telemetry query failed: %s", exc)
        return JSONResponse({"points": [], "duration": 0})


@app.get("/api/laps")
async def laps(limit: int = 20):
    try:
        return JSONResponse(await run_in_threadpool(_fetch_laps, limit))
    except Exception as exc:
        log.warning("Laps query failed: %s", exc)
        return JSONResponse([])

# ── Static ─────────────────────────────────────────────────────────────────────
app.mount("/", StaticFiles(directory="static", html=True), name="static")
