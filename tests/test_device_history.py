"""uid 0 — не «все юзеры».

На выходной ноде 44 в инбаунде есть статичный клиент "0.bridge" из конфига
xray, не юзер панели. Его uid 0 доходил до list_users(0), а та проверяла
`if user_id:` и отдавала список всех юзеров; история устройств падала на
`'list' object has no attribute 'allowed_fingerprints'` каждые 30 секунд.
"""

from __future__ import annotations

import asyncio
import logging

from marznode.models import User
from marznode.service._device_history import record_device_history
from marznode.storage import DeviceStorage, MemoryStorage


async def _storage(*uids: int) -> MemoryStorage:
    storage = MemoryStorage()
    for uid in uids:
        await storage.update_user_inbounds(
            User(id=uid, username=f"user{uid}", key=f"key{uid}"), []
        )
    return storage


def _meta(ip: str) -> dict[str, str]:
    return {"remote_ip": ip, "client_name": "xray"}


def test_list_users_zero_is_a_lookup_not_all_users():
    storage = asyncio.run(_storage(1, 2))

    assert asyncio.run(storage.list_users(0)) is None
    assert asyncio.run(storage.list_users(1)).id == 1
    assert {u.id for u in asyncio.run(storage.list_users())} == {1, 2}
    assert {u.id for u in asyncio.run(storage.list_users(None))} == {1, 2}


def test_list_users_finds_a_user_with_id_zero():
    storage = asyncio.run(_storage(0, 1))

    assert asyncio.run(storage.list_users(0)).id == 0


def test_bridge_uid_zero_does_not_break_device_history(caplog):
    storage = asyncio.run(_storage(1, 2))
    devices = DeviceStorage()

    with caplog.at_level(logging.ERROR):
        asyncio.run(
            record_device_history(
                devices,
                storage,
                total_usage={0: 100, 1: 200},
                meta={0: _meta("10.0.0.1"), 1: _meta("10.0.0.2")},
            )
        )

    assert not [r for r in caplog.records if r.levelno >= logging.ERROR]
    assert devices.get_user_devices(0) == []
    assert [d.remote_ip for d in devices.get_user_devices(1)] == ["10.0.0.2"]
