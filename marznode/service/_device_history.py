"""Device-history bookkeeping for FetchUsersStats.

Extracted from MarzService.FetchUsersStats so the gRPC handler stays
small and the device-tracking logic can be tested / changed in
isolation. Logs are kept verbose because operators currently rely on
them to debug "0 users" issues across nodes.
"""

from __future__ import annotations

import logging

from marznode.storage import BaseStorage, DeviceStorage

logger = logging.getLogger(__name__)


async def record_device_history(
    device_storage: DeviceStorage,
    storage: BaseStorage,
    total_usage: dict[int, int],
    meta: dict[int, dict[str, str]],
) -> None:
    """Persist per-user device snapshots and enforce device limits.

    `total_usage` and `meta` are the aggregated dicts FetchUsersStats
    builds from all backends. Errors are swallowed per-user so that one
    bad user never blocks the gRPC response from being sent.
    """
    logger.info("=== Saving device history and checking limits ===")
    logger.debug(
        "Total usage dict has %d users: %s",
        len(total_usage),
        list(total_usage.keys()),
    )
    logger.debug(
        "Meta dict has %d users: %s", len(meta), list(meta.keys())
    )

    all_uids = set(total_usage.keys()) | set(meta.keys())
    logger.info(
        "Processing device history for %d users "
        "(from usage: %d, from meta: %d)",
        len(all_uids),
        len(total_usage),
        len(meta),
    )

    try:
        processed = 0
        for uid in all_uids:
            processed += 1
            usage = total_usage.get(uid, 0)
            info = meta.get(uid, {})
            remote_ip = info.get("remote_ip", "")
            client_name = info.get("client_name", "unknown")

            if not remote_ip:
                continue

            try:
                storage_user = await storage.list_users(uid)
                allowed_fingerprints = None
                device_limit = None
                enforce_limit = False
                if storage_user:
                    allowed_fingerprints = storage_user.allowed_fingerprints
                    device_limit = storage_user.device_limit
                    enforce_limit = storage_user.enforce_device_limit
                    if enforce_limit and device_limit is not None:
                        logger.info(
                            "User %s has device limit enforcement: "
                            "limit=%s, allowed=%d",
                            uid,
                            device_limit,
                            len(allowed_fingerprints),
                        )

                is_allowed, reason = device_storage.update_device(
                    uid=uid,
                    remote_ip=remote_ip,
                    client_name=client_name,
                    current_usage=usage,
                    meta=info,
                    allowed_fingerprints=allowed_fingerprints,
                    device_limit=device_limit,
                    enforce_limit=enforce_limit,
                )

                if not is_allowed:
                    logger.warning(
                        "Device blocked for user %s: %s, ip=%s, client=%s",
                        uid,
                        reason,
                        remote_ip,
                        client_name,
                    )
                else:
                    logger.debug(
                        "Saved device for user %s: %s:%s",
                        uid,
                        remote_ip,
                        client_name,
                    )
            except Exception as e:
                logger.error(
                    "Error saving device history for user %s: %s",
                    uid,
                    e,
                    exc_info=True,
                )
                continue

        logger.info(
            "Processed device history for %d users (out of %d total)",
            processed,
            len(all_uids),
        )

        try:
            device_storage.mark_inactive_devices()
        except Exception as e:
            logger.error(
                "Error marking inactive devices: %s", e, exc_info=True
            )
    except Exception as e:
        logger.error(
            "Critical error in device history saving: %s", e, exc_info=True
        )
