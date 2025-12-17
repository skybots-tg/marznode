"""Storage for device connection history"""

import logging
import time
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Dict, List, Optional

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
    
    def __post_init__(self):
        if self.first_seen == 0:
            self.first_seen = int(time.time())
        if self.last_seen == 0:
            self.last_seen = self.first_seen
    
    def get_device_key(self) -> str:
        """Generate unique key for this device"""
        # Используем IP + client_name как уникальный идентификатор устройства
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
    
    def update_device(
        self, 
        uid: int, 
        remote_ip: str, 
        client_name: str,
        current_usage: int,
        meta: dict
    ):
        """Update or create device info"""
        device_key = f"{remote_ip}:{client_name}"
        
        # Вычисляем дельту трафика
        last_usage = self._last_usage.get(uid, 0)
        usage_delta = max(0, current_usage - last_usage)
        self._last_usage[uid] = current_usage
        
        if device_key in self._devices[uid]:
            # Обновляем существующее устройство
            device = self._devices[uid][device_key]
            device.update(meta, usage_delta)
            logger.debug(
                f"Updated device for user {uid}: {device_key}, "
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
            )
            self._devices[uid][device_key] = device
            logger.info(f"New device for user {uid}: {device_key}")
    
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

