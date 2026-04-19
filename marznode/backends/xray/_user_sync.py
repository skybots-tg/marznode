"""Deterministic user re-synchronization across xray restarts.

The default ``MemoryStorage.remove_inbound()`` filters every user's
inbound list, which means a plain stop()/start() cycle wipes the
user→inbound mapping and we end up with "0 users in xray + invalid
request user id" until the panel re-pushes everything.

These helpers snapshot the mapping before stop() and re-apply it after
start(), explicitly logging which user/inbound combinations could not
be restored and why.
"""

from __future__ import annotations

import logging
from typing import Awaitable, Callable

from marznode.backends.xray.api.exceptions import (
    EmailExistsError,
    XConnectionError,
)
from marznode.models import Inbound, User
from marznode.storage import BaseStorage

logger = logging.getLogger(__name__)

UserInboundsSnapshot = list[tuple[User, list[str]]]
AddUserFn = Callable[[User, Inbound], Awaitable[None]]


async def snapshot_users_for_restart(
    storage: BaseStorage,
) -> UserInboundsSnapshot:
    """Capture (user, [tag, ...]) before stop() wipes user.inbounds."""
    snapshot: UserInboundsSnapshot = []
    try:
        users = await storage.list_users() or []
    except Exception as e:
        logger.error(
            "Could not snapshot users before xray restart: %s (%s)",
            e,
            type(e).__name__,
            exc_info=True,
        )
        return snapshot
    for user in users:
        tags = [inb.tag for inb in (user.inbounds or [])]
        snapshot.append((user, tags))
    logger.info(
        "Snapshotted %d users (with their inbound tags) before restart",
        len(snapshot),
    )
    return snapshot


async def restore_users_after_restart(
    storage: BaseStorage,
    snapshot: UserInboundsSnapshot,
    add_user: AddUserFn,
) -> dict:
    """Re-attach snapshotted users to the freshly registered inbounds.

    Tags that no longer exist in the new config are dropped with an
    explicit warning so the operator can see why a user lost an inbound.
    """
    if not snapshot:
        return {"restored": 0, "dropped_tags": 0}
    restored = 0
    dropped_tags = 0
    for user, tags in snapshot:
        try:
            inbounds = await storage.list_inbounds(tag=tags) or []
        except Exception as e:
            logger.error(
                "Restoring user id=%s: list_inbounds(%s) failed: %s (%s)",
                user.id,
                tags,
                e,
                type(e).__name__,
            )
            continue
        present_tags = {i.tag for i in inbounds}
        missing = [t for t in tags if t not in present_tags]
        if missing:
            dropped_tags += len(missing)
            logger.warning(
                "Restoring user id=%s username=%s: %d inbound tag(s) "
                "no longer exist in new xray config: %s",
                user.id,
                user.username,
                len(missing),
                missing,
            )
        try:
            await storage.update_user_inbounds(user, inbounds)
        except Exception as e:
            logger.error(
                "Restoring user id=%s: update_user_inbounds failed: %s (%s)",
                user.id,
                e,
                type(e).__name__,
            )
            continue
        for inbound in inbounds:
            try:
                await add_user(user, inbound)
                restored += 1
            except EmailExistsError:
                pass
            except Exception as e:
                logger.error(
                    "Restoring user id=%s into inbound=%s failed: %s (%s)",
                    user.id,
                    inbound.tag,
                    e,
                    type(e).__name__,
                    exc_info=True,
                )
    logger.info(
        "Restored users after xray restart: %d user→inbound pushes ok, "
        "%d dropped tag(s) due to config changes",
        restored,
        dropped_tags,
    )
    return {"restored": restored, "dropped_tags": dropped_tags}


async def push_storage_users(
    storage: BaseStorage,
    inbounds: list[Inbound],
    add_user: AddUserFn,
) -> dict:
    """Push every user known to storage into the running xray.

    Logs the concrete reason for every per-user failure so that
    "0 users in xray after restart" is never silent.
    """
    added = 0
    skipped = 0
    failed = 0
    for inbound in inbounds:
        try:
            users = await storage.list_inbound_users(inbound.tag)
        except Exception as e:
            logger.error(
                "push_storage_users: storage.list_inbound_users(%s) failed: "
                "%s (%s)",
                inbound.tag,
                e,
                type(e).__name__,
                exc_info=True,
            )
            failed += 1
            continue
        for user in users:
            try:
                await add_user(user, inbound)
                added += 1
            except EmailExistsError:
                skipped += 1
            except Exception as e:
                failed += 1
                logger.error(
                    "push_storage_users: failed to add user id=%s "
                    "username=%s into inbound=%s: %s (%s)",
                    getattr(user, "id", "?"),
                    getattr(user, "username", "?"),
                    inbound.tag,
                    e,
                    type(e).__name__,
                    exc_info=True,
                )
    logger.info(
        "push_storage_users: %d inbounds processed, %d users added, "
        "%d skipped (already present), %d failed",
        len(inbounds),
        added,
        skipped,
        failed,
    )
    return {"added": added, "skipped": skipped, "failed": failed}


async def reconcile_xray_users(
    storage: BaseStorage,
    inbounds: list[Inbound],
    api,
    add_user: AddUserFn,
) -> dict:
    """Compare xray runtime users with storage and push the delta.

    This is the safety net for the race that turned node31 into
    "6823 in storage, 0 in xray": if `add_inbound_user` failed because
    xray API was briefly down (ConnectionRefused), the user stays in
    storage but never gets into xray, and the panel never retries.
    Running this periodically guarantees eventual consistency.

    Returns counters for diagnostics.
    """
    if not inbounds:
        return {"runtime_emails": 0, "storage_users": 0, "pushed": 0}

    try:
        api_stats = await api.get_users_stats(reset=False)
    except OSError as e:
        logger.warning(
            "reconcile_xray_users: xray API unreachable (%s), skipping pass",
            e,
        )
        return {"runtime_emails": 0, "storage_users": 0, "pushed": 0}
    except Exception as e:
        logger.warning(
            "reconcile_xray_users: get_users_stats failed: %s (%s)",
            e,
            type(e).__name__,
        )
        return {"runtime_emails": 0, "storage_users": 0, "pushed": 0}

    # email format used by XrayBackend.add_user is "{user.id}.{username}".
    runtime_uids: set[int] = set()
    for stat in api_stats:
        try:
            uid = int(stat.name.split(".")[0])
        except (ValueError, IndexError, AttributeError):
            continue
        runtime_uids.add(uid)

    storage_users = await storage.list_users() or []
    storage_uids = {u.id for u in storage_users}

    missing = storage_uids - runtime_uids
    if not missing:
        logger.debug(
            "reconcile_xray_users: in sync (storage=%d, runtime=%d)",
            len(storage_uids),
            len(runtime_uids),
        )
        return {
            "runtime_emails": len(runtime_uids),
            "storage_users": len(storage_uids),
            "pushed": 0,
        }

    logger.warning(
        "reconcile_xray_users: drift detected — storage has %d users, "
        "xray runtime has %d unique uids, %d missing in xray",
        len(storage_uids),
        len(runtime_uids),
        len(missing),
    )

    inbounds_by_tag = {i.tag: i for i in inbounds}
    pushed = 0
    failed = 0
    for user in storage_users:
        if user.id not in missing:
            continue
        for inbound in (user.inbounds or []):
            target = inbounds_by_tag.get(inbound.tag)
            if target is None:
                continue
            try:
                await add_user(user, target)
                pushed += 1
            except EmailExistsError:
                pass
            except (OSError, XConnectionError) as e:
                failed += 1
                logger.warning(
                    "reconcile_xray_users: still cannot push user id=%s "
                    "into inbound=%s: %s (%s) — will retry next pass",
                    user.id,
                    inbound.tag,
                    e,
                    type(e).__name__,
                )
            except Exception as e:
                failed += 1
                logger.error(
                    "reconcile_xray_users: failed to push user id=%s "
                    "into inbound=%s: %s (%s)",
                    user.id,
                    inbound.tag,
                    e,
                    type(e).__name__,
                    exc_info=True,
                )
    logger.warning(
        "reconcile_xray_users: pushed %d user→inbound entries to recover "
        "drift, %d still failing",
        pushed,
        failed,
    )
    return {
        "runtime_emails": len(runtime_uids),
        "storage_users": len(storage_uids),
        "pushed": pushed,
    }
