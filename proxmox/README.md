# PitLane on Proxmox LXC

One-liner to deploy the full PitLane telemetry stack (collector + InfluxDB + Grafana + dashboard) into a self-contained Proxmox LXC container.

## Install

Run on your Proxmox VE node as root:

```bash
bash <(curl -fsSL https://raw.githubusercontent.com/almad1/PitLane/main/proxmox/deploy.sh)
```

That's it. The script will:

1. Download the Debian 12 CT template (if not cached)
2. Create a privileged LXC with the `pitlane` tag
3. Load the [community-scripts](https://github.com/community-scripts/ProxmoxVE) function library inside the container
4. Install Docker CE via the official repository
5. Clone PitLane from GitHub into `/opt/pitlane`
6. Generate `/opt/pitlane/.env` with random credentials
7. Build the Docker images and start the stack
8. Register a `pitlane.service` systemd unit (auto-starts on boot)
9. Configure console auto-login and a login MOTD
10. Print your dashboard URL, Grafana URL, and generated passwords

**Runtime: ~3–5 minutes.**

## Customise before running

All settings are environment variables:

```bash
CTID=210 \
IP="10.0.0.50/24,gw=10.0.0.1" \
STORAGE=local-zfs \
bash <(curl -fsSL https://raw.githubusercontent.com/almad1/PitLane/main/proxmox/deploy.sh)
```

| Variable          | Default       | Description |
|-------------------|---------------|-------------|
| `CTID`            | auto-detected | Proxmox container ID |
| `CT_HOSTNAME`     | `pitlane`     | Container hostname |
| `STORAGE`         | `local-lvm`   | Proxmox storage pool (`local-lvm`, `local-zfs`, `local`, …) |
| `var_disk`        | `12`          | Rootfs size in GB |
| `var_cpu`         | `2`           | vCPU count |
| `var_ram`         | `2048`        | RAM in MB (InfluxDB needs ≥1.5 GB) |
| `BRIDGE`          | `vmbr0`       | Proxmox network bridge |
| `IP`              | `dhcp`        | `dhcp` or `x.x.x.x/yy,gw=x.x.x.x` |
| `REPO`            | this repo     | Git repo to clone |
| `BRANCH`          | `main`        | Branch to deploy |

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

## Update

Open the Proxmox console for the container (auto-login, no password needed) and type:

```bash
update
```

Or from the Proxmox host:

```bash
pct exec <CTID> -- bash /opt/pitlane/proxmox/update.sh
```

Pulls the latest code from GitHub, rebuilds changed images, and restarts the stack.

## File structure

```
proxmox/
├── deploy.sh               # CT script — runs on Proxmox host, creates the LXC
├── install/
│   └── pitlane-install.sh  # Install script — runs inside the container
└── update.sh               # Update script — run via 'update' inside the container
```

The architecture follows the [community-scripts](https://community-scripts.org/docs/ct/detailed_guide) convention:
- `deploy.sh` handles container creation and exposes `update_script()` for in-container updates
- `install/pitlane-install.sh` sources `install.func` from community-scripts for `$STD`, `setup_docker`, `cleanup_lxc`, and other helpers

## Useful commands

```bash
# View credentials
pct exec <CTID> -- cat /opt/pitlane/.env

# Check stack
pct exec <CTID> -- bash -c "cd /opt/pitlane && docker compose ps"

# Live logs
pct exec <CTID> -- bash -c "cd /opt/pitlane && docker compose logs -f"

# Stop / start
pct exec <CTID> -- bash -c "cd /opt/pitlane && docker compose down"
pct exec <CTID> -- bash -c "cd /opt/pitlane && docker compose up -d"
```
