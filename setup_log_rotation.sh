#!/bin/bash
# Настройка ротации логов для marznode (xray access/error + docker container logs).
# Идемпотентно: можно запускать многократно.
#
# Что делает:
#   1. logrotate для /var/log/xray/*.log и /var/lib/marznode/*.log
#      - hourly + size 50M (что наступит раньше)
#      - 3 архива, gzip
#      - copytruncate (без рестарта xray)
#   2. /etc/docker/daemon.json: глобальный лимит docker-логов 50MB x 3
#   3. logrotate для docker JSON-логов (страховка, если контейнер запущен ДО
#      применения daemon.json и продолжает писать в исходный файл)
#   4. Принудительный первый прогон, чтобы освободить место сразу
set -e

LOGROTATE_CONFIG="/etc/logrotate.d/marznode-xray"
LOGROTATE_DOCKER="/etc/logrotate.d/marznode-docker"
DOCKER_DAEMON="/etc/docker/daemon.json"

echo "[setup_log_rotation] === начало ==="

# === 1. logrotate для xray файловых логов ============================
cat > "$LOGROTATE_CONFIG" <<'EOF'
# marznode: xray access/error logs (оба возможных пути)
/var/log/xray/*.log /var/lib/marznode/access.log /var/lib/marznode/error.log {
    hourly
    size 50M
    rotate 3
    compress
    delaycompress
    missingok
    notifempty
    nocreate
    copytruncate
    nomail
    su root root
}
EOF
echo "[setup_log_rotation] записан $LOGROTATE_CONFIG"
logrotate -d "$LOGROTATE_CONFIG" >/dev/null 2>&1 \
    && echo "[setup_log_rotation] logrotate config валиден" \
    || echo "[setup_log_rotation] WARN: logrotate config не прошёл -d проверку"

# === 2. docker daemon.json: глобальный лимит логов ===================
mkdir -p /etc/docker
TMP_DAEMON=$(mktemp)
if [ -s "$DOCKER_DAEMON" ]; then
    python3 - "$DOCKER_DAEMON" "$TMP_DAEMON" <<'PY'
import json, sys
src, dst = sys.argv[1], sys.argv[2]
try:
    with open(src) as f:
        cfg = json.load(f)
except Exception:
    cfg = {}
cfg["log-driver"] = "json-file"
opts = cfg.get("log-opts") or {}
opts["max-size"] = "50m"
opts["max-file"] = "3"
cfg["log-opts"] = opts
with open(dst, "w") as f:
    json.dump(cfg, f, indent=2)
PY
else
    cat > "$TMP_DAEMON" <<'EOF'
{
  "log-driver": "json-file",
  "log-opts": {
    "max-size": "50m",
    "max-file": "3"
  }
}
EOF
fi

if ! diff -q "$TMP_DAEMON" "$DOCKER_DAEMON" >/dev/null 2>&1; then
    cp -a "$DOCKER_DAEMON" "${DOCKER_DAEMON}.bak.$(date +%s)" 2>/dev/null || true
    mv "$TMP_DAEMON" "$DOCKER_DAEMON"
    echo "[setup_log_rotation] обновлён $DOCKER_DAEMON, требуется reload докера для НОВЫХ контейнеров"
    if command -v systemctl >/dev/null 2>&1; then
        systemctl reload docker 2>/dev/null || systemctl restart docker || true
    fi
else
    rm -f "$TMP_DAEMON"
    echo "[setup_log_rotation] $DOCKER_DAEMON уже актуален"
fi

# === 3. logrotate для текущих json-логов запущенных контейнеров =====
# (daemon.json применяется только при создании контейнера; уже бегущим
#  контейнерам нужен logrotate + copytruncate, чтобы не разрастались)
cat > "$LOGROTATE_DOCKER" <<'EOF'
/var/lib/docker/containers/*/*-json.log {
    hourly
    size 50M
    rotate 3
    compress
    delaycompress
    missingok
    notifempty
    copytruncate
    nomail
    su root root
}
EOF
echo "[setup_log_rotation] записан $LOGROTATE_DOCKER"

# === 4. Первый прогон — сразу освобождаем место ======================
echo "[setup_log_rotation] forcing immediate rotation..."
logrotate -f "$LOGROTATE_CONFIG" 2>&1 | tail -5 || true
logrotate -f "$LOGROTATE_DOCKER" 2>&1 | tail -5 || true

# === 5. Hourly cron (страховка на случай редкого daily logrotate) =====
mkdir -p /etc/cron.hourly /etc/cron.d
HOURLY_CRON="/etc/cron.hourly/logrotate-marznode"
cat > "$HOURLY_CRON" <<'EOF'
#!/bin/sh
/usr/sbin/logrotate /etc/logrotate.d/marznode-xray >/dev/null 2>&1 || true
/usr/sbin/logrotate /etc/logrotate.d/marznode-docker >/dev/null 2>&1 || true
EOF
chmod +x "$HOURLY_CRON"
echo "[setup_log_rotation] установлен $HOURLY_CRON"

# Дополнительно — /etc/cron.d/, на случай если /etc/cron.hourly не запускается
CRON_D="/etc/cron.d/logrotate-marznode"
cat > "$CRON_D" <<'EOF'
# m h dom mon dow user command
17 * * * * root /usr/sbin/logrotate /etc/logrotate.d/marznode-xray >/dev/null 2>&1
23 * * * * root /usr/sbin/logrotate /etc/logrotate.d/marznode-docker >/dev/null 2>&1
EOF
chmod 0644 "$CRON_D"
echo "[setup_log_rotation] установлен $CRON_D"

echo "[setup_log_rotation] === готово ==="
echo
echo "Текущий размер логов:"
du -sh /var/log/xray/ /var/lib/marznode/*.log /var/lib/docker/containers 2>/dev/null | sort -h || true
