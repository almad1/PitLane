#!/usr/bin/env bash
# Copyright (c) 2025 Allen Madsen
# License: MIT | https://github.com/almad1/PitLane
# Source: https://github.com/almad1/PitLane
#
# PitLane — Proxmox LXC installer
#
# Run on your Proxmox VE node as root:
#
#   bash <(curl -fsSL https://raw.githubusercontent.com/almad1/PitLane/main/proxmox/deploy.sh)
#
# Override defaults:
#
#   CT_HOSTNAME=pitlane2 IP="10.0.0.50/24,gw=10.0.0.1" bash <(curl -fsSL ...)

set -euo pipefail

# ── Application metadata ──────────────────────────────────────────────────────
APP="PitLane"

# ── Container defaults (var_* naming matches community-scripts conventions) ───
var_cpu="${var_cpu:-2}"
var_ram="${var_ram:-2048}"    # InfluxDB needs ≥1.5 GB
var_disk="${var_disk:-12}"
var_os="${var_os:-debian}"
var_version="${var_version:-12}"
var_unprivileged="${var_unprivileged:-0}"  # privileged: Docker nesting is more reliable
var_tags="${var_tags:-pitlane;forza;telemetry;docker}"

# ── Instance settings (override via env) ─────────────────────────────────────
# CT_HOSTNAME avoids collision with bash's built-in $HOSTNAME variable
CT_HOSTNAME="${CT_HOSTNAME:-pitlane}"
STORAGE="${STORAGE:-local-lvm}"
BRIDGE="${BRIDGE:-vmbr0}"
IP="${IP:-dhcp}"
REPO="${REPO:-https://github.com/almad1/PitLane.git}"
BRANCH="${BRANCH:-main}"

# ── CTID: cluster-aware auto-detection ───────────────────────────────────────
if [[ -z "${CTID:-}" ]]; then
  CTID=$(pvesh get /cluster/nextid 2>/dev/null | tr -d '"' || true)
fi
if [[ -z "${CTID:-}" ]]; then
  # Fallback: scan pct + qm lists and find the first gap above 100
  USED=$( { pct list 2>/dev/null; qm list 2>/dev/null; } \
          | awk 'NR>1 {print $1}' | sort -n )
  CTID=100
  while printf '%s\n' "$USED" | grep -qx "$CTID"; do CTID=$(( CTID + 1 )); done
fi

# ── Messaging (community-scripts msg_* style) ─────────────────────────────────
YW='\033[33m'; GN='\033[1;32m'; RD='\033[1;31m'; CL='\033[0m'
CM='✔ '; CROSS='✖ '; TAB='  '
msg_info()  { echo -e "${TAB}⏳ ${YW}${*}${CL}"; }
msg_ok()    { echo -e "${TAB}${CM}${GN}${*}${CL}"; }
msg_warn()  { echo -e "${TAB}⚠  ${YW}${*}${CL}" >&2; }
msg_error() { echo -e "${TAB}${CROSS}${RD}${*}${CL}" >&2; exit 1; }
header_info() {
  echo ""
  echo -e "  ${GN}${APP}${CL} — Proxmox LXC Installer"
  echo ""
}

# ── update_script() ───────────────────────────────────────────────────────────
# Called automatically when this script is re-run inside an existing container.
# Mirrors the community-scripts update_script() pattern.
function update_script() {
  header_info

  if [[ ! -d /opt/pitlane ]]; then
    msg_error "No ${APP} Installation Found!"
  fi

  # Storage pre-check (mirrors check_container_storage)
  local disk_usage
  disk_usage=$(df /boot 2>/dev/null | awk 'NR==2 {gsub(/%/,""); print $5}' || echo "0")
  [[ "$disk_usage" -gt 85 ]] && \
    msg_warn "Storage is dangerously low (${disk_usage}% used on /boot)"

  msg_info "Updating ${APP}"
  bash /opt/pitlane/proxmox/update.sh
  msg_ok "Updated successfully!"
  exit
}

# ── Context detection ─────────────────────────────────────────────────────────
# pveversion only exists on Proxmox hosts. If it's missing we're running inside
# a container — route to update_script(), matching the community-scripts start()
# pattern.
if ! command -v pveversion >/dev/null 2>&1; then
  update_script
fi

# ── Pre-flight checks ─────────────────────────────────────────────────────────
[[ "$(id -u)" == "0" ]]     || msg_error "Must be run as root on the Proxmox node."
command -v pct   >/dev/null || msg_error "'pct' not found — run this on a Proxmox VE host."
command -v pveam >/dev/null || msg_error "'pveam' not found."

header_info
printf "  %-18s %s\n" "Container ID:"  "$CTID"
printf "  %-18s %s\n" "Hostname:"      "$CT_HOSTNAME"
printf "  %-18s %s  (${var_disk} GB disk)\n" "Storage:" "$STORAGE"
printf "  %-18s %s cores / %s MB RAM\n" "Resources:" "$var_cpu" "$var_ram"
printf "  %-18s %s  ip=%s\n" "Network:"    "$BRIDGE" "$IP"
printf "  %-18s %s @ %s\n"   "Source:"     "$REPO" "$BRANCH"
echo ""
read -rp "  Continue? [y/N] " _OK
[[ "$_OK" =~ ^[Yy]$ ]] || { echo "  Aborted."; exit 0; }
echo ""

# ── Debian CT template ────────────────────────────────────────────────────────
msg_info "Refreshing template list"
pveam update >/dev/null 2>&1 || true

TMPL=$(pveam available --section system 2>/dev/null \
  | awk '{print $2}' | grep "^${var_os}-${var_version}" | sort -V | tail -1)
[[ -n "$TMPL" ]] || msg_error "No ${var_os} ${var_version} template found. Try: pveam update"

if [[ ! -f "/var/lib/vz/template/cache/$TMPL" ]]; then
  msg_info "Downloading ${TMPL}"
  pveam download local "$TMPL" >/dev/null
  msg_ok "Downloaded ${TMPL}"
else
  msg_ok "Template cached: ${TMPL}"
fi

# ── Create LXC ────────────────────────────────────────────────────────────────
pct status "$CTID" &>/dev/null && msg_error "Container ${CTID} already exists. Use a different CTID."

msg_info "Creating LXC ${CTID}"
pct create "$CTID" "local:vztmpl/$TMPL" \
  --hostname     "$CT_HOSTNAME"              \
  --ostype       "$var_os"                   \
  --cores        "$var_cpu"                  \
  --memory       "$var_ram"                  \
  --rootfs       "${STORAGE}:${var_disk}"    \
  --net0         "name=eth0,bridge=${BRIDGE},ip=${IP}" \
  --unprivileged "$var_unprivileged"         \
  --features     nesting=1                   \
  --tags         "$var_tags"                 \
  --onboot       1                           \
  --start        1
msg_ok "Created LXC ${CTID}"

msg_info "Waiting for container to boot"
for i in $(seq 1 30); do
  pct exec "$CTID" -- hostname &>/dev/null && break
  sleep 1
done
pct exec "$CTID" -- hostname &>/dev/null || msg_error "Container never became responsive."
msg_ok "Container is responsive"

# ── Run install script inside the LXC ─────────────────────────────────────────
INSTALL_URL="https://raw.githubusercontent.com/almad1/PitLane/${BRANCH}/proxmox/install/pitlane-install.sh"
msg_info "Fetching install script"
SETUP=$(mktemp /tmp/pitlane_setup.XXXXXX.sh)
trap 'rm -f "$SETUP"' EXIT
curl -fsSL "$INSTALL_URL" > "$SETUP"
msg_ok "Fetched install script"

msg_info "Running install script in container"
pct push "$CTID" "$SETUP" /tmp/pitlane-install.sh
pct exec "$CTID" -- chmod +x /tmp/pitlane-install.sh

# Tee output to a log so we can extract the credentials block afterwards
LOG=$(mktemp /tmp/pitlane_log.XXXXXX)
pct exec "$CTID" -- env \
  REPO="$REPO" \
  BRANCH="$BRANCH" \
  bash /tmp/pitlane-install.sh 2>&1 | tee "$LOG"
pct exec "$CTID" -- rm -f /tmp/pitlane-install.sh

# ── Print summary ─────────────────────────────────────────────────────────────
LXC_IP=$(pct exec "$CTID" -- hostname -I 2>/dev/null | awk '{print $1}' || echo "")

GF_PASS=$(awk '/##PITLANE_CREDS##/,/##PITLANE_CREDS_END##/' "$LOG" \
  | grep 'GRAFANA_PASSWORD='      | cut -d= -f2)
IDB_PASS=$(awk '/##PITLANE_CREDS##/,/##PITLANE_CREDS_END##/' "$LOG" \
  | grep 'INFLUXDB_INIT_PASSWORD=' | cut -d= -f2)
ROOT_PASS=$(awk '/##PITLANE_CREDS##/,/##PITLANE_CREDS_END##/' "$LOG" \
  | grep 'ROOT_PASSWORD='          | cut -d= -f2)
rm -f "$LOG"

echo ""
echo -e "  ${GN}${APP} setup has been successfully initialized!${CL}"
echo ""
if [[ -n "${LXC_IP:-}" ]]; then
  echo -e "  Access it using:"
  echo -e "${TAB}${TAB}Dashboard  →  http://${LXC_IP}:8080"
  echo -e "${TAB}${TAB}Grafana    →  http://${LXC_IP}:3000"
  echo -e "${TAB}${TAB}Forza UDP  →  ${LXC_IP}:5302"
fi
echo ""
echo -e "  ${YW}Generated credentials${CL}"
[[ -n "${ROOT_PASS:-}"  ]] && printf "  %-22s %s\n" "LXC root password:"  "$ROOT_PASS"
[[ -n "${GF_PASS:-}"    ]] && printf "  %-22s %s\n" "Grafana password:"   "$GF_PASS"
[[ -n "${IDB_PASS:-}"   ]] && printf "  %-22s %s\n" "InfluxDB password:"  "$IDB_PASS"
echo -e "  (Full .env: pct exec ${CTID} -- cat /opt/pitlane/.env)"
echo ""
echo -e "  Console access:   auto-login (no password at TTY)"
echo -e "  SSH access:       ssh root@${LXC_IP:-<ip>}  (root password above)"
echo -e "  To update:        open console and type  ${GN}update${CL}"
echo ""
