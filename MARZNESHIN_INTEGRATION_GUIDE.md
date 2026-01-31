# Руководство по интеграции с Marzneshin

Это руководство описывает, какие изменения нужно внести в Marzneshin для полной интеграции с системой блокировки устройств marznode.

## 📋 Предварительные требования

1. Marznode обновлен и содержит новые поля в protobuf
2. Device tracking уже работает в Marzneshin (таблица `devices`)
3. У пользователей есть поле `device_limit`

## 🔧 Шаг 1: Обновление protobuf схемы

### 1.1. Скопировать обновленный proto файл

```bash
# Скопировать из marznode в marzneshin
cp marznode/service/service.proto app/marznode/service/
```

Или вручную обновить `app/marznode/service/service.proto`:

```protobuf
message User {
  uint32 id = 1;
  string username = 2;
  string key = 3;
  
  // ========== Device limit enforcement fields ==========
  optional uint32 device_limit = 4;           // Device limit (null = no limit)
  repeated string allowed_fingerprints = 5;   // List of allowed device fingerprints
  bool enforce_device_limit = 6;              // Enable device limit enforcement at proxy level
  // ====================================================
}
```

### 1.2. Регенерировать protobuf файлы

```bash
cd app/marznode
python -m grpc_tools.protoc -I. \
    --python_out=. \
    --grpc_python_out=. \
    --pyi_out=. \
    service/service.proto
```

Или используйте существующий скрипт, если он есть:

```bash
./regenerate_proto.sh
```

## 🔧 Шаг 2: Обновление операций синхронизации

### 2.1. Модифицировать функцию sync_user

**Файл: `app/marznode/operations.py`** (или аналогичный)

```python
from sqlalchemy.orm import Session
from app.db import device_crud
from app.config import ENFORCE_DEVICE_LIMITS_ON_PROXY

async def sync_user_with_device_limits(
    node: MarzNodeBase,
    user: User,
    db: Session
) -> None:
    """
    Синхронизировать пользователя с узлом, включая device limits.
    
    Args:
        node: MarzNode instance
        user: User model from database
        db: Database session
    """
    from app.marznode.service.service_pb2 import User as UserProto, UserData
    
    # Получить список незаблокированных устройств пользователя
    devices = device_crud.get_user_devices(
        db=db,
        user_id=user.id,
        is_blocked=False,  # Только незаблокированные
        limit=1000  # Разумный лимит для производительности
    )
    
    # Собрать fingerprints разрешенных устройств
    allowed_fingerprints = [device.fingerprint for device in devices if device.fingerprint]
    
    # Проверка: если у пользователя есть лимит, но нет зарегистрированных устройств
    if user.device_limit and user.device_limit > 0 and not allowed_fingerprints:
        logger.warning(
            f"User {user.username} (id={user.id}) has device_limit={user.device_limit} "
            f"but no registered devices. Allowing connections until devices are registered."
        )
    
    # Создать protobuf сообщение с device limits
    user_proto = UserProto(
        id=user.id,
        username=user.username,
        key=user.key,
        # ========== Device limit fields ==========
        device_limit=user.device_limit if user.device_limit is not None else None,
        allowed_fingerprints=allowed_fingerprints,
        enforce_device_limit=ENFORCE_DEVICE_LIMITS_ON_PROXY and user.device_limit is not None,
        # =========================================
    )
    
    # Получить inbounds для пользователя
    inbounds = get_user_inbounds(user)  # Ваша существующая логика
    
    # Создать UserData
    user_data = UserData(
        user=user_proto,
        inbounds=inbounds
    )
    
    # Отправить на узел
    try:
        await node.sync_user(user_data)
        logger.info(
            f"Synced user {user.username} (id={user.id}) with device enforcement: "
            f"limit={user.device_limit}, allowed_devices={len(allowed_fingerprints)}, "
            f"enforce={user_proto.enforce_device_limit}"
        )
    except Exception as e:
        logger.error(f"Failed to sync user {user.username} with node: {e}")
        raise
```

### 2.2. Обновить существующую функцию sync_user

Если у вас уже есть `sync_user`, обновите её:

```python
async def sync_user(node: MarzNodeBase, user: User, db: Session) -> None:
    """Синхронизировать пользователя с узлом"""
    # Ваша существующая логика...
    
    # ДОБАВИТЬ: Получение device limits
    devices = device_crud.get_user_devices(
        db=db,
        user_id=user.id,
        is_blocked=False,
    )
    allowed_fingerprints = [d.fingerprint for d in devices if d.fingerprint]
    
    # ИЗМЕНИТЬ: Создание User proto с новыми полями
    user_proto = UserProto(
        id=user.id,
        username=user.username,
        key=user.key,
        device_limit=user.device_limit,
        allowed_fingerprints=allowed_fingerprints,
        enforce_device_limit=True,  # Или из настроек
    )
    
    # Остальная логика...
```

## 🔧 Шаг 3: Триггеры синхронизации

### 3.1. При изменении устройств

Добавьте автоматическую синхронизацию при операциях с устройствами:

```python
# app/api/endpoints/devices.py

@router.post("/devices/{device_id}/block")
async def block_device(
    device_id: int,
    db: Session = Depends(get_db),
    admin: Admin = Depends(get_current_admin),
):
    """Заблокировать устройство"""
    device = device_crud.get_device(db, device_id)
    if not device:
        raise HTTPException(status_code=404, detail="Device not found")
    
    # Блокировать устройство
    device = device_crud.block_device(db, device_id)
    
    # ДОБАВИТЬ: Синхронизировать пользователя со всеми узлами
    user = crud.get_user(db, device.user_id)
    if user:
        await sync_user_with_all_nodes(user, db)
        logger.info(
            f"User {user.username} synced with nodes after blocking device {device_id}"
        )
    
    return device


@router.delete("/devices/{device_id}")
async def delete_device(
    device_id: int,
    db: Session = Depends(get_db),
    admin: Admin = Depends(get_current_admin),
):
    """Удалить устройство"""
    device = device_crud.get_device(db, device_id)
    if not device:
        raise HTTPException(status_code=404, detail="Device not found")
    
    user_id = device.user_id
    
    # Удалить устройство
    device_crud.delete_device(db, device_id)
    
    # ДОБАВИТЬ: Синхронизировать пользователя
    user = crud.get_user(db, user_id)
    if user:
        await sync_user_with_all_nodes(user, db)
        logger.info(
            f"User {user.username} synced with nodes after deleting device {device_id}"
        )
    
    return {"success": True}


@router.post("/devices/{device_id}/unblock")
async def unblock_device(
    device_id: int,
    db: Session = Depends(get_db),
    admin: Admin = Depends(get_current_admin),
):
    """Разблокировать устройство"""
    device = device_crud.get_device(db, device_id)
    if not device:
        raise HTTPException(status_code=404, detail="Device not found")
    
    # Разблокировать устройство
    device = device_crud.unblock_device(db, device_id)
    
    # ДОБАВИТЬ: Синхронизировать пользователя
    user = crud.get_user(db, device.user_id)
    if user:
        await sync_user_with_all_nodes(user, db)
        logger.info(
            f"User {user.username} synced with nodes after unblocking device {device_id}"
        )
    
    return device
```

### 3.2. Вспомогательная функция синхронизации

```python
# app/marznode/operations.py

async def sync_user_with_all_nodes(user: User, db: Session) -> None:
    """
    Синхронизировать пользователя со всеми узлами.
    
    Используется при изменении device limits или списка устройств.
    """
    from app.marznode import manager as node_manager
    
    # Получить все активные узлы
    nodes = await node_manager.get_all_nodes()
    
    sync_results = []
    for node_id, node in nodes.items():
        try:
            await sync_user_with_device_limits(node, user, db)
            sync_results.append((node_id, True, None))
        except Exception as e:
            logger.error(f"Failed to sync user {user.username} with node {node_id}: {e}")
            sync_results.append((node_id, False, str(e)))
    
    # Логировать результаты
    success_count = sum(1 for _, success, _ in sync_results if success)
    logger.info(
        f"Synced user {user.username} with {success_count}/{len(nodes)} nodes"
    )
    
    if success_count < len(nodes):
        failed_nodes = [node_id for node_id, success, _ in sync_results if not success]
        logger.warning(
            f"Failed to sync user {user.username} with nodes: {failed_nodes}"
        )
```

### 3.3. При изменении device_limit пользователя

```python
# app/api/endpoints/users.py

@router.put("/users/{user_id}")
async def update_user(
    user_id: int,
    user_update: UserUpdate,
    db: Session = Depends(get_db),
    admin: Admin = Depends(get_current_admin),
):
    """Обновить пользователя"""
    user = crud.get_user(db, user_id)
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    
    # Проверить изменение device_limit
    device_limit_changed = (
        hasattr(user_update, 'device_limit') and
        user_update.device_limit != user.device_limit
    )
    
    # Обновить пользователя
    user = crud.update_user(db, user_id, user_update)
    
    # ДОБАВИТЬ: Синхронизировать если изменился device_limit
    if device_limit_changed:
        await sync_user_with_all_nodes(user, db)
        logger.info(
            f"User {user.username} synced with nodes after device_limit change: "
            f"new_limit={user.device_limit}"
        )
    
    return user
```

## 🔧 Шаг 4: Настройки конфигурации

### 4.1. Добавить в .env

```bash
# Device limit enforcement
# Включить проверку лимитов устройств на уровне прокси-сервера (marznode)
ENFORCE_DEVICE_LIMITS_ON_PROXY=true

# Интервал синхронизации device limits (секунды)
DEVICE_LIMIT_SYNC_INTERVAL=300
```

### 4.2. Обновить config.py

```python
# app/config/env.py

from decouple import config

# Device limits enforcement
ENFORCE_DEVICE_LIMITS_ON_PROXY = config(
    "ENFORCE_DEVICE_LIMITS_ON_PROXY",
    default=True,
    cast=bool
)

DEVICE_LIMIT_SYNC_INTERVAL = config(
    "DEVICE_LIMIT_SYNC_INTERVAL",
    default=300,
    cast=int
)
```

## 🔧 Шаг 5: Периодическая синхронизация

Добавьте периодическую задачу для синхронизации (опционально):

```python
# app/tasks/device_limits.py

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from sqlalchemy.orm import Session
from app.db import get_db
from app.config import DEVICE_LIMIT_SYNC_INTERVAL

async def periodic_device_limits_sync():
    """
    Периодическая синхронизация device limits для всех пользователей.
    
    Используется как fallback на случай пропущенных обновлений.
    """
    from app.db import crud, device_crud
    from app.marznode import manager as node_manager
    
    logger.info("Starting periodic device limits sync")
    
    with next(get_db()) as db:
        # Получить всех пользователей с device_limit
        users = crud.get_users_with_device_limit(db)
        logger.info(f"Found {len(users)} users with device limits")
        
        for user in users:
            try:
                await sync_user_with_all_nodes(user, db)
            except Exception as e:
                logger.error(
                    f"Failed to sync user {user.username} during periodic sync: {e}"
                )
    
    logger.info("Periodic device limits sync completed")


def setup_device_limits_scheduler():
    """Настроить scheduler для периодической синхронизации"""
    scheduler = AsyncIOScheduler()
    
    scheduler.add_job(
        periodic_device_limits_sync,
        'interval',
        seconds=DEVICE_LIMIT_SYNC_INTERVAL,
        id='device_limits_sync',
        replace_existing=True,
    )
    
    scheduler.start()
    logger.info(
        f"Device limits sync scheduler started (interval: {DEVICE_LIMIT_SYNC_INTERVAL}s)"
    )
    
    return scheduler
```

В `app/main.py`:

```python
from app.tasks.device_limits import setup_device_limits_scheduler

@app.on_event("startup")
async def startup_event():
    # Ваш существующий код...
    
    # ДОБАВИТЬ: Запустить scheduler
    if config.ENFORCE_DEVICE_LIMITS_ON_PROXY:
        setup_device_limits_scheduler()
```

## 🧪 Шаг 6: Тестирование интеграции

### 6.1. Unit тест

```python
# tests/test_device_limits.py

import pytest
from app.marznode.operations import sync_user_with_device_limits
from app.db import device_crud

@pytest.mark.asyncio
async def test_sync_user_with_device_limits(db_session, mock_node, test_user):
    """Тест синхронизации пользователя с device limits"""
    
    # Создать устройства для пользователя
    device1 = device_crud.create_device(
        db_session,
        user_id=test_user.id,
        fingerprint="fp1",
        is_blocked=False
    )
    device2 = device_crud.create_device(
        db_session,
        user_id=test_user.id,
        fingerprint="fp2",
        is_blocked=True  # Заблокировано
    )
    
    # Установить device_limit
    test_user.device_limit = 2
    db_session.commit()
    
    # Синхронизировать
    await sync_user_with_device_limits(mock_node, test_user, db_session)
    
    # Проверить, что узел получил правильные данные
    assert mock_node.last_sync_call is not None
    user_proto = mock_node.last_sync_call.user
    
    assert user_proto.device_limit == 2
    assert len(user_proto.allowed_fingerprints) == 1  # Только незаблокированные
    assert "fp1" in user_proto.allowed_fingerprints
    assert "fp2" not in user_proto.allowed_fingerprints
    assert user_proto.enforce_device_limit is True
```

### 6.2. Integration тест

```python
@pytest.mark.asyncio
async def test_block_device_triggers_sync(
    client, db_session, test_user, test_device, mock_node
):
    """Тест автоматической синхронизации при блокировке устройства"""
    
    # Заблокировать устройство через API
    response = await client.post(f"/api/devices/{test_device.id}/block")
    assert response.status_code == 200
    
    # Проверить, что произошла синхронизация с узлом
    assert mock_node.sync_count > 0
    
    # Проверить, что устройство не в списке allowed_fingerprints
    user_proto = mock_node.last_sync_call.user
    assert test_device.fingerprint not in user_proto.allowed_fingerprints
```

## 📊 Шаг 7: Мониторинг и логи

### 7.1. Добавить метрики

```python
# app/utils/metrics.py

from prometheus_client import Counter, Histogram

device_limit_syncs_total = Counter(
    'device_limit_syncs_total',
    'Total device limit syncs',
    ['status']  # success, failure
)

device_limit_sync_duration = Histogram(
    'device_limit_sync_duration_seconds',
    'Device limit sync duration'
)
```

Использовать в sync функции:

```python
with device_limit_sync_duration.time():
    try:
        await sync_user_with_device_limits(node, user, db)
        device_limit_syncs_total.labels(status='success').inc()
    except Exception as e:
        device_limit_syncs_total.labels(status='failure').inc()
        raise
```

### 7.2. Логирование

Добавить структурированное логирование:

```python
import logging
import structlog

logger = structlog.get_logger(__name__)

async def sync_user_with_device_limits(...):
    logger.info(
        "syncing_user_with_device_limits",
        user_id=user.id,
        username=user.username,
        device_limit=user.device_limit,
        allowed_devices_count=len(allowed_fingerprints),
    )
    
    # ...
    
    logger.info(
        "sync_completed",
        user_id=user.id,
        node_id=node.id,
        enforce_enabled=user_proto.enforce_device_limit,
    )
```

## ✅ Чеклист интеграции

- [ ] Обновлен protobuf файл `service.proto`
- [ ] Регенерированы protobuf файлы (service_pb2.py и др.)
- [ ] Обновлена функция `sync_user` с device limits
- [ ] Добавлены триггеры синхронизации:
  - [ ] При блокировке устройства
  - [ ] При разблокировке устройства
  - [ ] При удалении устройства
  - [ ] При изменении device_limit
- [ ] Добавлена вспомогательная функция `sync_user_with_all_nodes`
- [ ] Добавлены настройки в `.env` и `config.py`
- [ ] Настроена периодическая синхронизация (опционально)
- [ ] Написаны тесты
- [ ] Добавлен мониторинг и логирование
- [ ] Проверена работа на тестовом окружении

## 🐛 Troubleshooting

### Проблема: Устройства не блокируются

**Проверить:**
1. `ENFORCE_DEVICE_LIMITS_ON_PROXY=true` в .env
2. У пользователя `device_limit` установлен
3. Синхронизация происходит после изменения устройств
4. Логи marznode показывают получение новых `allowed_fingerprints`

### Проблема: Fingerprints не совпадают

**Проверить:**
1. Алгоритм в marznode и marzneshin ИДЕНТИЧЕН
2. Используется одинаковая версия (version=1)
3. Компоненты fingerprint собираются в правильном порядке

### Проблема: Синхронизация не происходит

**Проверить:**
1. Триггеры правильно вызываются
2. Нет ошибок в логах при вызове `sync_user`
3. Соединение с marznode активно
4. gRPC порты доступны

## 📚 Дополнительные ресурсы

- Полная документация: `DEVICE_LIMIT_IMPLEMENTATION.md`
- Резюме изменений: `CHANGES_SUMMARY.md`
- Device fingerprint модуль: `marznode/utils/device_fingerprint.py`

---

**Автор:** AI Assistant  
**Дата:** 2025-12-17  
**Версия:** 1.0






