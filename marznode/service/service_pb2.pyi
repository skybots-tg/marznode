from google.protobuf.internal import containers as _containers
from google.protobuf.internal import enum_type_wrapper as _enum_type_wrapper
from google.protobuf import descriptor as _descriptor
from google.protobuf import message as _message
from typing import ClassVar as _ClassVar, Iterable as _Iterable, Mapping as _Mapping, Optional as _Optional, Union as _Union

DESCRIPTOR: _descriptor.FileDescriptor

class ConfigFormat(int, metaclass=_enum_type_wrapper.EnumTypeWrapper):
    __slots__ = ()
    PLAIN: _ClassVar[ConfigFormat]
    JSON: _ClassVar[ConfigFormat]
    YAML: _ClassVar[ConfigFormat]
PLAIN: ConfigFormat
JSON: ConfigFormat
YAML: ConfigFormat

class Empty(_message.Message):
    __slots__ = ()
    def __init__(self) -> None: ...

class Backend(_message.Message):
    __slots__ = ("name", "type", "version", "inbounds")
    NAME_FIELD_NUMBER: _ClassVar[int]
    TYPE_FIELD_NUMBER: _ClassVar[int]
    VERSION_FIELD_NUMBER: _ClassVar[int]
    INBOUNDS_FIELD_NUMBER: _ClassVar[int]
    name: str
    type: str
    version: str
    inbounds: _containers.RepeatedCompositeFieldContainer[Inbound]
    def __init__(self, name: _Optional[str] = ..., type: _Optional[str] = ..., version: _Optional[str] = ..., inbounds: _Optional[_Iterable[_Union[Inbound, _Mapping]]] = ...) -> None: ...

class BackendsResponse(_message.Message):
    __slots__ = ("backends",)
    BACKENDS_FIELD_NUMBER: _ClassVar[int]
    backends: _containers.RepeatedCompositeFieldContainer[Backend]
    def __init__(self, backends: _Optional[_Iterable[_Union[Backend, _Mapping]]] = ...) -> None: ...

class Inbound(_message.Message):
    __slots__ = ("tag", "config")
    TAG_FIELD_NUMBER: _ClassVar[int]
    CONFIG_FIELD_NUMBER: _ClassVar[int]
    tag: str
    config: str
    def __init__(self, tag: _Optional[str] = ..., config: _Optional[str] = ...) -> None: ...

class User(_message.Message):
    __slots__ = ("id", "username", "key", "device_limit", "allowed_fingerprints", "enforce_device_limit")
    ID_FIELD_NUMBER: _ClassVar[int]
    USERNAME_FIELD_NUMBER: _ClassVar[int]
    KEY_FIELD_NUMBER: _ClassVar[int]
    DEVICE_LIMIT_FIELD_NUMBER: _ClassVar[int]
    ALLOWED_FINGERPRINTS_FIELD_NUMBER: _ClassVar[int]
    ENFORCE_DEVICE_LIMIT_FIELD_NUMBER: _ClassVar[int]
    id: int
    username: str
    key: str
    device_limit: int
    allowed_fingerprints: _containers.RepeatedScalarFieldContainer[str]
    enforce_device_limit: bool
    def __init__(self, id: _Optional[int] = ..., username: _Optional[str] = ..., key: _Optional[str] = ..., device_limit: _Optional[int] = ..., allowed_fingerprints: _Optional[_Iterable[str]] = ..., enforce_device_limit: bool = ...) -> None: ...

class UserData(_message.Message):
    __slots__ = ("user", "inbounds")
    USER_FIELD_NUMBER: _ClassVar[int]
    INBOUNDS_FIELD_NUMBER: _ClassVar[int]
    user: User
    inbounds: _containers.RepeatedCompositeFieldContainer[Inbound]
    def __init__(self, user: _Optional[_Union[User, _Mapping]] = ..., inbounds: _Optional[_Iterable[_Union[Inbound, _Mapping]]] = ...) -> None: ...

class UsersData(_message.Message):
    __slots__ = ("users_data",)
    USERS_DATA_FIELD_NUMBER: _ClassVar[int]
    users_data: _containers.RepeatedCompositeFieldContainer[UserData]
    def __init__(self, users_data: _Optional[_Iterable[_Union[UserData, _Mapping]]] = ...) -> None: ...

class UsersStats(_message.Message):
    __slots__ = ("users_stats",)
    class UserStats(_message.Message):
        __slots__ = ("uid", "usage", "remote_ip", "client_name", "user_agent", "uplink", "downlink", "protocol", "tls_fingerprint")
        UID_FIELD_NUMBER: _ClassVar[int]
        USAGE_FIELD_NUMBER: _ClassVar[int]
        REMOTE_IP_FIELD_NUMBER: _ClassVar[int]
        CLIENT_NAME_FIELD_NUMBER: _ClassVar[int]
        USER_AGENT_FIELD_NUMBER: _ClassVar[int]
        UPLINK_FIELD_NUMBER: _ClassVar[int]
        DOWNLINK_FIELD_NUMBER: _ClassVar[int]
        PROTOCOL_FIELD_NUMBER: _ClassVar[int]
        TLS_FINGERPRINT_FIELD_NUMBER: _ClassVar[int]
        uid: int
        usage: int
        remote_ip: str
        client_name: str
        user_agent: str
        uplink: int
        downlink: int
        protocol: str
        tls_fingerprint: str
        def __init__(self, uid: _Optional[int] = ..., usage: _Optional[int] = ..., remote_ip: _Optional[str] = ..., client_name: _Optional[str] = ..., user_agent: _Optional[str] = ..., uplink: _Optional[int] = ..., downlink: _Optional[int] = ..., protocol: _Optional[str] = ..., tls_fingerprint: _Optional[str] = ...) -> None: ...
    USERS_STATS_FIELD_NUMBER: _ClassVar[int]
    users_stats: _containers.RepeatedCompositeFieldContainer[UsersStats.UserStats]
    def __init__(self, users_stats: _Optional[_Iterable[_Union[UsersStats.UserStats, _Mapping]]] = ...) -> None: ...

class LogLine(_message.Message):
    __slots__ = ("line",)
    LINE_FIELD_NUMBER: _ClassVar[int]
    line: str
    def __init__(self, line: _Optional[str] = ...) -> None: ...

class BackendConfig(_message.Message):
    __slots__ = ("configuration", "config_format")
    CONFIGURATION_FIELD_NUMBER: _ClassVar[int]
    CONFIG_FORMAT_FIELD_NUMBER: _ClassVar[int]
    configuration: str
    config_format: ConfigFormat
    def __init__(self, configuration: _Optional[str] = ..., config_format: _Optional[_Union[ConfigFormat, str]] = ...) -> None: ...

class BackendLogsRequest(_message.Message):
    __slots__ = ("backend_name", "include_buffer")
    BACKEND_NAME_FIELD_NUMBER: _ClassVar[int]
    INCLUDE_BUFFER_FIELD_NUMBER: _ClassVar[int]
    backend_name: str
    include_buffer: bool
    def __init__(self, backend_name: _Optional[str] = ..., include_buffer: bool = ...) -> None: ...

class RestartBackendRequest(_message.Message):
    __slots__ = ("backend_name", "config")
    BACKEND_NAME_FIELD_NUMBER: _ClassVar[int]
    CONFIG_FIELD_NUMBER: _ClassVar[int]
    backend_name: str
    config: BackendConfig
    def __init__(self, backend_name: _Optional[str] = ..., config: _Optional[_Union[BackendConfig, _Mapping]] = ...) -> None: ...

class BackendStats(_message.Message):
    __slots__ = ("running",)
    RUNNING_FIELD_NUMBER: _ClassVar[int]
    running: bool
    def __init__(self, running: bool = ...) -> None: ...

class DeviceInfo(_message.Message):
    __slots__ = ("remote_ip", "client_name", "user_agent", "protocol", "tls_fingerprint", "first_seen", "last_seen", "total_usage", "uplink", "downlink", "is_active")
    REMOTE_IP_FIELD_NUMBER: _ClassVar[int]
    CLIENT_NAME_FIELD_NUMBER: _ClassVar[int]
    USER_AGENT_FIELD_NUMBER: _ClassVar[int]
    PROTOCOL_FIELD_NUMBER: _ClassVar[int]
    TLS_FINGERPRINT_FIELD_NUMBER: _ClassVar[int]
    FIRST_SEEN_FIELD_NUMBER: _ClassVar[int]
    LAST_SEEN_FIELD_NUMBER: _ClassVar[int]
    TOTAL_USAGE_FIELD_NUMBER: _ClassVar[int]
    UPLINK_FIELD_NUMBER: _ClassVar[int]
    DOWNLINK_FIELD_NUMBER: _ClassVar[int]
    IS_ACTIVE_FIELD_NUMBER: _ClassVar[int]
    remote_ip: str
    client_name: str
    user_agent: str
    protocol: str
    tls_fingerprint: str
    first_seen: int
    last_seen: int
    total_usage: int
    uplink: int
    downlink: int
    is_active: bool
    def __init__(self, remote_ip: _Optional[str] = ..., client_name: _Optional[str] = ..., user_agent: _Optional[str] = ..., protocol: _Optional[str] = ..., tls_fingerprint: _Optional[str] = ..., first_seen: _Optional[int] = ..., last_seen: _Optional[int] = ..., total_usage: _Optional[int] = ..., uplink: _Optional[int] = ..., downlink: _Optional[int] = ..., is_active: bool = ...) -> None: ...

class UserDevicesHistory(_message.Message):
    __slots__ = ("uid", "devices")
    UID_FIELD_NUMBER: _ClassVar[int]
    DEVICES_FIELD_NUMBER: _ClassVar[int]
    uid: int
    devices: _containers.RepeatedCompositeFieldContainer[DeviceInfo]
    def __init__(self, uid: _Optional[int] = ..., devices: _Optional[_Iterable[_Union[DeviceInfo, _Mapping]]] = ...) -> None: ...

class UserDevicesRequest(_message.Message):
    __slots__ = ("uid", "active_only")
    UID_FIELD_NUMBER: _ClassVar[int]
    ACTIVE_ONLY_FIELD_NUMBER: _ClassVar[int]
    uid: int
    active_only: bool
    def __init__(self, uid: _Optional[int] = ..., active_only: bool = ...) -> None: ...

class AllUsersDevices(_message.Message):
    __slots__ = ("users",)
    USERS_FIELD_NUMBER: _ClassVar[int]
    users: _containers.RepeatedCompositeFieldContainer[UserDevicesHistory]
    def __init__(self, users: _Optional[_Iterable[_Union[UserDevicesHistory, _Mapping]]] = ...) -> None: ...

class SystemStats(_message.Message):
    __slots__ = ("cpu_percent", "cpu_count", "mem_total", "mem_used", "mem_available", "mem_percent", "disk_total", "disk_used", "disk_free", "disk_percent", "load_avg_1", "load_avg_5", "load_avg_15", "uptime_seconds", "collected_at", "disk_path")
    CPU_PERCENT_FIELD_NUMBER: _ClassVar[int]
    CPU_COUNT_FIELD_NUMBER: _ClassVar[int]
    MEM_TOTAL_FIELD_NUMBER: _ClassVar[int]
    MEM_USED_FIELD_NUMBER: _ClassVar[int]
    MEM_AVAILABLE_FIELD_NUMBER: _ClassVar[int]
    MEM_PERCENT_FIELD_NUMBER: _ClassVar[int]
    DISK_TOTAL_FIELD_NUMBER: _ClassVar[int]
    DISK_USED_FIELD_NUMBER: _ClassVar[int]
    DISK_FREE_FIELD_NUMBER: _ClassVar[int]
    DISK_PERCENT_FIELD_NUMBER: _ClassVar[int]
    LOAD_AVG_1_FIELD_NUMBER: _ClassVar[int]
    LOAD_AVG_5_FIELD_NUMBER: _ClassVar[int]
    LOAD_AVG_15_FIELD_NUMBER: _ClassVar[int]
    UPTIME_SECONDS_FIELD_NUMBER: _ClassVar[int]
    COLLECTED_AT_FIELD_NUMBER: _ClassVar[int]
    DISK_PATH_FIELD_NUMBER: _ClassVar[int]
    cpu_percent: float
    cpu_count: int
    mem_total: int
    mem_used: int
    mem_available: int
    mem_percent: float
    disk_total: int
    disk_used: int
    disk_free: int
    disk_percent: float
    load_avg_1: float
    load_avg_5: float
    load_avg_15: float
    uptime_seconds: int
    collected_at: int
    disk_path: str
    def __init__(self, cpu_percent: _Optional[float] = ..., cpu_count: _Optional[int] = ..., mem_total: _Optional[int] = ..., mem_used: _Optional[int] = ..., mem_available: _Optional[int] = ..., mem_percent: _Optional[float] = ..., disk_total: _Optional[int] = ..., disk_used: _Optional[int] = ..., disk_free: _Optional[int] = ..., disk_percent: _Optional[float] = ..., load_avg_1: _Optional[float] = ..., load_avg_5: _Optional[float] = ..., load_avg_15: _Optional[float] = ..., uptime_seconds: _Optional[int] = ..., collected_at: _Optional[int] = ..., disk_path: _Optional[str] = ...) -> None: ...
