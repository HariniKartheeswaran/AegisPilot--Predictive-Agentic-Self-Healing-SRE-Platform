#!/usr/bin/env bash
# Run ON the EC2 Prometheus host (e.g. 3.111.113.151) as root/sudo.
# Wires Aegis pre-alert + critical rules and points Alertmanager at K8s NodePort.
set -euo pipefail

NODE_IP="${NODE_IP:?Set NODE_IP to K3s node IP (War Room host, e.g. 13.207.225.219)}"
RULES_SRC="${1:-}"
PROM_YML="${PROM_YML:-/etc/prometheus/prometheus.yml}"
RULES_DST="${RULES_DST:-/etc/prometheus/aegis_rules.yml}"

if [[ -z "$RULES_SRC" || ! -f "$RULES_SRC" ]]; then
  echo "Usage: NODE_IP=13.207.225.219 $0 /path/to/k8s/alerting/rules.yml"
  exit 1
fi

cp -a "$RULES_SRC" "$RULES_DST"
chmod 644 "$RULES_DST"

# Idempotent snippet markers
BEGIN="# BEGIN AEGIS_ALERTING"
END="# END AEGIS_ALERTING"
if grep -q "$BEGIN" "$PROM_YML" 2>/dev/null; then
  echo "Aegis alerting block already present in $PROM_YML — updating NODE_IP only if needed."
else
  cat >> "$PROM_YML" <<EOF

$BEGIN
rule_files:
  - ${RULES_DST}
alerting:
  alertmanagers:
    - static_configs:
        - targets:
            - "${NODE_IP}:30903"
$END
EOF
  echo "Appended Aegis alerting block to $PROM_YML"
fi

# Prefer lifecycle reload; fall back to restart unit names commonly used.
if curl -sf -X POST http://127.0.0.1:9090/-/reload; then
  echo "Prometheus reloaded."
elif systemctl reload prometheus 2>/dev/null; then
  echo "Prometheus reloaded via systemd."
elif systemctl restart prometheus 2>/dev/null; then
  echo "Prometheus restarted via systemd."
else
  echo "WARN: could not reload Prometheus automatically — reload manually."
fi

echo "Check: http://$(hostname -I | awk '{print $1}'):9090/alerts"
echo "Expect: HighErrorRateWarning + HighErrorRate"
