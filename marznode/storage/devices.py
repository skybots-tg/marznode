"""Storage for device connection history"""

import logging
import time
from collections import defaultdict
from dataclasses import dataclass
from typing import Dict, Iterable, List, Optional, Union

from marznode.utils.device_fingerprint import (
    DEFAULT_FINGERPRINT_VERSION,
    build_device_fingerprints_all,
    is_device_allowed,
)

logger = logging.getLogger(__name__)


@dataclass
class DeviceInfo:
    """Information about a device/session"""
    remote_ip: str
    client_name: str
    user_agent: str = ""
    protocol: str = ""
    tls_fingerprint: str = ""
    first_seen: int = 0  # timestamp
    last_seen: int = 0   # timestamp
    total_usage: int = 0  # total traffic
    uplink: int = 0
    downlink: int = 0
    is_active: bool = True
    fingerprint: str = ""  # Device fingerprint for identification

    def __post_init__(self):
        if self.first_seen == 0:
            self.first_seen = int(time.time())
        if self.last_seen == 0:
            self.last_seen = self.first_seen

    def get_device_key(self) -> str:
        """Generate unique key for this device"""
        # Use fingerprint if available, otherwise fall back to IP:client_name
        if self.fingerprint:
            return self.fingerprint
        return f"{self.remote_ip}:{self.client_name}"

    def update(self, new_data: dict, usage_delta: int = 0):
        """Update device info with new data"""
        self.last_seen = int(time.time())
        self.is_active = True

        if usage_delta > 0:
            self.total_usage += usage_delta

        if "user_agent" in new_data and new_data["user_agent"]:
            self.user_agent = new_data["user_agent"]
        if "protocol" in new_data and new_data["protocol"]:
            self.protocol = new_data["protocol"]
        if "tls_fingerprint" in new_data and new_data["tls_fingerprint"]:
            self.tls_fingerprint = new_data["tls_fingerprint"]
        if "uplink" in new_data:
            self.uplink = int(new_data["uplink"])
        if "downlink" in new_data:
            self.downlink = int(new_data["downlink"])


class DeviceStorage:
    """Storage for device connection history"""

    def __init__(self, inactivity_timeout: int = 300):  # 5 минут по умолчанию
        self._devices: Dict[int, Dict[str, DeviceInfo]] = defaultdict(dict)
        self._last_usage: Dict[int, int] = {}  # для отслеживания дельты трафика
        self._inactivity_timeout = inactivity_timeout
        self._blocked_connections: Dict[int, List[str]] = defaultdict(list)  # uid -> list of blocked fingerprints

    def check_device_allowed(
        self,
        uid: int,
        fingerprint: Union[str, Iterable[str]],
        allowed_fingerprints: list[str],
        device_limit: Optional[int] = None,
        enforce: bool = True,
    ) -> tuple[bool, str]:
        """
        Check if a device is allowed to connect.

        ``fingerprint`` may be a single hash or an iterable of hashes (to
        match both v1 and v2 fingerprints during the dual-version window).

        Returns:
            Tuple of (is_allowed, reason)
        """
        allowed, reason = is_device_allowed(
            fingerprints=fingerprint,
            allowed_fingerprints=allowed_fingerprints,
            device_limit=device_limit,
            enforce=enforce,
        )

        if not allowed:
            primary_fp = (
                fingerprint
                if isinstance(fingerprint, str)
                else next(iter(fingerprint), "")
            )
            if primary_fp and primary_fp not in self._blocked_connections[uid]:
                self._blocked_connections[uid].append(primary_fp)
                logger.warning(
                    "Blocked connection for user %s: %s, fingerprint=%s...",
                    uid,
                    reason,
                    primary_fp[:16],
                )

        return allowed, reason

    def update_device(
        self,
        uid: int,
        remote_ip: str,
        client_name: str,
        current_usage: int,
        meta: dict,
        allowed_fingerprints: Optional[list[str]] = None,
        device_limit: Optional[int] = None,
        enforce_limit: bool = False,
    ) -> tuple[bool, str]:
        """
        Update or create device info.
        
        Returns:
            Tuple of (is_allowed, reason) - whether the device is allowed to connect
        """
        # Compute fingerprints for every supported version.  The allowed
        # list from Marzneshin may contain either v1 or v2 hashes, so we
        # match on any overlap; the v2 hash is used as the local storage
        # key to keep device records stable going forward.
        fingerprints_by_version = build_device_fingerprints_all(
            user_id=uid,
            client_name=client_name,
            tls_fingerprint=meta.get("tls_fingerprint", ""),
            os_guess=meta.get("os_guess", ""),
            user_agent=meta.get("user_agent", ""),
        )
        fingerprint = fingerprints_by_version[DEFAULT_FINGERPRINT_VERSION]

        if enforce_limit and allowed_fingerprints is not None:
            is_allowed, reason = self.check_device_allowed(
                uid=uid,
                fingerprint=tuple(fingerprints_by_version.values()),
                allowed_fingerprints=allowed_fingerprints,
                device_limit=device_limit,
                enforce=enforce_limit,
            )

            if not is_allowed:
                return False, reason

        device_key = fingerprint  # Use the v2 fingerprint as device key

        # Вычисляем дельту трафика
        last_usage = self._last_usage.get(uid, 0)
        usage_delta = max(0, current_usage - last_usage)
        self._last_usage[uid] = current_usage

        if device_key in self._devices[uid]:
            # Обновляем существующее устройство
            device = self._devices[uid][device_key]
            device.update(meta, usage_delta)
            logger.debug(
                f"Updated device for user {uid}: fingerprint={fingerprint[:16]}..., "
                f"usage_delta={usage_delta}, total={device.total_usage}"
            )
        else:
            # Создаём новое устройство
            device = DeviceInfo(
                remote_ip=remote_ip,
                client_name=client_name,
                user_agent=meta.get("user_agent", ""),
                protocol=meta.get("protocol", ""),
                tls_fingerprint=meta.get("tls_fingerprint", ""),
                total_usage=usage_delta,
                uplink=meta.get("uplink", 0),
                downlink=meta.get("downlink", 0),
                fingerprint=fingerprint,
            )
            self._devices[uid][device_key] = device
            logger.info(
                f"New device for user {uid}: fingerprint={fingerprint[:16]}..., "
                f"client={client_name}, ip={remote_ip}"
            )

        return True, "device allowed"

    def mark_inactive_devices(self):
        """Mark devices as inactive if they haven't been seen recently"""
        current_time = int(time.time())
        for uid, devices in self._devices.items():
            for device in devices.values():
                if device.is_active and (current_time - device.last_seen) > self._inactivity_timeout:
                    device.is_active = False
                    logger.debug(f"Device {device.get_device_key()} for user {uid} marked as inactive")

    def get_user_devices(self, uid: int, active_only: bool = False) -> List[DeviceInfo]:
        """Get all devices for a user"""
        if uid not in self._devices:
            return []

        devices = list(self._devices[uid].values())
        if active_only:
            devices = [d for d in devices if d.is_active]

        # Сортируем по последнему подключению (новые первые)
        devices.sort(key=lambda d: d.last_seen, reverse=True)
        return devices

    def get_all_devices(self) -> Dict[int, List[DeviceInfo]]:
        """Get all devices for all users"""
        return {
            uid: self.get_user_devices(uid)
            for uid in self._devices.keys()
        }

    def get_blocked_connections(self, uid: Optional[int] = None) -> Dict[int, List[str]]:
        """Get blocked connection attempts"""
        if uid is not None:
            return {uid: self._blocked_connections.get(uid, [])}
        return dict(self._blocked_connections)

    def clear_blocked_connections(self, uid: Optional[int] = None):
        """Clear blocked connection history"""
        if uid is not None:
            if uid in self._blocked_connections:
                del self._blocked_connections[uid]
        else:
            self._blocked_connections.clear()

    def cleanup_old_devices(self, max_age_seconds: int = 86400 * 7):  # 7 дней
        """Remove devices that haven't been seen for a long time"""
        current_time = int(time.time())
        removed_count = 0

        for uid, devices in list(self._devices.items()):
            for device_key, device in list(devices.items()):
                if (current_time - device.last_seen) > max_age_seconds:
                    del devices[device_key]
                    removed_count += 1

            # Удаляем пользователя, если у него не осталось устройств
            if not devices:
                del self._devices[uid]

        if removed_count > 0:
            logger.info(f"Cleaned up {removed_count} old devices")

        return removed_count

