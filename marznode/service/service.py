"""
The grpc Service to add/update/delete users
Right now it only supports Xray but that is subject to change
"""

import json
import logging
from collections import defaultdict

from grpclib import GRPCError, Status
from grpclib.server import Stream

from marznode.backends.abstract_backend import VPNBackend
from marznode.storage import BaseStorage
from .service_grpc import MarzServiceBase
from .service_pb2 import (
    BackendConfig as BackendConfig_pb2,
    Backend,
    BackendLogsRequest,
    RestartBackendRequest,
    BackendStats,
)
from .service_pb2 import (
    UserData,
    UsersData,
    Empty,
    BackendsResponse,
    Inbound,
    UsersStats,
    LogLine,
)
from ..models import User, Inbound as InboundModel

logger = logging.getLogger(__name__)


class MarzService(MarzServiceBase):
    """Add/Update/Delete users based on calls from the client"""

    def __init__(self, storage: BaseStorage, backends: dict[str, VPNBackend]):
        self._backends = backends
        self._storage = storage

    def _resolve_tag(self, inbound_tag: str) -> VPNBackend:
        for backend in self._backends.values():
            if backend.contains_tag(inbound_tag):
                return backend
        raise

    async def _add_user(self, user: User, inbounds: list[Inbound]):
        for inbound in inbounds:
            backend = self._resolve_tag(inbound.tag)
            logger.debug("adding user `%s` to inbound `%s`", user.username, inbound.tag)
            await backend.add_user(user, inbound)

    async def _remove_user(self, user: User, inbounds: list[InboundModel]):
        for inbound in inbounds:
            backend = self._resolve_tag(inbound.tag)
            logger.debug(
                "removing user `%s` from inbound `%s`", user.username, inbound.tag
            )
            await backend.remove_user(user, inbound)

    async def _update_user(self, user_data: UserData):
        user = user_data.user
        user = User(id=user.id, username=user.username, key=user.key)
        storage_user = await self._storage.list_users(user.id)
        if not storage_user and len(user_data.inbounds) > 0:
            """add the user in case there isn't any currently
            and the inbounds is non-empty"""
            inbound_tags = [i.tag for i in user_data.inbounds]
            inbound_additions = await self._storage.list_inbounds(tag=inbound_tags)
            await self._add_user(user, inbound_additions)
            await self._storage.update_user_inbounds(
                user,
                [i for i in inbound_additions],
            )
            return
        elif not user_data.inbounds and storage_user:
            """remove in case we have the user but client has sent
            us an empty list of inbounds"""
            await self._remove_user(storage_user, storage_user.inbounds)
            return await self._storage.remove_user(user)
        elif not user_data.inbounds and not storage_user:
            """we're asked to remove a user which we don't have, just pass."""
            return

        """otherwise synchronize the user with what 
        the client has sent us"""
        storage_tags = {i.tag for i in storage_user.inbounds}
        new_tags = {i.tag for i in user_data.inbounds}
        added_tags = new_tags - storage_tags
        removed_tags = storage_tags - new_tags
        new_inbounds = await self._storage.list_inbounds(tag=list(new_tags))
        added_inbounds = await self._storage.list_inbounds(tag=list(added_tags))
        removed_inbounds = await self._storage.list_inbounds(tag=list(removed_tags))
        await self._remove_user(storage_user, removed_inbounds)
        await self._add_user(storage_user, added_inbounds)
        await self._storage.update_user_inbounds(storage_user, new_inbounds)

    async def SyncUsers(self, stream: Stream[UserData, Empty]) -> None:
        async for user_data in stream:
            await self._update_user(user_data)

    async def FetchBackends(
        self,
        stream: Stream[Empty, BackendsResponse],
    ) -> None:
        await stream.recv_message()
        backends = [
            Backend(
                name=name,
                type=backend.backend_type,
                version=backend.version,
                inbounds=[
                    Inbound(tag=i.tag, config=json.dumps(i.config))
                    for i in backend.list_inbounds()
                ],
            )
            for name, backend in self._backends.items()
        ]
        await stream.send_message(BackendsResponse(backends=backends))

    async def RepopulateUsers(
        self,
        stream: Stream[UsersData, Empty],
    ) -> None:
        users_data = (await stream.recv_message()).users_data
        for user_data in users_data:
            await self._update_user(user_data)
        user_ids = {user_data.user.id for user_data in users_data}
        for storage_user in await self._storage.list_users():
            if storage_user.id not in user_ids:
                await self._remove_user(storage_user, storage_user.inbounds)
                await self._storage.remove_user(storage_user)
        await stream.send_message(Empty())

    async def FetchUsersStats(self, stream: Stream[Empty, UsersStats]) -> None:
        await stream.recv_message()
        
        # суммарный трафик по всем backend'ам
        total_usage: dict[int, int] = defaultdict(int)
        # раздельный RX/TX (если backend умеет)
        total_uplink: dict[int, int] = defaultdict(int)
        total_downlink: dict[int, int] = defaultdict(int)
        # метаданные по пользователям
        meta: dict[int, dict[str, str]] = {}

        for backend_name, backend in self._backends.items():
            # 1) как и раньше — usage
            stats = await backend.get_usages()
            for uid, usage in stats.items():
                total_usage[uid] += usage

            # 2) необязательные метаданные
            get_meta = getattr(backend, "get_users_meta", None)
            if not get_meta:
                continue

            try:
                backend_meta = await get_meta()
            except Exception as e:
                # не хотим, чтобы падала вся статистика из-за одного backend'а
                logger.warning(f"Failed to get metadata from backend {backend_name}: {e}")
                continue

            for uid, info in backend_meta.items():
                # info — обычный dict
                user_meta = meta.setdefault(uid, {})
                
                # не затираем уже известные поля, если другой backend успел их заполнить
                if info.get("remote_ip") and not user_meta.get("remote_ip"):
                    user_meta["remote_ip"] = info["remote_ip"]
                
                if info.get("client_name") and not user_meta.get("client_name"):
                    user_meta["client_name"] = info["client_name"]
                elif not user_meta.get("client_name"):
                    user_meta["client_name"] = backend_name
                
                if info.get("user_agent") and not user_meta.get("user_agent"):
                    user_meta["user_agent"] = info["user_agent"]
                
                if "uplink" in info:
                    total_uplink[uid] += int(info["uplink"])
                if "downlink" in info:
                    total_downlink[uid] += int(info["downlink"])
                
                # на будущее — протокол, tls_fp, etc
                for key in ("protocol", "tls_fingerprint"):
                    if info.get(key) and not user_meta.get(key):
                        user_meta[key] = info[key]

        # собираем ответ
        logger.debug(f"Total usage: {total_usage}, Meta: {meta}")
        user_stats = []
        for uid, usage in total_usage.items():
            info = meta.get(uid, {})
            user_stats.append(
                UsersStats.UserStats(
                    uid=uid,
                    usage=usage,
                    remote_ip=info.get("remote_ip", ""),
                    client_name=info.get("client_name", ""),
                    user_agent=info.get("user_agent", ""),
                    uplink=total_uplink.get(uid, 0),
                    downlink=total_downlink.get(uid, 0),
                    protocol=info.get("protocol", ""),
                    tls_fingerprint=info.get("tls_fingerprint", ""),
                )
            )
        
        await stream.send_message(UsersStats(users_stats=user_stats))

    async def StreamBackendLogs(
        self, stream: Stream[BackendLogsRequest, LogLine]
    ) -> None:
        req = await stream.recv_message()
        if req.backend_name not in self._backends:
            raise
        async for line in self._backends[req.backend_name].get_logs(req.include_buffer):
            await stream.send_message(LogLine(line=line))

    async def FetchBackendConfig(
        self, stream: Stream[Backend, BackendConfig_pb2]
    ) -> None:
        req = await stream.recv_message()
        backend = self._backends[req.name]
        config = backend.get_config()
        await stream.send_message(
            BackendConfig_pb2(configuration=config, config_format=backend.config_format)
        )

    async def RestartBackend(
        self, stream: Stream[RestartBackendRequest, Empty]
    ) -> None:
        message = await stream.recv_message()

        await self._backends[message.backend_name].restart(message.config.configuration)
        await stream.send_message(Empty())

    async def GetBackendStats(self, stream: Stream[Backend, BackendStats]):
        backend = await stream.recv_message()
        if backend.name not in self._backends.keys():
            raise GRPCError(
                Status.NOT_FOUND,
                "Backend doesn't exist",
            )
        running = self._backends[backend.name].running
        await stream.send_message(BackendStats(running=running))
