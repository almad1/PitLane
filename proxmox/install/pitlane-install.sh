#!/usr/bin/env bash
# Copyright (c) 2025 Allen Madsen
# License: MIT | https://github.com/almad1/PitLane
# Source: https://github.com/almad1/PitLane
#
# PitLane install script — runs inside the LXC container.
# Called by proxmox/deploy.sh after container creation.

# ── Load community-scripts function library ───────────────────────────────────
# install.func bootstraps core.func + error_handler.func, giving us:
#   $STD, msg_info, msg_ok, msg_warn, msg_error, catch_errors,
#   setting_up_container, network_check, update_os, setup_docker, cleanup_lxc
source /dev/stdin <<<"$(curl -fsSL https://raw.githubusercontent.com/community-scripts/ProxmoxVE/main/misc/install.func)"
color
verb_ip6
catch_errors
setting_up_container
network_check
update_os

REPO="${REPO:-https://github.com/almad1/PitLane.git}"
BRANCH="${BRANCH:-main}"

# ── Dependencies ─────────────────────────────────────────────────────────────
msg_info "Installing Dependencies"
$STD apt-get install -y git openssl ca-certificates
msg_ok "Installed Dependencies"

# ── Docker CE (official repo, includes docker-compose-plugin) ─────────────────
msg_info "Installing Docker"
setup_docker
msg_ok "Installed Docker"

# ── Clone repo ────────────────────────────────────────────────────────────────
msg_info "Cloning PitLane"
$STD git clone --branch "$BRANCH" "$REPO" /opt/pitlane
msg_ok "Cloned PitLane (${BRANCH})"

# ── Generate credentials ──────────────────────────────────────────────────────
msg_info "Generating credentials"
TOKEN=$(openssl rand -hex 32)
INFLUX_PASS=$(openssl rand -base64 12 | tr -dc 'A-Za-z0-9' | head -c 16)
GRAFANA_PASS=$(openssl rand -base64 8  | tr -dc 'A-Za-z0-9' | head -c 12)
ROOT_PASS=$(openssl rand -base64 12   | tr -dc 'A-Za-z0-9' | head -c 16)
echo "root:${ROOT_PASS}" | chpasswd

{
  echo "# PitLane — generated $(date -u +%Y-%m-%dT%H:%M:%SZ)"
  echo "# Edit this file then run: cd /opt/pitlane && docker compose up -d"
  echo ""
  echo "# ── InfluxDB ──────────────────────────────────────────────────────────"
  echo "INFLUXDB_INIT_USERNAME=admin"
  echo "INFLUXDB_INIT_PASSWORD=${INFLUX_PASS}"
  echo "INFLUXDB_ORG=pitlane"
  echo "INFLUXDB_BUCKET=forza"
  echo "INFLUXDB_TOKEN=${TOKEN}"
  echo ""
  echo "# ── Grafana ────────────────────────────────────────────────────────────"
  echo "GRAFANA_USER=admin"
  echo "GRAFANA_PASSWORD=${GRAFANA_PASS}"
  echo ""
  echo "# ── Collector ──────────────────────────────────────────────────────────"
  echo "# Match this port in Forza's Data Out settings."
  echo "UDP_PORT=5302"
} > /opt/pitlane/.env
chmod 600 /opt/pitlane/.env
msg_ok "Generated credentials"

# ── Build Docker images ───────────────────────────────────────────────────────
msg_info "Building Docker images"
cd /opt/pitlane
$STD docker compose build
msg_ok "Built Docker images"

# ── systemd service ───────────────────────────────────────────────────────────
msg_info "Installing pitlane.service"
cat > /etc/systemd/system/pitlane.service << 'UNIT'
[Unit]
Description=PitLane Telemetry Stack
After=docker.service
Requires=docker.service

[Service]
Type=oneshot
RemainAfterExit=yes
WorkingDirectory=/opt/pitlane
EnvironmentFile=/opt/pitlane/.env
ExecStart=/usr/bin/docker compose up -d --remove-orphans
ExecStop=/usr/bin/docker compose down
StandardOutput=journal
StandardError=journal
TimeoutStartSec=120

[Install]
WantedBy=multi-user.target
UNIT
$STD systemctl enable --now pitlane.service
msg_ok "Installed pitlane.service"

# ── Console auto-login (TTY only — SSH still requires the root password) ──────
msg_info "Configuring console"
GETTY_OVERRIDE="/etc/systemd/system/container-getty@1.service.d/override.conf"
mkdir -p "$(dirname "$GETTY_OVERRIDE")"
cat > "$GETTY_OVERRIDE" << 'GETTY'
[Service]
ExecStart=
ExecStart=-/sbin/agetty --autologin root --noclear --keep-baud tty%I 115200,38400,9600 $TERM
GETTY
systemctl daemon-reload

# ── MOTD ─────────────────────────────────────────────────────────────────────
[[ -d /etc/update-motd.d ]] && chmod -x /etc/update-motd.d/* 2>/dev/null || true
cat > /etc/profile.d/00_pitlane.sh << 'MOTD'
[ -t 1 ] || return 0
IP=$(hostname -I 2>/dev/null | awk '{print $1}')
echo ""
echo "  PitLane — Forza telemetry stack"
echo ""
echo "    Dashboard   http://${IP}:8080"
echo "    Grafana     http://${IP}:3000"
echo "    Forza UDP   ${IP}:5302"
echo ""
echo "  Type 'update' to pull the latest code and restart the stack."
echo ""
MOTD
grep -qxF "export TERM='xterm-256color'" /root/.bashrc \
  || echo "export TERM='xterm-256color'" >> /root/.bashrc
msg_ok "Configured console"

# ── 'update' command shortcut ─────────────────────────────────────────────────
msg_info "Installing update command"
echo 'bash /opt/pitlane/proxmox/update.sh' > /usr/bin/update
chmod +x /usr/bin/update
msg_ok "Installed update command"

# ── Credentials output (captured by host for the install summary) ─────────────
echo "##PITLANE_CREDS##"                         >&2
echo "GRAFANA_PASSWORD=${GRAFANA_PASS}"           >&2
echo "INFLUXDB_INIT_PASSWORD=${INFLUX_PASS}"      >&2
echo "INFLUXDB_TOKEN=${TOKEN}"                    >&2
echo "ROOT_PASSWORD=${ROOT_PASS}"                 >&2
echo "##PITLANE_CREDS_END##"                      >&2

# ── Final cleanup (community-scripts pattern) ─────────────────────────────────
cleanup_lxc
