"""What a vpn server should do"""

import asyncio
import json
import logging
from collections import defaultdict

from marznode.backends.abstract_backend import VPNBackend
from marznode.backends.xray._config import XrayConfig
from marznode.backends.xray._runner import XrayCore
from marznode.backends.xray.api import XrayAPI
from marznode.backends.xray.api.exceptions import (
    EmailExistsError,
    EmailNotFoundError,
    TagNotFoundError,
)
from marznode.backends.xray.api.types.account import accounts_map
from marznode.config import XRAY_RESTART_ON_FAILURE, XRAY_RESTART_ON_FAILURE_INTERVAL
from marznode.models import User, Inbound
from marznode.storage import BaseStorage
from marznode.storage.devices import DeviceStorage
from marznode.utils.network import find_free_port

logger = logging.getLogger(__name__)


class XrayBackend(VPNBackend):
    backend_type = "xray"
    config_format = 1

    def __init__(
        self,
        executable_path: str,
        assets_path: str,
        config_path: str,
        storage: BaseStorage,
    ):
        self._config = None
        self._inbound_tags = set()
        self._inbounds = list()
        self._api = None
        self._runner = XrayCore(executable_path, assets_path)
        self._storage = storage
        self._config_path = config_path
        self._restart_lock = asyncio.Lock()
        
        # Device tracking and enforcement
        self._device_storage = DeviceStorage(inactivity_timeout=300)
        self._device_check_interval = 60  # Check devices every 60 seconds
        self._device_enforcement_enabled = False
        
        asyncio.create_task(self._restart_on_failure())
        asyncio.create_task(self._periodic_device_check())

    @property
    def running(self) -> bool:
        return self._runner.running

    @property
    def version(self):
        return self._runner.version

    def contains_tag(self, tag: str) -> bool:
        return tag in self._inbound_tags

    def list_inbounds(self) -> list:
        return self._inbounds

    def get_config(self) -> str:
        with open(self._config_path) as f:
            return f.read()

    def save_config(self, config: str) -> None:
        with open(self._config_path, "w") as f:
            f.write(config)

    async def add_storage_users(self):
        for inbound in self._inbounds:
            for user in await self._storage.list_inbound_users(inbound.tag):
                await self.add_user(user, inbound)

    async def _restart_on_failure(self):
        while True:
            await self._runner.stop_event.wait()
            self._runner.stop_event.clear()
            if self._restart_lock.locked():
                logger.debug("Xray restarting as planned")
            else:
                logger.debug("Xray stopped unexpectedly")
                if XRAY_RESTART_ON_FAILURE:
                    await asyncio.sleep(XRAY_RESTART_ON_FAILURE_INTERVAL)
                    await self.start()
                    await self.add_storage_users()

    async def start(self, backend_config: str | None = None):
        if backend_config is None:
            with open(self._config_path) as f:
                backend_config = f.read()
        else:
            self.save_config(json.dumps(json.loads(backend_config), indent=2))
        xray_api_port = find_free_port()
        self._config = XrayConfig(backend_config, api_port=xray_api_port)
        self._config.register_inbounds(self._storage)
        self._inbound_tags = {i["tag"] for i in self._config.inbounds}
        self._inbounds = list(self._config.list_inbounds())
        self._api = XrayAPI("127.0.0.1", xray_api_port)
        await self._runner.start(self._config)

    async def stop(self):
        await self._runner.stop()
        for tag in self._inbound_tags:
            self._storage.remove_inbound(tag)
        self._inbound_tags = set()
        self._inbounds = list()

    async def restart(self, backend_config: str | None) -> list[Inbound] | None:
        logger.info("=== XrayBackend.restart() CALLED ===")
        logger.info(f"Restarting Xray backend, has new config: {backend_config is not None}")
        
        # xray_config = backend_config if backend_config else self._config
        await self._restart_lock.acquire()
        logger.debug("Acquired restart lock")
        try:
            if not backend_config:
                logger.info("Restarting Xray with existing config")
                return await self._runner.restart(self._config)
            
            # При перезапуске с новым конфигом нужно установить флаг restarting,
            # чтобы stop() знал, что это запланированный перезапуск
            logger.info("Restarting Xray with new config (stop + start)")
            self._runner.restarting = True
            logger.info("Set restarting flag to True before stop()")
            try:
                await self.stop()
                logger.info("Xray stopped, starting with new config")
                await self.start(backend_config)
                logger.info("Xray restarted successfully")
            finally:
                # Сбрасываем флаг после завершения перезапуска
                self._runner.restarting = False
                logger.info("Set restarting flag to False after restart")
        except Exception as e:
            logger.error(
                f"Error during Xray restart: {e}",
                exc_info=True
            )
            raise
        finally:
            self._restart_lock.release()
            logger.debug("Released restart lock")

    async def add_user(self, user: User, inbound: Inbound):
        email = f"{user.id}.{user.username}"

        account_class = accounts_map[inbound.protocol]
        flow = inbound.config["flow"] or ""
        logger.debug(flow)
        user_account = account_class(
            email=email,
            seed=user.key,
            flow=flow,
        )

        try:
            await self._api.add_inbound_user(inbound.tag, user_account)
            
            # Log device limit configuration if enabled
            if user.enforce_device_limit and user.device_limit is not None:
                logger.info(
                    f"User {user.username} (id={user.id}) added with device limit: "
                    f"{user.device_limit}, allowed devices: {len(user.allowed_fingerprints)}"
                )
        except (EmailExistsError, TagNotFoundError):
            raise
        except OSError:
            logger.warning("user addition requested when xray api is down")

    async def remove_user(self, user: User, inbound: Inbound):
        email = f"{user.id}.{user.username}"
        try:
            await self._api.remove_inbound_user(inbound.tag, email)
        except (EmailNotFoundError, TagNotFoundError):
            raise
        except OSError:
            logger.warning("user removal requested when xray api is down")

    async def get_usages(self, reset: bool = True) -> dict[int, int]:
        try:
            api_stats = await self._api.get_users_stats(reset=reset)
        except OSError:
            api_stats = []
        stats = defaultdict(int)
        for stat in api_stats:
            uid = int(stat.name.split(".")[0])
            stats[uid] += stat.value
        return stats

    async def get_users_meta(self) -> dict[int, dict]:
        """
        Возвращает метаданные пользователей: uplink/downlink из API и IP из логов.
        """
        try:
            stats = await self._api.get_users_stats(reset=False)
        except OSError:
            stats = []

        uplink: dict[int, int] = defaultdict(int)
        downlink: dict[int, int] = defaultdict(int)

        # Собираем uplink/downlink из API
        for stat in stats:
            # stat.name формат: "user>>>123.username>>>traffic>>>uplink" или "downlink"
            parts = stat.name.split(">>>")
            if len(parts) < 4:
                continue
            
            user_email = parts[1]  # "123.username"
            link = parts[3]        # "uplink" / "downlink"
            
            try:
                uid = int(user_email.split(".")[0])
            except (ValueError, IndexError):
                continue
            
            if link == "uplink":
                uplink[uid] += stat.value
            elif link == "downlink":
                downlink[uid] += stat.value

        # Получаем метаданные из логов (IP адреса)
        log_meta = self._runner.get_last_meta()
        
        logger.info(f"=== XrayBackend.get_users_meta() ===")
        logger.info(f"Log metadata from access logs: {len(log_meta)} users")
        for uid, data in list(log_meta.items())[:3]:  # Показываем первые 3
            logger.info(f"  User {uid}: {data}")

        # Объединяем данные
        meta: dict[int, dict] = {}
        all_uids = set(uplink.keys()) | set(downlink.keys()) | set(log_meta.keys())
        
        for uid in all_uids:
            user_meta = {
                "uplink": uplink.get(uid, 0),
                "downlink": downlink.get(uid, 0),
                "client_name": "xray",
            }
            
            # Добавляем IP из логов, если он есть
            if uid in log_meta and "remote_ip" in log_meta[uid]:
                user_meta["remote_ip"] = log_meta[uid]["remote_ip"]
                logger.debug(f"Added remote_ip for user {uid}: {user_meta['remote_ip']}")
            
            meta[uid] = user_meta
        
        logger.info(f"Returning metadata for {len(meta)} users, {sum(1 for m in meta.values() if 'remote_ip' in m)} with IPs")
        return meta
    
    async def _periodic_device_check(self):
        """Periodically check device connections and enforce limits"""
        while True:
            try:
                await asyncio.sleep(self._device_check_interval)
                
                if not self.running or not self._device_enforcement_enabled:
                    continue
                
                # Get current usage stats and metadata
                try:
                    usages = await self.get_usages(reset=False)
                    meta = await self.get_users_meta()
                except Exception as e:
                    logger.error(f"Error getting stats for device check: {e}")
                    continue
                
                # Check each user's devices
                users = await self._storage.list_users()
                if not users:
                    continue
                
                for user in users:
                    if not user.enforce_device_limit or user.device_limit is None:
                        continue
                    
                    uid = user.id
                    if uid not in usages or uid not in meta:
                        continue
                    
                    # Update device storage and check if allowed
                    remote_ip = meta[uid].get("remote_ip", "unknown")
                    client_name = meta[uid].get("client_name", "xray")
                    
                    is_allowed, reason = self._device_storage.update_device(
                        uid=uid,
                        remote_ip=remote_ip,
                        client_name=client_name,
                        current_usage=usages[uid],
                        meta=meta[uid],
                        allowed_fingerprints=user.allowed_fingerprints,
                        device_limit=user.device_limit,
                        enforce_limit=user.enforce_device_limit,
                    )
                    
                    if not is_allowed:
                        logger.warning(
                            f"Device limit violation for user {user.username} (id={uid}): {reason}"
                        )
                        # TODO: Implement blocking mechanism
                        # Options:
                        # 1. Remove user from xray temporarily
                        # 2. Add to blacklist route
                        # 3. Report to Marzneshin for action
                
                # Mark inactive devices
                self._device_storage.mark_inactive_devices()
                
            except Exception as e:
                logger.error(f"Error in periodic device check: {e}", exc_info=True)
    
    def get_device_storage(self) -> DeviceStorage:
        """Get device storage instance for external access"""
        return self._device_storage
    
    def set_device_enforcement(self, enabled: bool):
        """Enable or disable device limit enforcement"""
        self._device_enforcement_enabled = enabled
        logger.info(f"Device limit enforcement {'enabled' if enabled else 'disabled'}")

    async def get_logs(self, include_buffer: bool = True):
        if include_buffer:
            for line in self._runner.get_buffer():
                yield line
        log_stm = self._runner.get_logs_stm()
        async with log_stm:
            async for line in log_stm:
                yield line
