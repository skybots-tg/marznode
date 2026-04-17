# Реализация блокировки устройств сверх лимита в Marznode

## 📋 Обзор

Реализована система жесткой блокировки подключений с устройств, не зарегистрированных в системе (сверх лимита), на уровне прокси-сервера.

## 🎯 Что реализовано

### 1. Расширение protobuf протокола

**Файл: `marznode/service/service.proto`**

Добавлены новые поля в сообщение `User`:

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

### 2. Модуль вычисления device fingerprint

**Файл: `marznode/utils/device_fingerprint.py`**

Реализован модуль для вычисления fingerprint устройств, совместимый с логикой Marzneshin:

- `build_device_fingerprint()` - основная функция вычисления fingerprint
- `is_device_allowed()` - проверка разрешения устройства
- `extract_device_info_from_meta()` - извлечение информации об устройстве

**Критически важно:** Алгоритм вычисления fingerprint ИДЕНТИЧЕН с `app/utils/device_fingerprint.py` в Marzneshin.

### 3. Расширение модели User

**Файл: `marznode/models/user.py`**

Добавлены новые поля:

```python
class User(BaseModel):
    id: int
    username: str
    key: str
    inbounds: list["Inbound"] = []
    
    # Device limit enforcement fields
    device_limit: Optional[int] = None  # None = no limit
    allowed_fingerprints: list[str] = Field(default_factory=list)
    enforce_device_limit: bool = False
```

### 4. Расширение DeviceStorage

**Файл: `marznode/storage/devices.py`**

Добавлена проверка fingerprint при обновлении устройства:

- `check_device_allowed()` - проверка разрешения устройства
- `update_device()` - обновлено для поддержки проверки лимитов
- `get_blocked_connections()` - получение истории блокировок
- Добавлено поле `fingerprint` в `DeviceInfo`

### 5. Интеграция в XrayBackend

**Файл: `marznode/backends/xray/xray_backend.py`**

Добавлена интеграция с системой проверки устройств:

- Инициализация `DeviceStorage` в конструкторе
- Периодическая проверка устройств (`_periodic_device_check()`)
- Логирование информации о лимитах устройств при добавлении пользователя
- Методы управления enforcement: `get_device_storage()`, `set_device_enforcement()`

### 6. Обновление service.py

**Файл: `marznode/service/service.py`**

Обновлена обработка пользователей:

- `_update_user()` - парсинг новых полей из protobuf
- `FetchUsersStats()` - проверка лимитов устройств при сохранении статистики

## 🔄 Flow работы

### 1. Синхронизация пользователя с Marzneshin

```
Marzneshin → gRPC SyncUsers/RepopulateUsers
  ↓
User {
  id: 123
  username: "john"
  key: "..."
  device_limit: 3
  allowed_fingerprints: ["fp1", "fp2", "fp3"]
  enforce_device_limit: true
}
  ↓
Marznode: сохранение в storage
```

### 2. Подключение пользователя

```
User connects → Xray → Access log
  ↓
XrayCore._handle_log_line() → extract IP
  ↓
FetchUsersStats() → collect metadata
  ↓
DeviceStorage.update_device()
  ↓
build_device_fingerprint() → calculate hash
  ↓
is_device_allowed() → check against allowed_fingerprints
  ↓
IF allowed: save device info
IF blocked: log warning, track in blocked_connections
```

### 3. Периодическая проверка (XrayBackend)

```
Every 60 seconds:
  ↓
_periodic_device_check()
  ↓
Get usages and metadata
  ↓
For each user with enforce_device_limit:
  ↓
  update_device() with enforcement
  ↓
  If not allowed: log violation
```

## 📊 Структура данных

### Пример User с лимитом устройств

```python
User(
    id=123,
    username="john_doe",
    key="a1b2c3d4...",
    device_limit=3,
    allowed_fingerprints=[
        "sha256:abc123def456...",
        "sha256:789ghi012jkl...",
        "sha256:345mno678pqr...",
    ],
    enforce_device_limit=True
)
```

### DeviceInfo с fingerprint

```python
DeviceInfo(
    remote_ip="1.2.3.4",
    client_name="v2rayNG",
    fingerprint="sha256:abc123def456...",
    first_seen=1702828800,
    last_seen=1702915200,
    total_usage=1073741824,
    is_active=True,
)
```

## 🔧 Конфигурация

### В Marzneshin (при синхронизации)

```python
# При синхронизации пользователя с узлом
from app.db import device_crud

# Получить разрешенные устройства
devices = device_crud.get_user_devices(
    db, 
    user.id, 
    is_blocked=False,
)

allowed_fingerprints = [device.fingerprint for device in devices]

# Создать UserData для отправки
user_data = UserData(
    user=User(
        id=user.id,
        username=user.username,
        key=user.key,
        device_limit=user.device_limit,
        allowed_fingerprints=allowed_fingerprints,
        enforce_device_limit=True,  # или из настроек
    ),
    inbounds=get_user_inbounds(user)
)
```

### В Marznode (активация enforcement)

```python
# В XrayBackend можно включить/выключить enforcement
backend.set_device_enforcement(enabled=True)

# Получить device storage для просмотра блокировок
device_storage = backend.get_device_storage()
blocked = device_storage.get_blocked_connections()
```

## ⚠️ Важные моменты

### 1. Синхронизация fingerprint алгоритма

**КРИТИЧНО:** Алгоритм в `marznode/utils/device_fingerprint.py::build_device_fingerprint()`
ДОЛЖЕН быть побайтно идентичен `app/utils/device_fingerprint.py` в Marzneshin.
Совместимость закреплена golden-тестами в `tests/test_device_fingerprint.py`
обоих проектов — не трогайте векторы, не синхронизировав обе стороны.

Поддерживаются две версии хеша одновременно:

**v1 (legacy, сохранён только для обратной совместимости):**
```python
components = [
    str(user_id),
    client_name or "",
    tls_fingerprint or "",
    os_guess or "",
    user_agent or "",
]
source = "|".join(components)
fingerprint = hashlib.sha256(source.encode("utf-8", errors="replace")).hexdigest()
```

У v1 есть известные недостатки, которые в v2 исправлены:

- коллизия на разделителе `|` (`("a", "b|c")` и `("a|b", "c")` дают одинаковый хеш);
- нестабильность из-за различий в регистре/пробелах `client_name` (каждое обновление клиента = новое устройство);
- любое пустое поле приводит к вкладу нулевой длины — компоненты могут быть неразличимы.

**v2 (текущий default):**
```python
payload = {
    "v": 2,
    "uid": int(user_id),
    "cn": (normalize_client_name(client_name) or "").strip(),
    "tls": (tls_fingerprint or "").strip().lower(),
    "os":  (os_guess or "").strip().lower(),
    "ua":  (user_agent or "").strip(),
}
source = json.dumps(payload, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
fingerprint = hashlib.sha256(source.encode("utf-8", errors="replace")).hexdigest()
```

Что изменилось:

- **Версия в payload** — исключает коллизии между v1 и v2 и даёт безопасное поле для будущих миграций.
- **JSON-сериализация** — разделитель экранируется, устраняется v1-коллизия.
- **`normalize_client_name`** — `v2rayNG`/`V2RAYNG`/`v2rayng` дают один fingerprint.
- **`errors="replace"`** — surrogate-символы в User-Agent больше не роняют обработчик коннекта.

### 1a. Dual-режим и миграция v1 → v2

- `build_device_fingerprint(...)` без параметра `version` возвращает **v2**.
- `build_device_fingerprint(..., version=1)` сохранён для расчёта легаси-хеша.
- `build_device_fingerprints_all(...)` возвращает `{1: hash_v1, 2: hash_v2}` — используется на точках проверки.
- `is_device_allowed` принимает как одиночный хеш, так и итерируемый набор — устройство считается разрешённым, если **любая** из версий найдена в `allowed_fingerprints`.
- В Marzneshin `device_tracker.track_user_connection` делает lazy-lookup: сначала ищет запись по v2, при промахе — по v1. Существующие v1-записи продолжают работать без ручной миграции.
- Новые записи всегда создаются как v2; v1 естественно отомрут, когда пользователь сменит клиент.

### 2. Когда синхронизировать allowed_fingerprints

Нужно обновлять список при:
- Регистрации нового устройства
- Удалении устройства (DELETE /devices/{id})
- Блокировке/разблокировке устройства
- Изменении device_limit

### 3. Обработка NULL device_limit

```python
if device_limit is None:
    # Лимит не установлен - разрешить все
    return True, "no device limit set"

if device_limit == 0:
    # Устройства запрещены полностью
    return False, "devices not allowed for this user"

# Иначе проверять fingerprint
```

### 4. Ограничение: multi-node leak лимита устройств

Проверка лимита в `is_device_allowed` на marznode **не атомарна** между узлами.
При `len(allowed_fingerprints) < device_limit` каждая marznode-нода независимо
пропускает одно «новое» устройство, прежде чем Marzneshin разошлёт обновлённый
список хешей. Фактический верхний предел — `N_нод × device_limit`.

Истинный жёсткий потолок достижим только если решение принимает Marzneshin
централизованно (текущая реализация `device_tracker.py` в Marzneshin это и
делает при создании `UserDevice`). На стороне marznode проверка остаётся
только отсекающей: блокируются устройства, которых нет в allowed-листе,
когда список уже полон.

### 5. Производительность

Реализованные оптимизации:
- Используется хеш-таблица для быстрой проверки fingerprint
- Периодическая очистка старых записей
- Ограничение размера кэша метаданных
- Асинхронная обработка

## 🧪 Тестирование

### Проверка работы fingerprint

```python
from marznode.utils.device_fingerprint import build_device_fingerprint

# Тест 1: Генерация fingerprint
fp, version = build_device_fingerprint(
    user_id=123,
    client_name="v2rayNG",
    tls_fingerprint="abc123",
)
print(f"Fingerprint: {fp}")
print(f"Version: {version}")

# Тест 2: Проверка разрешения
from marznode.utils.device_fingerprint import is_device_allowed

is_allowed, reason = is_device_allowed(
    fingerprint=fp,
    allowed_fingerprints=["other_fp", fp],
    device_limit=2,
    enforce=True,
)
print(f"Allowed: {is_allowed}, Reason: {reason}")
```

### Проверка DeviceStorage

```python
from marznode.storage.devices import DeviceStorage

storage = DeviceStorage()

# Добавление устройства с проверкой
is_allowed, reason = storage.update_device(
    uid=123,
    remote_ip="1.2.3.4",
    client_name="v2rayNG",
    current_usage=1000,
    meta={"tls_fingerprint": "abc123"},
    allowed_fingerprints=["fp1", "fp2"],
    device_limit=2,
    enforce_limit=True,
)

print(f"Device allowed: {is_allowed}, Reason: {reason}")

# Получить устройства пользователя
devices = storage.get_user_devices(123)
for device in devices:
    print(f"Device: {device.fingerprint[:16]}..., Active: {device.is_active}")
```

## 📝 Следующие шаги для полной интеграции

### В Marzneshin

1. **Модифицировать sync_user в marznode operations:**

```python
# app/marznode/operations.py
async def sync_user(node: MarzNodeBase, user: User, db: Session):
    from app.db import device_crud
    
    # Получить разрешенные устройства
    devices = device_crud.get_user_devices(
        db, 
        user.id, 
        is_blocked=False,
    )
    allowed_fingerprints = [device.fingerprint for device in devices]
    
    # Создать UserData с новыми полями
    user_data = UserData(
        user=User(
            id=user.id,
            username=user.username,
            key=user.key,
            device_limit=user.device_limit,
            allowed_fingerprints=allowed_fingerprints,
            enforce_device_limit=True,  # из настроек
        ),
        inbounds=get_user_inbounds(user)
    )
    
    await node.sync_user(user_data)
```

2. **Регенерировать protobuf файлы в Marzneshin:**

```bash
cd app/marznode
python -m grpc_tools.protoc -I. \
    --python_out=. \
    --grpc_python_out=. \
    --pyi_out=. \
    service.proto
```

3. **Добавить автоматическую синхронизацию при изменении устройств:**

- При регистрации устройства → sync_user
- При удалении устройства → sync_user
- При блокировке/разблокировке → sync_user

## 🐛 Отладка

### Включить подробное логирование

В файле конфигурации или через переменные окружения:

```bash
# Marznode
export LOG_LEVEL=DEBUG

# Логи будут показывать:
# - Генерацию fingerprints
# - Проверки устройств
# - Блокировки подключений
# - Обновления allowed_fingerprints
```

### Просмотр заблокированных устройств

```python
# Через gRPC API или прямой доступ к DeviceStorage
blocked = device_storage.get_blocked_connections(uid=123)
for fingerprint in blocked:
    print(f"Blocked: {fingerprint[:16]}...")
```

## 📖 API для получения информации об устройствах

Уже реализованы gRPC методы:

```protobuf
service MarzService {
  // Получить устройства пользователя
  rpc FetchUserDevices(UserDevicesRequest) returns (UserDevicesHistory);
  
  // Получить все устройства
  rpc FetchAllDevices(Empty) returns (AllUsersDevices);
}
```

Использование в Marzneshin:

```python
# Получить устройства пользователя
devices = await node.fetch_user_devices(uid=123, active_only=True)
for device in devices:
    print(f"IP: {device.remote_ip}, Active: {device.is_active}")
```

## ✅ Чеклист интеграции

- [x] Расширен protobuf протокол (User message)
- [x] Добавлен модуль device_fingerprint.py
- [x] Расширена модель User в marznode
- [x] Обновлен DeviceStorage с проверкой fingerprints
- [x] Интегрирована проверка в XrayBackend
- [x] Регенерированы protobuf файлы
- [x] Обновлен service.py для обработки новых полей
- [ ] Обновлен Marzneshin для отправки device_limit и allowed_fingerprints
- [ ] Протестирована работа блокировки на реальных данных
- [ ] Добавлены метрики и мониторинг блокировок

## 🔗 Связанные файлы

- `marznode/service/service.proto` - protobuf схема
- `marznode/utils/device_fingerprint.py` - вычисление fingerprint
- `marznode/models/user.py` - модель User
- `marznode/storage/devices.py` - хранилище устройств
- `marznode/backends/xray/xray_backend.py` - интеграция в Xray
- `marznode/service/service.py` - gRPC service
- `marznode/backends/xray/_runner.py` - парсинг логов Xray

## 🆘 Поддержка

При возникновении проблем:

1. Проверьте логи marznode (должны быть WARNING о блокировках)
2. Убедитесь, что fingerprint алгоритм идентичен в marznode и Marzneshin
3. Проверьте, что allowed_fingerprints обновляется при изменении устройств
4. Убедитесь, что enforce_device_limit=True для пользователя






