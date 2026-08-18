# 🏎️ PitLane — Forza Horizon Telemetry

A self-contained Docker stack for recording and analysing Forza Horizon telemetry
in real time: a live driving dashboard, lap-comparison analytics, InfluxDB
history, and Grafana.

```
┌────────────────────────────────────────────────────────────────────┐
│  Forza (Xbox/PC) ──UDP──► collector ──┬──► InfluxDB ◄── Grafana    │
│                                       │                            │
│                                       └──► relay file (tmpfs)      │
│                                                 │                  │
│                    browser ◄──SSE── dashboard ◄─┘                  │
│                            ◄──REST── (lap history via InfluxDB)    │
└────────────────────────────────────────────────────────────────────┘
```

| Service     | Port          | Purpose                                        |
|-------------|---------------|------------------------------------------------|
| `collector` | **5302/UDP**  | Receives Forza Data Out packets                |
| `dashboard` | **8080**      | **Main UI** — live dashboard + lap analytics   |
| `influxdb`  | 8086          | Time-series storage (internal + browser)       |
| `grafana`   | 3000          | Alternative dashboards                         |

The live path never touches the database: the collector atomically writes the
latest packet to a JSON file on a shared tmpfs volume, the dashboard polls it at
~30 Hz and streams it to the browser over Server-Sent Events. InfluxDB is the
historical path (lap records + full-rate telemetry for analysis).

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
for it with retries.

### 3. Configure Forza

In-game:
1. **Settings → HUD and Gameplay → Data Out** → **ON**
2. **Data Out IP Address** → the LAN IP of the machine running Docker
   (e.g. `192.168.1.42`; find it with `ip addr` / `ifconfig`)
3. **Data Out IP Port** → `5302` (matches `UDP_PORT` in `.env`)
4. **Data Out Packet Format** → **Car Dash**

Start driving — the collector log should immediately show heartbeat lines:

```bash
docker compose logs -f collector
```

### 4. Open the dashboard

**<http://localhost:8080>** — the live dashboard.

- **Editable layout**: click **EDIT LAYOUT** to drag, resize, or hide any of
  the 13 tiles (tachometer, lap times, inputs, tyre grip, friction circle,
  live track trace, lap history, suspension, wheel RPM, slip ratio, engine,
  car info, MapGenie map). The layout is saved server-side and follows you to
  any browser.
- **Analytics** (top-right, or <http://localhost:8080/analytics.html>):
  session browser, lap table, and the comparison workspace — pick up to six
  laps (A–F, cross-session), get Δ-time vs the reference lap, speed,
  throttle/brake, steering and tyre-slip charts with a synced cursor,
  drag-zoom, click-to-pin, and a racing-line map linked to the chart cursor.
  2D/3D track map with contact & jump detection: drag to pan/orbit, scroll to
  zoom.

Grafana remains available at <http://localhost:3000> (default credentials
`admin` / `pitlane`, change in `.env`).

---

## Architecture

```
PitLane/
├── docker-compose.yml
├── .env.example              ← copy to .env
├── VERSION                   ← bumped on every release
├── THIRD_PARTY.md            ← licences for adapted/vendored code
├── collector/
│   ├── parser.py             ← Forza UDP packet → ForzaPacket dataclass
│   ├── writer.py             ← InfluxDB writer + LapTracker (laps, rewinds)
│   └── main.py               ← UDP socket loop + relay-file writer
├── dashboard/
│   ├── main.py               ← FastAPI: SSE live stream + analysis API
│   └── static/
│       ├── index.html        ← live dashboard (GridStack tiles)
│       ├── analytics.html    ← lap analysis (uPlot charts)
│       └── vendor/           ← uPlot, vendored (works offline)
├── grafana/provisioning/     ← datasource + dashboard auto-provisioning
└── tests/                    ← parser, resampler, and API tests (run in CI)
```

The lap-comparison engine in `analytics.html` is adapted from
[LapScope](https://github.com/darcane/LapScope) (MIT) — see `THIRD_PARTY.md`.

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

**`telemetry`** — written at game rate (~60 Hz) while racing. Menu packets are
relayed to the dashboard (for the PAUSED badge) but not written to the DB.

Tags: `session_id`, `car_ordinal`, `car_class`, `drivetrain`

Key fields: `speed_kmh`, `engine_rpm`, `throttle_pct`, `brake_pct`, `gear`,
`tire_temp_fl/fr/rl/rr`, `tire_combined_slip_*`, `susp_travel_m_*`,
`g_lateral`, `g_longitudinal`, `current_lap_time`, `best_lap` …

**`laps`** — one point per completed lap.

Tags: `session_id`, `car_ordinal`, `car_class`, `drivetrain`, `estimated`, `dirty`

Fields: `lap_number`, `lap_time`, `best_lap`, `max_speed_kmh`,
`distance_traveled`, `max_g_lat`, `max_g_lon`, `avg_throttle`, `avg_brake`

`estimated=true` marks a race's final lap (Forza stops sending the moment you
cross the line, so its time is taken from the live lap timer). `dirty=true`
marks laps where a rewind was detected.

---

## Backups

All history lives in the `influxdb_data` Docker volume (plus the saved
dashboard layout and session groups in `dashboard_data`). To back up:

```bash
# InfluxDB → a dated backup directory on the host
docker compose exec influxdb influx backup /tmp/backup \
  --token "$INFLUXDB_TOKEN"
docker compose cp influxdb:/tmp/backup "./backups/influx-$(date +%Y%m%d)"

# Layout + groups (two small JSON files)
docker compose cp dashboard:/data "./backups/dashboard-$(date +%Y%m%d)"
```

Restore with `influx restore` (see the
[InfluxDB docs](https://docs.influxdata.com/influxdb/v2/admin/backup-restore/)).
A weekly cron/launchd job pointing at those two commands is enough — the data
only grows while you're actually driving.

---

## Development

```bash
# Run the test suite (parser, resampler, API)
pip install -r dashboard/requirements.txt -r collector/requirements.txt pytest httpx
pytest -q
```

Tests also run in GitHub Actions on every push.

`VERSION` is bumped on every release: patch for small fixes, minor for
features, major for reworks. The running version shows in the dashboard
header and at `/api/version`.

---

## Useful commands

```bash
# Tail collector logs
docker compose logs -f collector

# Open InfluxDB UI (explore raw data)
open http://localhost:8086

# Live telemetry snapshot (debugging)
curl -s http://localhost:8080/api/live | python3 -m json.tool

# Reset all data (caution: wipes InfluxDB volumes)
docker compose down -v

# Rebuild after code changes
docker compose up --build -d
```

---

## Troubleshooting

**No data on the dashboard** — check that:
- The collector is running: `docker compose ps`
- Forza's Data Out IP matches your machine's LAN IP (not `127.0.0.1`)
- Forza's Data Out port matches `UDP_PORT` in `.env` (default `5302`)
- `UDP_PORT` is not blocked by your firewall

**Collector logs show "Listening..." but never a heartbeat, even though
Forza is definitely sending** — on Docker Desktop for Mac, a UDP port's
internal NAT/proxy mapping can get stuck if the host ever binds that same
port directly outside of Docker (e.g. for debugging with `nc` or a raw
socket) while the container also has it mapped. Symptoms: `docker port`
and `docker compose ps` both look correct, but no traffic ever reaches the
container. Fix: change `UDP_PORT` in `.env` to an unused port, update
Forza's Data Out port to match, then `docker compose down && docker compose
up -d`. Avoid binding the UDP port directly on the host while the stack is
running. (The collector now runs with `network_mode: host`, which sidesteps
the proxy entirely.)

**"No data source found" in Grafana** — restart Grafana after InfluxDB is
healthy:
```bash
docker compose restart grafana
```

**Grafana shows "bucket not found"** — the `INFLUXDB_BUCKET` in `.env`
must match across all three services. The default is `forza`.

**Analytics shows no sessions** — sessions appear after the first completed
lap. Free-roam driving counts (Horizon sets its race flag while driving), but
a lap boundary is what creates the record.
