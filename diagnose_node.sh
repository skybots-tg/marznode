#!/bin/bash
# Диагностика проблем с marznode

echo "=== 1. Проверка статуса контейнера ==="
docker ps -a | grep marznode

echo ""
echo "=== 2. Последние логи marznode (50 строк) ==="
docker logs marznode --tail 50 2>&1

echo ""
echo "=== 3. Проверка, запущен ли xray внутри контейнера ==="
docker exec marznode ps aux 2>/dev/null || echo "Контейнер не запущен"

echo ""
echo "=== 4. Проверка, слушает ли gRPC порт ==="
docker exec marznode netstat -tlnp 2>/dev/null | grep 53042 || echo "Порт 53042 не слушает"

echo ""
echo "=== 5. Проверка xray процесса ==="
docker exec marznode pgrep -a xray 2>/dev/null || echo "xray процесс не найден"

echo ""
echo "=== 6. Последние ошибки в логах ==="
docker logs marznode 2>&1 | grep -iE "(error|exception|failed|traceback)" | tail -20
