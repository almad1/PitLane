# Third-Party Notices

PitLane bundles and derives from the following third-party work. Full licence
texts live alongside the vendored files in `dashboard/static/vendor/`.

---

## uPlot

- **Version:** 1.6.31
- **Author:** Leon Sorokin
- **Licence:** MIT
- **Source:** https://github.com/leeoniya/uPlot
- **Bundled at:**
  - `dashboard/static/vendor/uplot.iife.min.js`
  - `dashboard/static/vendor/uplot.min.css`
- **Licence text:** `dashboard/static/vendor/LICENSE.uPlot`

Vendored rather than loaded from a CDN so the dashboard keeps working on a
machine with no internet access.

---

## LapScope

- **Author:** Erdem Darcan
- **Licence:** MIT
- **Source:** https://github.com/darcane/LapScope
- **Licence text:** `dashboard/static/vendor/LICENSE.LapScope`

PitLane's lap-analysis page is derived from LapScope's analysis frontend.
Specifically adapted from `app/static/js/analysis.js`:

- distance-aligned lap interpolation onto a reference lap's axis (`interp`)
- the Δ-time-vs-reference-lap chart concept
- synchronised cursor / drag-zoom across stacked charts, and the pinned
  position marker (`syncZoom`, `drawPinLine`)
- chart-hover to track-map marker linkage
- the A–F multi-lap pick tray and its colour palette
- the raw-telemetry-at-cursor table

The backend is **not** derived from LapScope: LapScope stores laps in SQLite,
whereas PitLane queries InfluxDB, so all data access was written from scratch.
The live dashboard (`dashboard/static/index.html`) is likewise original.
