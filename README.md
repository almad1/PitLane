# 🏎️ PitLane — Forza Horizon Telemetry

A self-contained Docker stack for recording and visualising Forza Horizon telemetry in real time.

```
┌─────────────────────────────────────────────────────────┐
│  Forza (Xbox/PC)  ──UDP──►  collector  ──►  InfluxDB    │
│                                                          │
│  Grafana  ◄──Flux queries──  InfluxDB                   │
└─────────────────────────────────────────────────────────┘
```

| Service      | Port          | Purpose                                 |
|-------------|---------------|------------------------------------------|
| `collector` | **5300/UDP**  | Receives Forza Data Out packets          |
| `influxdb`  | 8086          | Time-series storage (internal + browser) |
| `grafana`   | **3000**      | Dashboard (open in your browser)         |

---

## Quick Start

### 1. Clone & configure

```bash
cp .env.example .env
```

Open `.env` and change at minimum the `INFLUXDB_TOKEN` to a long random string.
All other defaults work out of the box.

### 2. Start the stack

```bash
docker compose up --build -d
```

InfluxDB auto-initialises on the first run (takes ~20 s). The collector waits
for it with retries. Grafana becomes available at **<http://localhost:3000>**.

Default credentials: `admin` / `pitlane` (change in `.env`).

### 3. Configure Forza

In-game:
1. **Settings → HUD and Gameplay → Data Out** → **ON**
2. **Data Out IP Address** → the LAN IP of the machine running Docker
   (e.g. `192.168.1.42`; find it with `ip addr` / `ifconfig`)
3. **Data Out IP Port** → `5300` (matches `UDP_PORT` in `.env`)
4. **Data Out Packet Format** → **Car Dash**

Start a race — the collector log should immediately show heartbeat lines:

```
docker compose logs -f collector
```

### 4. Open the dashboard

Navigate to **<http://localhost:3000>** and open **🏎️ Forza Telemetry**.

Use the **Session** dropdown at the top to filter to a specific session, or
leave it on **All** to see everything in the selected time range.

---

## Dashboard Panels

| Panel | What it shows |
|-------|---------------|
| Speed | Current km/h (live, last 30 s) |
| Engine RPM | Gauge, threshold-coloured at 75 % / 90 % |
| Gear | Current gear (N = neutral) |
| Current Lap | Running lap timer |
| Best Lap | Best lap of the session |
| Fuel | Fuel level 0–100 % |
| Boost | Turbo boost (PSI) |
| Speed trace | km/h over the selected time window |
| RPM trace | RPM over the selected time window |
| Throttle & Brake | Overlaid 0–100 % input trace |
| Tire Temperatures | All four tyre temps in °F |
| Tire Combined Slip | All four tyres' combined slip values |
| Suspension Travel | Suspension travel (m) per corner |
| G-Forces | Lateral, longitudinal, and vertical G |
| Lap Times | Table of every completed lap with max speed |

---

## Architecture

```
PitLane/
├── docker-compose.yml
├── .env.example              ← copy to .env
├── collector/
│   ├── Dockerfile
│   ├── requirements.txt      ← influxdb-client only
│   ├── parser.py             ← Forza UDP packet → ForzaPacket dataclass
│   ├── writer.py             ← InfluxDB writer + LapTracker
│   └── main.py               ← UDP socket loop
└── grafana/
    └── provisioning/
        ├── datasources/influxdb.yml
        └── dashboards/
            ├── provider.yml
            └── forza-telemetry.json
```

### Packet format

The collector handles three packet sizes:

| Size | Format | Notes |
|------|--------|-------|
| 232 B | Sled (`<iI27f4i20f5i`) | Engine, motion, tyres, car info |
| 311 B | Car Dash, classic (`<iI27f4i20f5i17fH6B3b`) | Older Forza titles |
| 324 B | Car Dash, Forza Horizon 6 (`<iI27f4i20f5i3i17fH6B3b1x`) | + position, speed, temps, inputs, laps |

Forza Horizon 6 sends 13 bytes more than the classic Car Dash format: three
extra int32 fields right after `NumCylinders` (observed constant during a
session, e.g. `(38, 0, 0)` — likely a track/region ID, not currently
surfaced on the dashboard) plus one trailing padding byte. Also note that
`WheelOnRumbleStrip*` fields are `int32`, not `float32`, despite occupying
the same byte width as the surrounding floats — mistyping them doesn't shift
any offsets, but does corrupt those four fields specifically.

Set Forza to **Car Dash** format to get all data including throttle/brake and
lap timers. The sled-only format is also accepted (older game versions).

### InfluxDB measurements

**`telemetry`** — written at game rate (~60 Hz while `is_race_on == 1`).

Tags: `session_id`, `car_ordinal`, `car_class`, `drivetrain`

Key fields: `speed_kmh`, `engine_rpm`, `throttle_pct`, `brake_pct`, `gear`,
`tire_temp_fl/fr/rl/rr`, `tire_combined_slip_*`, `susp_travel_m_*`,
`g_lateral`, `g_longitudinal`, `current_lap_time`, `best_lap` …

**`laps`** — one point per completed lap.

Tags: `session_id`, `car_ordinal`, `car_class`

Fields: `lap_number`, `lap_time`, `best_lap`, `max_speed_kmh`, `distance_traveled`

---

## Useful commands

```bash
# Tail collector logs
docker compose logs -f collector

# Open InfluxDB UI (explore raw data)
open http://localhost:8086

# Reset all data (caution: wipes InfluxDB volumes)
docker compose down -v

# Rebuild collector after code changes
docker compose up --build -d collector
```

---

## Troubleshooting

**No data in Grafana** — check that:
- The collector is running: `docker compose ps`
- Forza's Data Out IP matches your machine's LAN IP (not `127.0.0.1`)
- Forza's Data Out port matches `UDP_PORT` in `.env`
- `UDP_PORT` is not blocked by your firewall
- The time range in Grafana includes "now" (use **Last 5 minutes**)

**Collector logs show "Listening..." but never a heartbeat, even though
Forza is definitely sending** — on Docker Desktop for Mac, a UDP port's
internal NAT/proxy mapping can get stuck if the host ever binds that same
port directly outside of Docker (e.g. for debugging with `nc` or a raw
socket) while the container also has it mapped. Symptoms: `docker port`
and `docker compose ps` both look correct, but no traffic ever reaches the
container. Fix: change `UDP_PORT` in `.env` to an unused port, update
Forza's Data Out port to match, then `docker compose down && docker compose
up -d`. Avoid binding the UDP port directly on the host while the stack is
running.

**"No data source found"** — restart Grafana after InfluxDB is healthy:
```bash
docker compose restart grafana
```

**Grafana shows "bucket not found"** — the `INFLUXDB_BUCKET` in `.env`
must match across all three services. The default is `forza`.

**Session filter not populated** — the template variable queries InfluxDB for
session tags. It will be empty until the first race completes. Try extending
the dashboard time range to **Last 24 hours** if sessions are missing.
