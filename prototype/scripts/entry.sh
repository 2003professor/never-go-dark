#!/bin/sh
# Container entrypoint: keys, WireGuard interfaces, services. ROLE = hq | vehicle
set -e
ROLE=$1
K=/app/run/keys
mkdir -p "$K" /app/logs
umask 077
for n in a b m; do
  [ -s "$K/$ROLE.$n.key" ] || wg genkey > "$K/$ROLE.$n.key"
  wg pubkey < "$K/$ROLE.$n.key" > "$K/$ROLE.$n.pub"
done
PEER=vehicle; [ "$ROLE" = vehicle ] && PEER=hq
for n in a b m; do while [ ! -s "$K/$PEER.$n.pub" ]; do sleep 0.2; done; done

python3 /app/scripts/wgsetup.py "$ROLE"
if [ "$ROLE" = hq ]; then python3 /app/scripts/hq_services.py & fi
touch "/app/run/$ROLE.ready"
exec sleep infinity
