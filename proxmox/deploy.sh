#!/usr/bin/env bash
# ─────────────────────────────────────────────────────────────────────────────
# PitLane — Proxmox LXC installer (single script)
#
# Run on your Proxmox VE node as root:
#
#   bash <(curl -fsSL https://raw.githubusercontent.com/almad1/PitLane/main/proxmox/deploy.sh)
#
# Override any setting via environment variables before calling:
#
#   CTID=210 CT_HOSTNAME=pitlane2 IP="10.0.0.50/24,gw=10.0.0.1" bash <(curl -fsSL ...)
# ─────────────────────────────────────────────────────────────────────────────
set -euo pipefail

# ── Settings — edit or override with env vars ─────────────────────────────────
# CT_HOSTNAME avoids colliding with bash's built-in $HOSTNAME variable
CT_HOSTNAME="${CT_HOSTNAME:-pitlane}"
STORAGE="${STORAGE:-local-lvm}"  # Proxmox storage pool for the rootfs disk
DISK_GB="${DISK_GB:-12}"
CORES="${CORES:-2}"
RAM_MB="${RAM_MB:-2048}"         # InfluxDB needs headroom; 2 GB minimum
BRIDGE="${BRIDGE:-vmbr0}"
# IP: "dhcp"  or  "192.168.1.50/24,gw=192.168.1.1"
IP="${IP:-dhcp}"

# CTID: use Proxmox's cluster-aware next-ID finder so we never collide with
# existing VMs or containers.  Falls back to manual scan if pvesh is absent.
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

REPO="${REPO:-https://github.com/almad1/PitLane.git}"
BRANCH="${BRANCH:-main}"
# ─────────────────────────────────────────────────────────────────────────────

R='\033[1;31m'; G='\033[1;32m'; Y='\033[1;33m'; C='\033[1;36m'; N='\033[0m'
log()  { echo -e "  ${G}▶${N} $*"; }
warn() { echo -e "  ${Y}⚠${N}  $*"; }
die()  { echo -e "  ${R}✖${N}  $*" >&2; exit 1; }
hr()   { echo -e "${C}────────────────────────────────────────────────────────${N}"; }

# ── Pre-flight checks ─────────────────────────────────────────────────────────
[[ "$(id -u)" == "0" ]]     || die "Must be run as root on the Proxmox node."
command -v pct   >/dev/null || die "'pct' not found — run this on a Proxmox VE host."
command -v pveam >/dev/null || die "'pveam' not found."

hr
echo ""
echo -e "  ${C}PitLane — Proxmox LXC Installer${N}"
echo ""
printf "  %-18s %s\n" "Container ID:"  "$CTID"
printf "  %-18s %s\n" "Hostname:"      "$CT_HOSTNAME"
printf "  %-18s %s  (${DISK_GB} GB disk)\n" "Storage:"  "$STORAGE"
printf "  %-18s %s cores / %s MB RAM\n" "Resources:" "$CORES" "$RAM_MB"
printf "  %-18s %s  ip=%s\n" "Network:"  "$BRIDGE" "$IP"
printf "  %-18s %s\n" "Source repo:"   "$REPO"
printf "  %-18s %s\n" "Branch:"        "$BRANCH"
echo ""
hr
echo ""
read -rp "  Continue? [y/N] " _OK
[[ "$_OK" =~ ^[Yy]$ ]] || { echo "  Aborted."; exit 0; }
echo ""

# ── Debian 12 CT template ─────────────────────────────────────────────────────
log "Refreshing template list…"
pveam update >/dev/null 2>&1 || true

TMPL=$(pveam available --section system 2>/dev/null \
  | awk '{print $2}' | grep '^debian-12' | sort -V | tail -1)
[[ -n "$TMPL" ]] || die "No Debian 12 template found. Try: pveam update"

if [[ ! -f "/var/lib/vz/template/cache/$TMPL" ]]; then
  log "Downloading $TMPL…"
  pveam download local "$TMPL"
else
  log "Template cached: $TMPL"
fi

# ── Create LXC ────────────────────────────────────────────────────────────────
pct status "$CTID" &>/dev/null && die "Container $CTID already exists. Use a different CTID."

log "Creating LXC $CTID…"
pct create "$CTID" "local:vztmpl/$TMPL" \
  --hostname    "$CT_HOSTNAME"              \
  --ostype      debian                      \
  --cores       "$CORES"                    \
  --memory      "$RAM_MB"                   \
  --rootfs      "${STORAGE}:${DISK_GB}"     \
  --net0        "name=eth0,bridge=${BRIDGE},ip=${IP}" \
  --unprivileged 0                           \
  --features    nesting=1                    \
  --tags        pitlane                      \
  --onboot      1                            \
  --start       1

log "Waiting for container to boot…"
for i in $(seq 1 30); do
  pct exec "$CTID" -- hostname &>/dev/null && break
  sleep 1
done
pct exec "$CTID" -- hostname &>/dev/null || die "Container never became responsive."

# ── Build the in-container setup script ──────────────────────────────────────
# Uses a tempfile so the inner script can use its own variables ($TOKEN, etc.)
# without interference from the outer shell.  << 'EOF' (single-quoted) writes
# everything literally; the LXC shell expands variables when it runs the file.

SETUP=$(mktemp /tmp/pitlane_setup.XXXXXX.sh)
trap 'rm -f "$SETUP"' EXIT

cat > "$SETUP" << 'PITLANE_INNER_EOF'
#!/usr/bin/env bash
set -euo pipefail

REPO="PITLANE_REPO_PLACEHOLDER"
BRANCH="PITLANE_BRANCH_PLACEHOLDER"

G='\033[1;32m'; Y='\033[1;33m'; N='\033[0m'
log()  { echo -e "  ${G}▶${N} $*"; }
warn() { echo -e "  ${Y}⚠${N}  $*"; }

# ── System packages ───────────────────────────────────────────────────────────
log "Updating system packages…"
# Suppress locale noise from a bare LXC template
export LANG=C
export LC_ALL=C
export DEBIAN_FRONTEND=noninteractive
# Pipeline-Depth=0 prevents the flood of "Tried to start delayed item" warnings
# that appear when apt's HTTP pipelining races on a freshly-networked container.
APT="apt-get -o Acquire::http::Pipeline-Depth=0 -o Acquire::ForceIPv4=true"
$APT update -qq
$APT upgrade -y -qq -o Dpkg::Use-Pty=0
$APT install -y -qq -o Dpkg::Use-Pty=0 git curl openssl ca-certificates

# ── Docker CE ─────────────────────────────────────────────────────────────────
log "Installing Docker CE…"
curl -fsSL https://get.docker.com | sh >/dev/null 2>&1
systemctl enable docker --quiet
systemctl start docker
$APT install -y -qq -o Dpkg::Use-Pty=0 docker-compose-plugin
log "Docker $(docker --version | awk '{print $3}' | tr -d ',')"

# ── Clone repo ────────────────────────────────────────────────────────────────
log "Cloning PitLane from ${REPO}…"
git clone --branch "$BRANCH" "$REPO" /opt/pitlane
log "Cloned branch ${BRANCH}."

# ── Generate .env with random credentials ─────────────────────────────────────
log "Generating credentials…"
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

# ── Build Docker images ────────────────────────────────────────────────────────
log "Building Docker images (takes 2–3 min on first run)…"
cd /opt/pitlane
docker compose build --quiet

# ── systemd service ────────────────────────────────────────────────────────────
log "Installing pitlane.service…"
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

systemctl daemon-reload
systemctl enable pitlane.service --quiet

# ── Console auto-login (TTY only — SSH still requires the root password) ──────
log "Configuring console auto-login…"
GETTY_OVERRIDE="/etc/systemd/system/container-getty@1.service.d/override.conf"
mkdir -p "$(dirname "$GETTY_OVERRIDE")"
cat > "$GETTY_OVERRIDE" << 'GETTY'
[Service]
ExecStart=
ExecStart=-/sbin/agetty --autologin root --noclear --keep-baud tty%I 115200,38400,9600 $TERM
GETTY
systemctl daemon-reload

# ── MOTD (shown on every login) ───────────────────────────────────────────────
log "Installing MOTD…"
# Disable the default dynamic MOTD scripts
[[ -d /etc/update-motd.d ]] && chmod -x /etc/update-motd.d/* 2>/dev/null || true
# Write a profile.d script that prints info on interactive login
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
# Ensure TERM is set for colour in the console
grep -qxF "export TERM='xterm-256color'" /root/.bashrc \
  || echo "export TERM='xterm-256color'" >> /root/.bashrc

# ── 'update' command shortcut ─────────────────────────────────────────────────
log "Installing 'update' command…"
echo 'bash /opt/pitlane/proxmox/update.sh' > /usr/bin/update
chmod +x /usr/bin/update

# ── Start ─────────────────────────────────────────────────────────────────────
log "Starting PitLane stack…"
docker compose up -d

log "Stack status:"
docker compose ps --format "table {{.Name}}\t{{.Status}}"

# Write credentials to stderr so they're captured by the host's log file
# but don't appear inline during the live docker pull/start output.
echo "##PITLANE_CREDS##"                        >&2
echo "GRAFANA_PASSWORD=${GRAFANA_PASS}"          >&2
echo "INFLUXDB_INIT_PASSWORD=${INFLUX_PASS}"     >&2
echo "INFLUXDB_TOKEN=${TOKEN}"                   >&2
echo "ROOT_PASSWORD=${ROOT_PASS}"                >&2
echo "##PITLANE_CREDS_END##"                     >&2
PITLANE_INNER_EOF

# Inject real repo + branch values
sed -i "s|PITLANE_REPO_PLACEHOLDER|${REPO}|g"     "$SETUP"
sed -i "s|PITLANE_BRANCH_PLACEHOLDER|${BRANCH}|g" "$SETUP"

# ── Run inside the LXC ────────────────────────────────────────────────────────
log "Pushing setup script and running inside container…"
pct push "$CTID" "$SETUP" /tmp/pitlane_setup.sh
pct exec "$CTID" -- chmod +x /tmp/pitlane_setup.sh

# Run and tee to a log so we can extract the credentials block at the end
LOG=$(mktemp /tmp/pitlane_log.XXXXXX)
pct exec "$CTID" -- bash /tmp/pitlane_setup.sh 2>&1 | tee "$LOG"
pct exec "$CTID" -- rm -f /tmp/pitlane_setup.sh

# ── Print summary ─────────────────────────────────────────────────────────────
LXC_IP=$(pct exec "$CTID" -- hostname -I 2>/dev/null | awk '{print $1}' || echo "")

# Extract generated passwords from log
GF_PASS=$(awk '/##PITLANE_CREDS##/,/##PITLANE_CREDS_END##/' "$LOG" \
  | grep 'GRAFANA_PASSWORD=' | cut -d= -f2)
IDB_PASS=$(awk '/##PITLANE_CREDS##/,/##PITLANE_CREDS_END##/' "$LOG" \
  | grep 'INFLUXDB_INIT_PASSWORD=' | cut -d= -f2)
ROOT_PASS=$(awk '/##PITLANE_CREDS##/,/##PITLANE_CREDS_END##/' "$LOG" \
  | grep 'ROOT_PASSWORD=' | cut -d= -f2)
rm -f "$LOG"

echo ""
hr
echo ""
echo -e "  ${G}PitLane is running — LXC ${CTID}${N}"
echo ""
if [[ -n "${LXC_IP:-}" ]]; then
  printf "  %-22s ${C}%s${N}\n" "Container IP:"   "$LXC_IP"
  printf "  %-22s ${C}http://%s:8080${N}\n" "Dashboard:"    "$LXC_IP"
  printf "  %-22s ${C}http://%s:3000${N}\n" "Grafana:"      "$LXC_IP"
  printf "  %-22s ${C}%s:5302 (UDP)${N}\n"  "Forza data-out:" "$LXC_IP"
fi
echo ""
echo -e "  ${Y}Generated credentials${N}"
[[ -n "${ROOT_PASS:-}" ]] && printf "  %-22s %s\n" "LXC root password:" "$ROOT_PASS"
[[ -n "${GF_PASS:-}"   ]] && printf "  %-22s %s\n" "Grafana password:"  "$GF_PASS"
[[ -n "${IDB_PASS:-}"  ]] && printf "  %-22s %s\n" "InfluxDB password:" "$IDB_PASS"
echo -e "  ${Y}(Full .env: pct exec ${CTID} -- cat /opt/pitlane/.env)${N}"
echo ""
echo -e "  To update:        open the LXC console and type ${C}update${N}"
echo -e "  (or from host):   ${C}pct exec ${CTID} -- bash /opt/pitlane/proxmox/update.sh${N}"
echo -e "  Console access:   auto-login (no password prompt at TTY)"
echo -e "  SSH access:       ${C}ssh root@${LXC_IP}${N}  (root password above)"
echo ""
hr
