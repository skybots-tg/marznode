"""reconcile_xray_users: сверка того, что держит xray, с тем, что в storage.

Раньше «кто есть в xray» бралось из счётчиков статистики, а они есть только
у тех, кто возил трафик. На ноде 41 это давало «346 missing» каждые две
минуты при 432 юзерах на каждом инбаунде и сотни add_user впустую.
"""

from __future__ import annotations

import asyncio
import logging
import re

import pytest
from grpclib.const import Cardinality, Handler, Status
from grpclib.exceptions import GRPCError
from grpclib.server import Server

from marznode.backends.xray import _user_sync
from marznode.backends.xray._user_sync import reconcile_xray_users
from marznode.backends.xray.api import XrayAPI
from marznode.backends.xray.api.exceptions import (
    EmailExistsError,
    TagNotFoundError,
    UnimplementedError,
    UnknownError,
)
from marznode.backends.xray.api.inbound_users_pb2 import (
    GET_INBOUND_USERS,
    GetInboundUserRequest,
)
from marznode.backends.xray.api.proto.common.protocol import user_pb2
from marznode.backends.xray.api.proto.common.serial import typed_message_pb2
from marznode.backends.xray.api.stats import StatResponse
from marznode.models import Inbound, User
from marznode.storage.memory import MemoryStorage
from marznode.utils.network import find_free_port

TAGS = ["a", "b", "c"]


def _inbound(tag: str) -> Inbound:
    return Inbound(tag=tag, protocol="vless", config={"tag": tag, "flow": None})


def _email(uid: int) -> str:
    return f"{uid}.user{uid}"


class FakeXray:
    """HandlerService/StatsService as far as reconcile uses them."""

    def __init__(self, members: dict[str, set[int]]):
        self.members = {tag: {_email(u) for u in uids} for tag, uids in members.items()}
        self.errors: dict[str, Exception] = {}
        self.unimplemented = False
        self.down = False
        self.stats: list[StatResponse] = []
        self.pushes: list[tuple[int, str]] = []
        self.reads: list[str] = []

    async def get_inbound_users(self, tag: str) -> list[str]:
        self.reads.append(tag)
        if self.down:
            raise ConnectionRefusedError(111, "Connection refused")
        if self.unimplemented:
            raise UnimplementedError("unknown method GetInboundUsers")
        if tag in self.errors:
            raise self.errors[tag]
        if tag not in self.members:
            raise TagNotFoundError(f"handler not found: {tag}", tag)
        return sorted(self.members[tag])

    async def get_users_stats(self, reset: bool = False) -> list[StatResponse]:
        return self.stats

    async def add_user(self, user: User, inbound: Inbound) -> None:
        email = _email(user.id)
        if email in self.members[inbound.tag]:
            raise EmailExistsError(f"User {email} already exists.", email)
        self.members[inbound.tag].add(email)
        self.pushes.append((user.id, inbound.tag))


async def _storage(users: dict[int, list[str]], extra_tags=()) -> MemoryStorage:
    storage = MemoryStorage()
    for tag in [*TAGS, *extra_tags]:
        storage.register_inbound(_inbound(tag))
    for uid, tags in users.items():
        user = User(id=uid, username=f"user{uid}", key=f"key{uid}")
        await storage.update_user_inbounds(user, await storage.list_inbounds(tag=tags))
    return storage


def _reconcile(storage, xray, confirm_delay=0.0):
    return reconcile_xray_users(
        storage,
        [_inbound(t) for t in TAGS],
        xray,
        xray.add_user,
        confirm_delay=confirm_delay,
    )


def test_idle_users_are_not_drift(caplog, monkeypatch):
    """Нода 41: все на месте, трафика нет ни у кого — тишина и ни одного add."""
    monkeypatch.setattr(_user_sync, "_announced_mode", "inbound_users")
    storage = asyncio.run(_storage({1: TAGS, 2: TAGS, 3: ["a"]}))
    xray = FakeXray({"a": {1, 2, 3}, "b": {1, 2}, "c": {1, 2}})

    with caplog.at_level(logging.INFO):
        result = asyncio.run(_reconcile(storage, xray))

    assert xray.pushes == []
    assert result["missing"] == 0
    assert result["runtime_emails"] == 3
    assert [r for r in caplog.records if r.levelno >= logging.INFO] == []


def test_the_mode_is_announced_once(caplog, monkeypatch):
    monkeypatch.setattr(_user_sync, "_announced_mode", None)
    storage = asyncio.run(_storage({1: TAGS}))
    xray = FakeXray({"a": {1}, "b": {1}, "c": {1}})

    with caplog.at_level(logging.INFO):
        asyncio.run(_reconcile(storage, xray))
        asyncio.run(_reconcile(storage, xray))

    [record] = [r for r in caplog.records if r.levelno >= logging.INFO]
    assert "GetInboundUsers of 3/3 inbound(s)" in record.getMessage()


def test_user_dropped_from_one_inbound_is_put_back_there_only():
    storage = asyncio.run(_storage({1: TAGS, 2: TAGS}))
    xray = FakeXray({"a": {1, 2}, "b": {1}, "c": {1, 2}})

    result = asyncio.run(_reconcile(storage, xray))

    assert xray.pushes == [(2, "b")]
    assert result["missing"] == 1 and result["pushed"] == 1
    assert xray.members["b"] == {_email(1), _email(2)}


def test_users_never_pushed_after_an_outage_get_pushed():
    """Нода 31: в storage есть, в xray после отказа API — никого."""
    storage = asyncio.run(_storage({1: TAGS, 2: ["a", "c"], 3: ["b"]}))
    xray = FakeXray({"a": set(), "b": set(), "c": set()})

    result = asyncio.run(_reconcile(storage, xray))

    assert sorted(xray.pushes) == [(1, "a"), (1, "b"), (1, "c"), (2, "a"), (2, "c"), (3, "b")]
    assert result["pushed"] == 6 and result["failed"] == 0


def test_a_revocation_in_flight_is_not_undone():
    """Сервис убирает юзера из xray раньше, чем из storage.

    Если пушить сразу, в этот зазор юзер вернётся в xray, и убрать его
    оттуда будет уже некому.
    """
    storage = asyncio.run(_storage({1: TAGS, 2: TAGS}))
    xray = FakeXray({"a": {1, 2}, "b": {1}, "c": {1, 2}})

    async def scenario():
        task = asyncio.create_task(_reconcile(storage, xray, confirm_delay=0.05))
        await asyncio.sleep(0.01)
        # Панель сняла с юзера 2 инбаунд b: из xray он уже убран, storage догоняет.
        user2 = await storage.list_users(2)
        await storage.update_user_inbounds(user2, await storage.list_inbounds(tag=["a", "c"]))
        return await task

    result = asyncio.run(scenario())

    assert xray.pushes == []
    assert result["missing"] == 0


def test_an_add_that_lands_during_the_recheck_is_not_repeated():
    storage = asyncio.run(_storage({1: TAGS, 2: TAGS}))
    xray = FakeXray({"a": {1, 2}, "b": {1}, "c": {1, 2}})

    async def scenario():
        task = asyncio.create_task(_reconcile(storage, xray, confirm_delay=0.05))
        await asyncio.sleep(0.01)
        xray.members["b"].add(_email(2))  # add панели долетел
        return await task

    result = asyncio.run(scenario())

    assert xray.pushes == []
    assert result["missing"] == 0


def test_inbounds_of_other_backends_are_left_alone():
    storage = asyncio.run(_storage({1: ["a", "hysteria"]}, extra_tags=["hysteria"]))
    xray = FakeXray({"a": {1}, "b": set(), "c": set()})

    asyncio.run(_reconcile(storage, xray))

    assert "hysteria" not in xray.reads
    assert xray.pushes == []


def test_an_unreadable_inbound_does_not_stop_the_others(caplog):
    storage = asyncio.run(_storage({1: TAGS}))
    xray = FakeXray({"a": set(), "c": {1}})  # b неизвестен xray
    xray.errors["c"] = UnknownError("app/proxyman/command: proxy is not a UserManager")

    with caplog.at_level(logging.DEBUG):
        asyncio.run(_reconcile(storage, xray))

    assert xray.pushes == [(1, "a")]
    levels = {
        m.group(1): r.levelno
        for r in caplog.records
        if (m := re.search(r"inbound '([^']+)'", r.getMessage()))
    }
    assert levels == {"b": logging.WARNING, "c": logging.DEBUG}


def test_unreachable_xray_skips_the_pass():
    storage = asyncio.run(_storage({1: TAGS}))
    xray = FakeXray({"a": set(), "b": set(), "c": set()})
    xray.down = True

    result = asyncio.run(_reconcile(storage, xray))

    assert xray.pushes == []
    assert result["pushed"] == 0


def test_a_core_without_get_inbound_users_falls_back_to_stats():
    """Старый xray: прежнее поведение — по счётчикам, юзер без трафика «пропал»."""
    storage = asyncio.run(_storage({1: TAGS, 2: ["a", "b"]}))
    xray = FakeXray({"a": {1}, "b": {1}, "c": {1}})
    xray.unimplemented = True
    xray.stats = [StatResponse(_email(1), "user", "uplink", 10)]

    result = asyncio.run(_reconcile(storage, xray))

    assert result["mode"] == "stats"
    assert xray.pushes == [(2, "a"), (2, "b")]


# --- сам вызов GetInboundUsers, по проводу -------------------------------


class _Raw:
    """Сообщение, которое кодируется как есть: проверяем провод, а не себя."""

    def __init__(self, data: bytes = b""):
        self.data = data

    def SerializeToString(self) -> bytes:
        return self.data

    @classmethod
    def FromString(cls, data: bytes) -> "_Raw":
        return cls(data)


def _varint(n: int) -> bytes:
    out = b""
    while True:
        byte, n = n & 0x7F, n >> 7
        out += bytes([byte | (0x80 if n else 0)])
        if not n:
            return out


def _users_reply(emails: list[str]) -> bytes:
    """GetInboundUserResponse так, как его шлёт xray: repeated User users = 1."""
    reply = b""
    for email in emails:
        user = user_pb2.User(
            level=0,
            email=email,
            account=typed_message_pb2.TypedMessage(
                type="xray.proxy.vless.Account", value=b"\n$b72a58de"
            ),
        ).SerializeToString()
        reply += b"\n" + _varint(len(user)) + user
    return reply


class _HandlerService:
    def __init__(self, handler):
        self.handler = handler
        self.requests: list[bytes] = []

    def __mapping__(self):
        return {GET_INBOUND_USERS: Handler(self._call, Cardinality.UNARY_UNARY, _Raw, _Raw)}

    async def _call(self, stream):
        request = await stream.recv_message()
        self.requests.append(request.data)
        await self.handler(stream, request)


async def _against(handler, call):
    service = _HandlerService(handler)
    server = Server([service])
    port = find_free_port()
    await server.start("127.0.0.1", port)
    api = XrayAPI("127.0.0.1", port)
    try:
        return service, await call(api)
    finally:
        api._channel.close()
        server.close()
        await server.wait_closed()


def test_request_is_the_upstream_encoding():
    assert GetInboundUserRequest(tag="RU Direct").SerializeToString() == b"\n\tRU Direct"


def test_get_inbound_users_reads_xray_reply():
    emails = ["6776.630df0367ba14bc4aa4311e523581a6b", "18105.b6855cc4"]

    async def reply(stream, request):
        await stream.send_message(_Raw(_users_reply(emails)))

    service, got = asyncio.run(_against(reply, lambda api: api.get_inbound_users("RU->NL-1 Bridge")))

    assert got == emails
    assert service.requests == [GetInboundUserRequest(tag="RU->NL-1 Bridge").SerializeToString()]


def test_a_core_without_the_method_raises_unimplemented():
    # Так отвечает grpc-go на неизвестный метод. Пустой mapping у grpclib-сервера
    # не годится: он шлёт trailers-only без content-type, и клиент видит UNKNOWN.
    async def reply(stream, request):
        raise GRPCError(
            Status.UNIMPLEMENTED,
            "unknown method GetInboundUsers for service "
            "xray.app.proxyman.command.HandlerService",
        )

    with pytest.raises(UnimplementedError):
        asyncio.run(_against(reply, lambda api: api.get_inbound_users("a")))


def test_an_unknown_tag_is_tag_not_found():
    async def reply(stream, request):
        raise GRPCError(
            Status.UNKNOWN,
            "app/proxyman/command: failed to get handler: x > "
            "app/proxyman/inbound: handler not found: x",
        )

    with pytest.raises(TagNotFoundError):
        asyncio.run(_against(reply, lambda api: api.get_inbound_users("x")))
