#!/bin/bash
# Быстрая проверка работы отслеживания IP адресов

echo "=========================================="
echo "Проверка отслеживания IP адресов"
echo "=========================================="
echo ""

# Проверка 1: Логи пишутся
echo "1. Проверка access логов..."
if [ -f "/var/lib/marznode/xray-access.log" ]; then
    LINES=$(wc -l < /var/lib/marznode/xray-access.log)
    SIZE=$(du -h /var/lib/marznode/xray-access.log | cut -f1)
    echo "   ✓ Файл логов существует"
    echo "   ✓ Размер: $SIZE"
    echo "   ✓ Строк: $LINES"
    
    # Показываем последние записи
    echo ""
    echo "   Последние 3 записи:"
    tail -3 /var/lib/marznode/xray-access.log | sed 's/^/     /'
else
    echo "   ✗ Файл логов НЕ найден!"
    exit 1
fi

echo ""

# Проверка 2: Парсинг работает
echo "2. Проверка парсинга логов..."
if docker compose logs marznode 2>/dev/null | grep -q "Captured IP"; then
    echo "   ✓ Парсер работает!"
    echo ""
    echo "   Последние захваченные IP:"
    docker compose logs marznode 2>/dev/null | grep "Captured IP" | tail -5 | sed 's/^/     /'
else
    echo "   ⚠ Сообщения 'Captured IP' не найдены"
    echo "   Это нормально, если пользователи не подключались недавно"
    echo "   или если DEBUG режим выключен"
fi

echo ""

# Проверка 3: Извлекаем уникальные IP из логов
echo "3. Уникальные IP адреса в логах (последние 100 строк):"
if [ -f "/var/lib/marznode/xray-access.log" ]; then
    UNIQUE_IPS=$(tail -100 /var/lib/marznode/xray-access.log | \
        grep -oP 'from (?:tcp:|udp:)?\K[0-9a-fA-F:.]+(?=:\d+)' | \
        sort -u)
    
    if [ -n "$UNIQUE_IPS" ]; then
        echo "$UNIQUE_IPS" | while read ip; do
            COUNT=$(tail -100 /var/lib/marznode/xray-access.log | grep -c "$ip")
            echo "   - $ip (подключений: $COUNT)"
        done
    else
        echo "   Нет данных"
    fi
fi

echo ""

# Проверка 4: Извлекаем уникальных пользователей
echo "4. Активные пользователи (последние 100 строк):"
if [ -f "/var/lib/marznode/xray-access.log" ]; then
    UNIQUE_USERS=$(tail -100 /var/lib/marznode/xray-access.log | \
        grep -oP 'email: \K[0-9]+' | \
        sort -u)
    
    if [ -n "$UNIQUE_USERS" ]; then
        echo "$UNIQUE_USERS" | while read uid; do
            # Находим IP для этого пользователя
            USER_IP=$(tail -100 /var/lib/marznode/xray-access.log | \
                grep "email: $uid\." | \
                grep -oP 'from (?:tcp:|udp:)?\K[0-9a-fA-F:.]+(?=:\d+)' | \
                tail -1)
            echo "   - User ID: $uid → IP: ${USER_IP:-unknown}"
        done
    else
        echo "   Нет данных"
    fi
fi

echo ""
echo "=========================================="
echo "Проверка завершена!"
echo "=========================================="
echo ""

# Финальная рекомендация
if [ -f "/etc/logrotate.d/marznode-xray" ]; then
    echo "✓ Ротация логов настроена"
else
    echo "⚠ Ротация логов НЕ настроена!"
    echo "  Выполните: ./setup_log_rotation.sh"
fi

echo ""

