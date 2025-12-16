#!/bin/bash
# Простой скрипт для просмотра статистики без gRPC

echo "=========================================="
echo "Статистика отслеживания пользователей"
echo "=========================================="
echo ""

# Проверяем логи
if [ ! -f "/var/lib/marznode/xray-access.log" ]; then
    echo "✗ Файл логов не найден!"
    exit 1
fi

echo "📊 Анализ access логов..."
echo ""

# Получаем статистику за последние 1000 строк
LINES_TO_CHECK=1000

echo "Данные за последние $LINES_TO_CHECK подключений:"
echo ""

# Извлекаем уникальных пользователей и их IP
echo "User ID | IP Address         | Подключений"
echo "--------|-------------------|------------"

tail -$LINES_TO_CHECK /var/lib/marznode/xray-access.log | \
    grep -oP 'from (?:tcp:|udp:)?\K[0-9a-fA-F:.]+(?=:\d+)|email: \K[0-9]+(?=\.)' | \
    paste -d' ' - - | \
    awk '{
        user[$2]=$1
        count[$2]++
    }
    END {
        for (uid in user) {
            printf "%-7s | %-17s | %d\n", uid, user[uid], count[uid]
        }
    }' | sort -n

echo ""
echo "=========================================="
echo ""

# Показываем последние активные соединения
echo "🔴 Последние 5 активных подключений:"
echo ""

tail -5 /var/lib/marznode/xray-access.log | while read line; do
    # Извлекаем данные
    TIMESTAMP=$(echo "$line" | grep -oP '^\d{4}/\d{2}/\d{2} \d{2}:\d{2}:\d{2}')
    IP=$(echo "$line" | grep -oP 'from (?:tcp:|udp:)?\K[0-9a-fA-F:.]+(?=:\d+)')
    USER=$(echo "$line" | grep -oP 'email: \K[0-9]+')
    DEST=$(echo "$line" | grep -oP 'accepted \K[^[]+')
    
    echo "  [$TIMESTAMP] User $USER от $IP → $DEST"
done

echo ""
echo "=========================================="
echo ""

# Статистика трафика (если возможно получить через docker)
echo "📈 Статистика трафика (из Xray API):"
echo ""

# Пробуем получить через docker exec
docker compose exec -T marznode python3 -c "
import asyncio
from marznode.backends.xray.api.stats import StatsAPI

async def get_stats():
    try:
        api = StatsAPI('127.0.0.1', 8080)
        stats = await api.get_users_stats(reset=False)
        
        users_data = {}
        for stat in stats:
            parts = stat.name.split('>>>')
            if len(parts) >= 4:
                user_email = parts[1]
                link_type = parts[3]
                try:
                    uid = int(user_email.split('.')[0])
                    if uid not in users_data:
                        users_data[uid] = {'uplink': 0, 'downlink': 0}
                    
                    if link_type == 'uplink':
                        users_data[uid]['uplink'] = stat.value
                    elif link_type == 'downlink':
                        users_data[uid]['downlink'] = stat.value
                except:
                    pass
        
        if users_data:
            print('User ID | Uplink      | Downlink    | Total')
            print('--------|-------------|-------------|------------')
            for uid, data in sorted(users_data.items()):
                up = data['uplink']
                down = data['downlink']
                total = up + down
                
                def fmt(b):
                    for u in ['B', 'KB', 'MB', 'GB', 'TB']:
                        if b < 1024:
                            return f'{b:.1f} {u}'
                        b /= 1024
                    return f'{b:.1f} TB'
                
                print(f'{uid:<7} | {fmt(up):<11} | {fmt(down):<11} | {fmt(total)}')
        else:
            print('Нет данных о трафике')
    except Exception as e:
        print(f'Ошибка получения статистики: {e}')

asyncio.run(get_stats())
" 2>/dev/null || echo "⚠ Не удалось получить данные о трафике"

echo ""
echo "=========================================="
echo "✓ Готово!"
echo ""
echo "💡 Эти данные отправляются в Marzneshin через gRPC"
echo "   в формате: {uid, usage, uplink, downlink, remote_ip}"
echo ""

