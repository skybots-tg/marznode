"""Смена ключа у уже заведённого юзера должна доезжать до xray.

07.10.2026: клиент перевыпустил ссылку, новая не работала ни на одном
сервере, старая продолжала пускать. ``_update_user`` сверял только набор
инбаундов: юзер с тем же набором и новым ключом считался «в порядке», и ни
SyncUsers, ни RepopulateUsers ключ не меняли. Отпечаток ключ тоже не
учитывал, так что и сверка панели расхождения не видела.
"""

from __future__ import annotations

import asyncio

import pytest

from marznode.backends.xray._user_sync import reconcile_xray_users
from marznode.backends.xray.api.exceptions import (
    EmailExistsError,
    EmailNotFoundError,
)
from marznode.models import Inbound, User
from marznode.service import service as svc
from marznode.service.service import MarzService
from marznode.service.service_pb2 import (
    Empty,
    Inbound as InboundPb,
    User as UserPb,
    UserData,
    UsersData,
)
from marznode.storage.memory import MemoryStorage
from marznode.utils.users_digest import keyed_users_digest, users_digest

TAGS = ["a", "b"]


def _inbound(tag: str) -> Inbound:
    return Inbound(tag=tag, protocol="vless", config={"tag": tag, "flow": None})


class FakeBackend:
    """Xray, как его видит сервис: email → ключ, по инбаундам."""

    def __init__(self, tags):
        self.tags = set(tags)
        self.members: dict[str, dict[str, str]] = {t: {} for t in tags}
        self.fail_remove = False
        self.on_remove = None

    def contains_tag(self, tag: str) -> bool:
        return tag in self.tags

    async def add_user(self, user: User, inbound: Inbound) -> None:
        email = f"{user.id}.{user.username}"
        if email in self.members[inbound.tag]:
            raise EmailExistsError(f"User {email} already exists.", email)
        self.members[inbound.tag][email] = user.key

    async def remove_user(self, user: User, inbound: Inbound) -> None:
        if self.fail_remove:
            raise RuntimeError("xray api: boom")
        email = f"{user.id}.{user.username}"
        if email not in self.members[inbound.tag]:
            raise EmailNotFoundError(f"User {email} not found.", email)
        del self.members[inbound.tag][email]
        if self.on_remove:
            await self.on_remove()

    # для reconcile_xray_users
    async def get_inbound_users(self, tag: str) -> list[str]:
        return sorted(self.members[tag])

    def keys(self, uid: int, username: str) -> dict[str, str | None]:
        email = f"{uid}.{username}"
        return {t: self.members[t].get(email) for t in sorted(self.tags)}


class FakeStream:
    def __init__(self, messages=()):
        self._in = list(messages)
        self.sent = []

    async def recv_message(self):
        return self._in.pop(0) if self._in else None

    async def send_message(self, message):
        self.sent.append(message)

    def __aiter__(self):
        return self

    async def __anext__(self):
        if not self._in:
            raise StopAsyncIteration
        return self._in.pop(0)


def _user_data(uid: int, key: str, tags=TAGS) -> UserData:
    return UserData(
        user=UserPb(id=uid, username=f"user{uid}", key=key),
        inbounds=[InboundPb(tag=t) for t in tags],
    )


@pytest.fixture
def node():
    storage = MemoryStorage()
    for tag in TAGS:
        storage.register_inbound(_inbound(tag))
    backend = FakeBackend(TAGS)
    service = MarzService(storage, {"xray": backend})
    asyncio.run(service._update_user(_user_data(1, "old-key")))
    assert backend.keys(1, "user1") == {"a": "old-key", "b": "old-key"}
    return service, storage, backend


def test_sync_users_replaces_the_key(node):
    service, storage, backend = node

    asyncio.run(service.SyncUsers(FakeStream([_user_data(1, "new-key")])))

    assert backend.keys(1, "user1") == {"a": "new-key", "b": "new-key"}
    stored = asyncio.run(storage.list_users(1))
    assert stored.key == "new-key"
    assert sorted(i.tag for i in stored.inbounds) == TAGS


def test_repopulate_converges_a_node_that_missed_the_revoke(node):
    """Нода была офлайн во время перевыпуска — полная выгрузка её чинит."""
    service, storage, backend = node

    stream = FakeStream([UsersData(users_data=[_user_data(1, "new-key")])])
    asyncio.run(service.RepopulateUsers(stream))

    assert backend.keys(1, "user1") == {"a": "new-key", "b": "new-key"}
    assert asyncio.run(storage.list_users(1)).key == "new-key"
    assert isinstance(stream.sent[0], Empty)


def test_key_and_inbounds_change_together(node):
    service, storage, backend = node

    asyncio.run(service._update_user(_user_data(1, "new-key", tags=["b"])))

    assert backend.keys(1, "user1") == {"a": None, "b": "new-key"}
    stored = asyncio.run(storage.list_users(1))
    assert (stored.key, [i.tag for i in stored.inbounds]) == ("new-key", ["b"])


def test_an_inbound_xray_already_lost_does_not_block_the_rekey(node):
    service, storage, backend = node
    del backend.members["a"]["1.user1"]

    asyncio.run(service._update_user(_user_data(1, "new-key")))

    assert backend.keys(1, "user1") == {"a": "new-key", "b": "new-key"}


def test_same_key_does_not_touch_xray(node):
    service, storage, backend = node
    removed = []

    async def on_remove():
        removed.append(True)

    backend.on_remove = on_remove

    asyncio.run(service._update_user(_user_data(1, "old-key")))

    assert removed == []
    assert backend.keys(1, "user1") == {"a": "old-key", "b": "old-key"}


def test_a_failed_removal_leaves_storage_with_the_old_key(node):
    """Иначе отпечаток покажет новый ключ, а xray будет пускать по старому."""
    service, storage, backend = node
    backend.fail_remove = True

    with pytest.raises(RuntimeError):
        asyncio.run(service._update_user(_user_data(1, "new-key")))

    stored = asyncio.run(storage.list_users(1))
    assert stored.key == "old-key"
    assert sorted(i.tag for i in stored.inbounds) == TAGS
    assert backend.keys(1, "user1") == {"a": "old-key", "b": "old-key"}


def test_reconcile_in_the_gap_pushes_the_new_key(node):
    """Сверка xray, сработавшая между снятием и добавлением, не вернёт старый ключ."""
    service, storage, backend = node
    pushed = []

    async def reconcile_now():
        if pushed:
            return
        pushed.append(True)
        backend.on_remove = None
        await reconcile_xray_users(
            storage,
            [_inbound(t) for t in TAGS],
            backend,
            backend.add_user,
            confirm_delay=0,
        )

    backend.on_remove = reconcile_now

    asyncio.run(service._update_user(_user_data(1, "new-key")))

    assert pushed == [True]
    assert backend.keys(1, "user1") == {"a": "new-key", "b": "new-key"}


def test_digest_sees_the_key(node):
    service, storage, backend = node

    def digest():
        stream = FakeStream([Empty()])
        asyncio.run(service.GetUsersDigest(stream))
        return stream.sent[0]

    before = digest()
    asyncio.run(service._update_user(_user_data(1, "new-key")))
    after = digest()

    # Старый отпечаток смену ключа не видит — для старых панелей он прежний.
    assert after.digest == before.digest == users_digest([(1, TAGS)])
    assert after.keyed_digest != before.keyed_digest
    assert after.keyed_digest == keyed_users_digest([(1, "new-key", TAGS)])
    assert after.count == 1


def test_an_image_without_keyed_digest_answers_the_old_way(node, monkeypatch):
    """service_pb2 из старого образа: поле не заполняется, RPC не падает."""
    service, storage, backend = node
    monkeypatch.setattr(svc, "_HAS_KEYED_DIGEST", False)

    stream = FakeStream([Empty()])
    asyncio.run(service.GetUsersDigest(stream))

    assert stream.sent[0].keyed_digest == ""
    assert stream.sent[0].digest == users_digest([(1, TAGS)])
