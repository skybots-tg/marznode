#!/bin/bash
# Скрипт для настройки ротации логов Xray для marznode
# Это предотвращает безграничный рост файла логов

set -e

XRAY_LOG_FILE="/var/lib/marznode/xray-access.log"
LOGROTATE_CONFIG="/etc/logrotate.d/marznode-xray"

echo "=========================================="
echo "Настройка ротации логов для Xray"
echo "=========================================="

# Создаем конфигурацию logrotate
cat > "$LOGROTATE_CONFIG" << 'EOF'
/var/lib/marznode/xray-access.log {
    daily                # Ротация каждый день
    rotate 7             # Хранить 7 последних файлов
    compress             # Сжимать старые логи
    delaycompress        # Не сжимать последний файл
    missingok            # Не ругаться, если файл отсутствует
    notifempty           # Не ротировать пустые файлы
    create 0644 root root # Права на новый файл
    sharedscripts
    postrotate
        # Перезапускаем контейнер после ротации, чтобы Xray начал писать в новый файл
        if [ -f /usr/local/bin/docker-compose ] || command -v docker-compose &> /dev/null; then
            cd /opt/marznode && docker-compose restart marznode > /dev/null 2>&1 || true
        fi
    endscript
}
EOF

echo "✓ Конфигурация logrotate создана: $LOGROTATE_CONFIG"

# Проверяем конфигурацию
if logrotate -d "$LOGROTATE_CONFIG" > /dev/null 2>&1; then
    echo "✓ Конфигурация logrotate валидна"
else
    echo "⚠ Предупреждение: Не удалось проверить конфигурацию logrotate"
fi

echo ""
echo "=========================================="
echo "Настройка завершена!"
echo "=========================================="
echo ""
echo "Логи Xray будут ротироваться ежедневно."
echo "Старые логи будут храниться 7 дней и сжиматься."
echo ""
echo "Для ручной ротации выполните:"
echo "  logrotate -f $LOGROTATE_CONFIG"
echo ""
echo "Для проверки следующей даты ротации:"
echo "  cat /var/lib/logrotate/status | grep xray"
echo ""

