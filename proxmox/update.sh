#!/usr/bin/env bash
# ─────────────────────────────────────────────────────────────────────────────
# PitLane — update to latest code from GitHub (run inside the LXC)
#
# From the Proxmox host:
#   pct exec <CTID> -- bash /opt/pitlane/proxmox/update.sh
# ─────────────────────────────────────────────────────────────────────────────
set -euo pipefail

G='\033[1;32m'; N='\033[0m'
log() { echo -e "  ${G}▶${N} $*"; }

cd /opt/pitlane

log "Pulling latest code…"
git pull origin main

log "Rebuilding changed images…"
docker compose build --quiet

log "Restarting stack…"
docker compose up -d --remove-orphans

log "Running containers:"
docker compose ps --format "table {{.Name}}\t{{.Status}}"
