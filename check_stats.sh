#!/bin/bash
# Обертка для запуска test_grpc_stats.py внутри Docker контейнера

echo "Запуск проверки статистики через Docker контейнер..."
echo ""

# Копируем скрипт в контейнер и запускаем
docker compose exec -T marznode python3 - << 'PYTHON_SCRIPT'
#!/usr/bin/env python3
"""Тестовый скрипт для проверки статистики через gRPC"""

import asyncio
import json
from grpclib.client import Channel
from marznode.service.service_grpc import MarzServiceStub
from marznode.service.service_pb2 import Empty


async def fetch_stats(host="127.0.0.1", port=53042):
    """Получает статистику пользователей через gRPC"""
    
    print("=" * 70)
    print("Тестирование gRPC метода FetchUsersStats")
    print("=" * 70)
    print(f"\nПодключение к {host}:{port}...")
    
    try:
        async with Channel(host, port) as channel:
            stub = MarzServiceStub(channel)
            
            print("✓ Соединение установлено")
            print("\nЗапрос статистики пользователей...\n")
            
            response = await stub.FetchUsersStats(Empty())
            
            print("=" * 70)
            print(f"Получено данных о {len(response.users_stats)} пользователях")
            print("=" * 70)
            print()
            
            if not response.users_stats:
                print("⚠ Нет активных пользователей или данных о трафике")
                return
            
            for i, user_stat in enumerate(response.users_stats, 1):
                print(f"Пользователь #{i}:")
                print(f"  User ID:        {user_stat.uid}")
                print(f"  Total Traffic:  {format_bytes(user_stat.usage)}")
                print(f"  Uplink:         {format_bytes(user_stat.uplink)}")
                print(f"  Downlink:       {format_bytes(user_stat.downlink)}")
                print(f"  IP Address:     {user_stat.remote_ip or '(не определен)'}")
                print(f"  Client:         {user_stat.client_name or '(не указан)'}")
                print()
            
            print("=" * 70)
            print("JSON представление (как видит Marzneshin):")
            print("=" * 70)
            
            json_data = {
                "users_stats": [
                    {
                        "uid": user_stat.uid,
                        "usage": user_stat.usage,
                        "uplink": user_stat.uplink,
                        "downlink": user_stat.downlink,
                        "remote_ip": user_stat.remote_ip,
                        "client_name": user_stat.client_name,
                    }
                    for user_stat in response.users_stats
                ]
            }
            
            print(json.dumps(json_data, indent=2, ensure_ascii=False))
            print()
            
            print("=" * 70)
            print("Сводная статистика:")
            print("=" * 70)
            
            total_users = len(response.users_stats)
            total_traffic = sum(u.usage for u in response.users_stats)
            users_with_ip = sum(1 for u in response.users_stats if u.remote_ip)
            
            print(f"  Всего пользователей:         {total_users}")
            print(f"  Пользователей с IP:          {users_with_ip}")
            print(f"  IP не определен:             {total_users - users_with_ip}")
            print(f"  Общий трафик:                {format_bytes(total_traffic)}")
            print()
            
            if users_with_ip < total_users:
                print("⚠ Не все пользователи имеют определенный IP адрес")
                print("  Причины: пользователь не подключался или данные устарели")
            else:
                print("✓ У всех активных пользователей определен IP адрес!")
            
            print()
            
    except Exception as e:
        print(f"\n✗ Ошибка: {e}")
        raise


def format_bytes(bytes_value):
    """Форматирует байты в человекочитаемый вид"""
    if bytes_value == 0:
        return "0 B"
    
    units = ['B', 'KB', 'MB', 'GB', 'TB']
    unit_index = 0
    value = float(bytes_value)
    
    while value >= 1024 and unit_index < len(units) - 1:
        value /= 1024
        unit_index += 1
    
    return f"{value:.2f} {units[unit_index]}"


if __name__ == "__main__":
    try:
        asyncio.run(fetch_stats())
    except KeyboardInterrupt:
        print("\n\nПрервано пользователем")
    except Exception as e:
        print(f"\n\nФатальная ошибка: {e}")
        import sys
        sys.exit(1)
PYTHON_SCRIPT

echo ""
echo "Проверка завершена!"

