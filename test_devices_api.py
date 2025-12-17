#!/usr/bin/env python3
"""
Тестовый скрипт для проверки новых методов API истории устройств
"""

import asyncio
import sys
from grpclib.client import Channel
from marznode.service.service_grpc import MarzServiceStub
from marznode.service.service_pb2 import Empty, UserDevicesRequest


async def test_fetch_user_devices(stub, uid: int, active_only: bool = False):
    """Тест получения устройств пользователя"""
    print(f"\n=== Testing FetchUserDevices for user {uid} (active_only={active_only}) ===")
    
    try:
        response = await stub.FetchUserDevices(
            UserDevicesRequest(uid=uid, active_only=active_only)
        )
        
        print(f"User ID: {response.uid}")
        print(f"Total devices: {len(response.devices)}")
        
        for idx, device in enumerate(response.devices, 1):
            print(f"\nDevice {idx}:")
            print(f"  Remote IP: {device.remote_ip}")
            print(f"  Client Name: {device.client_name}")
            print(f"  User Agent: {device.user_agent}")
            print(f"  Protocol: {device.protocol}")
            print(f"  TLS Fingerprint: {device.tls_fingerprint}")
            print(f"  First Seen: {device.first_seen}")
            print(f"  Last Seen: {device.last_seen}")
            print(f"  Total Usage: {device.total_usage} bytes")
            print(f"  Uplink: {device.uplink} bytes")
            print(f"  Downlink: {device.downlink} bytes")
            print(f"  Is Active: {device.is_active}")
        
        return response
    except Exception as e:
        print(f"Error: {e}")
        import traceback
        traceback.print_exc()
        return None


async def test_fetch_all_devices(stub):
    """Тест получения всех устройств"""
    print("\n=== Testing FetchAllDevices ===")
    
    try:
        response = await stub.FetchAllDevices(Empty())
        
        print(f"Total users with devices: {len(response.users)}")
        total_devices = sum(len(user.devices) for user in response.users)
        print(f"Total devices across all users: {total_devices}")
        
        for user_history in response.users:
            print(f"\nUser ID: {user_history.uid}")
            print(f"  Devices: {len(user_history.devices)}")
            
            for idx, device in enumerate(user_history.devices, 1):
                status = "🟢 Active" if device.is_active else "🔴 Inactive"
                print(f"    {idx}. {device.remote_ip} ({device.client_name}) - {status}")
                print(f"       Usage: {device.total_usage} bytes")
        
        return response
    except Exception as e:
        print(f"Error: {e}")
        import traceback
        traceback.print_exc()
        return None


async def main():
    """Основная функция"""
    host = sys.argv[1] if len(sys.argv) > 1 else "localhost"
    port = int(sys.argv[2]) if len(sys.argv) > 2 else 53042
    
    print(f"Connecting to {host}:{port}")
    
    # Создаём insecure соединение (для тестирования)
    # В продакшене нужно использовать SSL
    channel = Channel(host, port)
    stub = MarzServiceStub(channel)
    
    try:
        # Тест 1: Получить все устройства
        await test_fetch_all_devices(stub)
        
        # Тест 2: Получить устройства конкретного пользователя
        # (замените 1 на реальный UID пользователя)
        if len(sys.argv) > 3:
            uid = int(sys.argv[3])
            await test_fetch_user_devices(stub, uid, active_only=False)
            await test_fetch_user_devices(stub, uid, active_only=True)
        else:
            print("\n\nТип: python test_devices_api.py [host] [port] [user_id]")
            print("Пример: python test_devices_api.py localhost 53042 1")
    
    finally:
        channel.close()


if __name__ == "__main__":
    asyncio.run(main())

