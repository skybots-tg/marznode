#!/usr/bin/env python3
"""
Тестовый скрипт для проверки статистики через gRPC
Показывает, какие данные отдает marznode
"""

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
        # Подключаемся к marznode
        async with Channel(host, port) as channel:
            stub = MarzServiceStub(channel)
            
            print("✓ Соединение установлено")
            print("\nЗапрос статистики пользователей...\n")
            
            # Запрашиваем статистику
            response = await stub.FetchUsersStats(Empty())
            
            print("=" * 70)
            print(f"Получено данных о {len(response.users_stats)} пользователях")
            print("=" * 70)
            print()
            
            if not response.users_stats:
                print("⚠ Нет активных пользователей или данных о трафике")
                return
            
            # Выводим данные по каждому пользователю
            for i, user_stat in enumerate(response.users_stats, 1):
                print(f"Пользователь #{i}:")
                print(f"  User ID:        {user_stat.uid}")
                print(f"  Total Traffic:  {format_bytes(user_stat.usage)}")
                print(f"  Uplink:         {format_bytes(user_stat.uplink)}")
                print(f"  Downlink:       {format_bytes(user_stat.downlink)}")
                print(f"  IP Address:     {user_stat.remote_ip or '(не определен)'}")
                print(f"  Client:         {user_stat.client_name or '(не указан)'}")
                print(f"  User Agent:     {user_stat.user_agent or '(не доступен)'}")
                print(f"  Protocol:       {user_stat.protocol or '(не указан)'}")
                print(f"  TLS FP:         {user_stat.tls_fingerprint or '(не указан)'}")
                print()
            
            # JSON представление
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
                        "user_agent": user_stat.user_agent,
                        "protocol": user_stat.protocol,
                        "tls_fingerprint": user_stat.tls_fingerprint,
                    }
                    for user_stat in response.users_stats
                ]
            }
            
            print(json.dumps(json_data, indent=2, ensure_ascii=False))
            print()
            
            # Статистика
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
            print(f"  Средний трафик на человека:  {format_bytes(total_traffic // total_users if total_users > 0 else 0)}")
            print()
            
            if users_with_ip < total_users:
                print("⚠ Не все пользователи имеют определенный IP адрес")
                print("  Возможные причины:")
                print("  - Пользователь еще не подключался")
                print("  - Данные устарели (TTL истек)")
                print("  - Access логи не записываются")
            else:
                print("✓ У всех активных пользователей определен IP адрес!")
            
            print()
            
    except Exception as e:
        print(f"\n✗ Ошибка: {e}")
        print("\nВозможные причины:")
        print("  1. Marznode не запущен")
        print("  2. Неверный host/port")
        print("  3. Проблемы с SSL сертификатами")
        print("  4. Firewall блокирует соединение")
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
    import sys
    
    # Параметры по умолчанию
    host = "127.0.0.1"
    port = 53042
    
    # Можно передать параметры из командной строки
    if len(sys.argv) > 1:
        host = sys.argv[1]
    if len(sys.argv) > 2:
        port = int(sys.argv[2])
    
    try:
        asyncio.run(fetch_stats(host, port))
    except KeyboardInterrupt:
        print("\n\nПрервано пользователем")
    except Exception as e:
        print(f"\n\nФатальная ошибка: {e}")
        sys.exit(1)

