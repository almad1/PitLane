# PitLane on Proxmox LXC

One-liner to deploy the full PitLane telemetry stack (collector + InfluxDB + Grafana + dashboard) into a self-contained Proxmox LXC container.

## Install

Run on your Proxmox VE node as root:

```bash
bash <(curl -fsSL https://raw.githubusercontent.com/almad1/PitLane/main/proxmox/deploy.sh)
```

That's it. The script will:

1. Download the Debian 12 CT template (if not cached)
2. Create a privileged LXC (ID **200** by default)
3. Clone PitLane from GitHub into `/opt/pitlane`
4. Install Docker CE + Compose plugin
5. Generate `/opt/pitlane/.env` with random credentials
6. Build the Docker images and start the stack
7. Register a `pitlane.service` systemd unit (auto-starts on boot)
8. Print your dashboard URL, Grafana URL, and generated passwords

**Runtime: ~3–5 minutes.**

## Customise before running

All settings are environment variables:

```bash
CTID=210 \
IP="10.0.0.50/24,gw=10.0.0.1" \
STORAGE=local-zfs \
bash <(curl -fsSL https://raw.githubusercontent.com/almad1/PitLane/main/proxmox/deploy.sh)
```

| Variable  | Default       | Description |
|-----------|---------------|-------------|
| `CTID`    | `200`         | Proxmox container ID |
| `HOSTNAME`| `pitlane`     | Container hostname |
| `STORAGE` | `local-lvm`   | Proxmox storage pool (`local-lvm`, `local-zfs`, `local`, …) |
| `DISK_GB` | `12`          | Rootfs size in GB |
| `CORES`   | `2`           | vCPU count |
| `RAM_MB`  | `2048`        | RAM in MB (InfluxDB needs ≥1.5 GB) |
| `BRIDGE`  | `vmbr0`       | Proxmox network bridge |
| `IP`      | `dhcp`        | `dhcp` or `x.x.x.x/yy,gw=x.x.x.x` |

## Point Forza at the LXC

In **Forza Horizon → Settings → HUD and Gameplay → Data Out**:

| Field | Value |
|-------|-------|
| Data Out | **On** |
| Data Out IP address | LXC IP (printed after install) |
| Data Out IP port | **5302** |
| Data Out packet format | **Car Dash** |

## Ports

| Service | Port | Protocol |
|---------|------|----------|
| PitLane dashboard | **8080** | HTTP |
| Grafana | **3000** | HTTP |
| InfluxDB | **8086** | HTTP |
| Forza telemetry in | **5302** | **UDP** |

## Useful commands

```bash
# Open a shell in the container
pct enter 200

# View credentials
pct exec 200 -- cat /opt/pitlane/.env

# Check stack
pct exec 200 -- bash -c "cd /opt/pitlane && docker compose ps"

# Live logs
pct exec 200 -- bash -c "cd /opt/pitlane && docker compose logs -f"

# Stop / start
pct exec 200 -- bash -c "cd /opt/pitlane && docker compose down"
pct exec 200 -- bash -c "cd /opt/pitlane && docker compose up -d"
```

## Update

```bash
pct exec 200 -- bash /opt/pitlane/proxmox/update.sh
```

Pulls the latest code from GitHub, rebuilds changed images, and restarts the stack.
