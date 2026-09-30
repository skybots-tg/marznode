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

import asyncio
import logging
from collections import Counter
from typing import Awaitable, Callable

from marznode.backends.xray.api.exceptions import (
    EmailExistsError,
    TagNotFoundError,
    UnimplementedError,
    XConnectionError,
    XrayError,
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


# How long a gap between storage and xray has to persist before it is
# pushed. The service takes a revoked user out of xray first and out of
# storage second; a push into that window would put the user back into
# xray with nothing left to take them out again.
RECONCILE_CONFIRM_DELAY = 5.0

# Xray refuses GetInboundUsers for inbounds whose proxy can't hold users;
# add_user can't put anyone there either, so such inbounds aren't checked.
_NOT_A_USER_MANAGER = "not a UserManager"

# Which way of reading xray membership was last announced in the log:
# once per switch, not every pass.
_announced_mode: str | None = None

Membership = dict[str, set[int]]
Gaps = dict[tuple[int, str], User]


def _uid(email: str) -> int | None:
    # email format used by XrayBackend.add_user is "{user.id}.{username}".
    try:
        return int(email.split(".")[0])
    except (ValueError, AttributeError):
        return None


def _announce(mode: str, level: int, message: str, *args) -> None:
    global _announced_mode
    if _announced_mode != mode:
        _announced_mode = mode
        logger.log(level, message, *args)


def _preview(uids: list[int], limit: int = 20) -> str:
    shown = ", ".join(map(str, uids[:limit]))
    return shown if len(uids) <= limit else f"{shown} … (+{len(uids) - limit})"


async def _read_membership(api, tags: list[str]) -> Membership:
    """uids per inbound tag as xray holds them; unreadable tags left out.

    Raises UnimplementedError when the core has no GetInboundUsers;
    connection-level failures propagate and end the pass.
    """
    membership: Membership = {}
    for tag in tags:
        try:
            emails = await api.get_inbound_users(tag)
        except (UnimplementedError, XConnectionError):
            raise
        except TagNotFoundError:
            logger.warning(
                "reconcile_xray_users: inbound '%s' is not in running xray, "
                "not checked",
                tag,
            )
            continue
        except XrayError as e:
            permanent = _NOT_A_USER_MANAGER in (e.details or "")
            logger.log(
                logging.DEBUG if permanent else logging.WARNING,
                "reconcile_xray_users: can't read users of inbound '%s': "
                "%s (%s)",
                tag,
                e.details,
                type(e).__name__,
            )
            continue
        membership[tag] = {uid for uid in map(_uid, emails) if uid is not None}
    return membership


def _find_gaps(storage_users: list[User], membership: Membership) -> Gaps:
    """(uid, tag) pairs that storage expects in xray and xray doesn't hold.

    Only tags present in ``membership`` are compared: the rest belong to
    another backend or couldn't be read.
    """
    gaps: Gaps = {}
    for user in storage_users:
        for inbound in user.inbounds or []:
            present = membership.get(inbound.tag)
            if present is not None and user.id not in present:
                gaps[(user.id, inbound.tag)] = user
    return gaps


async def reconcile_xray_users(
    storage: BaseStorage,
    inbounds: list[Inbound],
    api,
    add_user: AddUserFn,
    confirm_delay: float = RECONCILE_CONFIRM_DELAY,
) -> dict:
    """Compare what each xray inbound holds with storage and push the gap.

    This is the safety net for the race that turned node31 into
    "6823 in storage, 0 in xray": if `add_inbound_user` failed because
    xray API was briefly down (ConnectionRefused), the user stays in
    storage but never gets into xray, and the panel never retries.
    Running this periodically guarantees eventual consistency.

    Membership is read per inbound with GetInboundUsers. The stats
    counters used before exist only for users that carried traffic since
    the last reset, so idle users looked missing: every pass "found"
    hundreds of them and re-added each one into EmailExistsError. Cores
    without GetInboundUsers still get that stats-based pass.

    Returns counters for diagnostics.
    """
    result = {
        "mode": "inbound_users",
        "runtime_emails": 0,
        "storage_users": 0,
        "missing": 0,
        "pushed": 0,
        "failed": 0,
    }
    if not inbounds:
        return result

    try:
        membership = await _read_membership(api, [i.tag for i in inbounds])
    except UnimplementedError as e:
        return await _reconcile_by_stats(storage, inbounds, api, add_user, e.details)
    except Exception as e:
        logger.warning(
            "reconcile_xray_users: can't read xray inbound users: %s (%s), "
            "skipping pass",
            e,
            type(e).__name__,
        )
        return result

    storage_users = await storage.list_users() or []
    gaps = _find_gaps(storage_users, membership)
    result["runtime_emails"] = len(set().union(*membership.values()))
    result["storage_users"] = len(storage_users)
    _announce(
        "inbound_users",
        logging.INFO,
        "reconcile_xray_users: checking storage against GetInboundUsers of "
        "%d/%d inbound(s); storage has %d users, xray %d",
        len(membership),
        len(inbounds),
        len(storage_users),
        result["runtime_emails"],
    )
    if not gaps:
        logger.debug(
            "reconcile_xray_users: in sync (storage=%d users, %d inbounds "
            "checked)",
            len(storage_users),
            len(membership),
        )
        return result

    await asyncio.sleep(confirm_delay)
    try:
        membership = await _read_membership(api, sorted({tag for _, tag in gaps}))
    except Exception as e:
        logger.warning(
            "reconcile_xray_users: re-check failed: %s (%s), %d gap(s) left "
            "for the next pass",
            e,
            type(e).__name__,
            len(gaps),
        )
        return result
    fresh = _find_gaps(await storage.list_users() or [], membership)
    confirmed = gaps.keys() & fresh.keys()
    if not confirmed:
        logger.info(
            "reconcile_xray_users: %d gap(s) closed by themselves within "
            "%.0fs (a panel update was in flight)",
            len(gaps),
            confirm_delay,
        )
        return result
    gaps = {pair: fresh[pair] for pair in sorted(confirmed)}
    result["missing"] = len(gaps)

    uids = sorted({uid for uid, _ in gaps})
    logger.warning(
        "reconcile_xray_users: drift detected — %d user→inbound pair(s) of "
        "%d user(s) are in storage but not in xray: %s; uids: %s",
        len(gaps),
        len(uids),
        dict(Counter(tag for _, tag in gaps)),
        _preview(uids),
    )

    inbounds_by_tag = {i.tag: i for i in inbounds}
    already = 0
    for (uid, tag), user in gaps.items():
        try:
            await add_user(user, inbounds_by_tag[tag])
            result["pushed"] += 1
        except EmailExistsError:
            already += 1
        except (OSError, XConnectionError) as e:
            result["failed"] += 1
            logger.warning(
                "reconcile_xray_users: still cannot push user id=%s "
                "into inbound=%s: %s (%s) — will retry next pass",
                uid,
                tag,
                e,
                type(e).__name__,
            )
        except Exception as e:
            result["failed"] += 1
            logger.error(
                "reconcile_xray_users: failed to push user id=%s "
                "into inbound=%s: %s (%s)",
                uid,
                tag,
                e,
                type(e).__name__,
                exc_info=True,
            )
    logger.warning(
        "reconcile_xray_users: pushed %d user→inbound entries to recover "
        "drift, %d already present, %d still failing",
        result["pushed"],
        already,
        result["failed"],
    )
    return result


async def _reconcile_by_stats(
    storage: BaseStorage,
    inbounds: list[Inbound],
    api,
    add_user: AddUserFn,
    reason: str | None,
) -> dict:
    """The pass for cores without GetInboundUsers: runtime from stats.

    Stats counters exist only for users that carried traffic since the
    last reset, so idle users look missing here and get re-added into
    EmailExistsError on every pass. Kept only for Xray older than 25.x.
    """
    _announce(
        "stats",
        logging.WARNING,
        "reconcile_xray_users: xray has no GetInboundUsers (%s), falling "
        "back to stats — idle users will show up as drift",
        reason,
    )

    try:
        api_stats = await api.get_users_stats(reset=False)
    except OSError as e:
        logger.warning(
            "reconcile_xray_users: xray API unreachable (%s), skipping pass",
            e,
        )
        return {"mode": "stats", "runtime_emails": 0, "storage_users": 0, "pushed": 0}
    except Exception as e:
        logger.warning(
            "reconcile_xray_users: get_users_stats failed: %s (%s)",
            e,
            type(e).__name__,
        )
        return {"mode": "stats", "runtime_emails": 0, "storage_users": 0, "pushed": 0}

    runtime_uids: set[int] = set()
    for stat in api_stats:
        uid = _uid(stat.name)
        if uid is not None:
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
            "mode": "stats",
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
        "mode": "stats",
        "runtime_emails": len(runtime_uids),
        "storage_users": len(storage_uids),
        "pushed": pushed,
    }
