"""Fail-soft helpers for loading the xray config file.

If ``xray_config.json`` is missing or unreadable at start time we don't
want marznode to crash on ``FileNotFoundError`` — that takes the entire
node offline and prevents the panel from pushing a fresh config via
``RestartBackend``. Instead, we briefly retry (handles slow volume
mounts), then fall back to a minimal in-memory config so xray can boot
and stay reachable.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os

logger = logging.getLogger(__name__)


# Minimal config used when xray_config.json is missing or unreadable.
# Has no user-facing inbounds — _apply_api() will inject API_INBOUND so
# xray can still start and the panel can later push a real config via
# RestartBackend.
FAILSAFE_CONFIG: dict = {
    "log": {"loglevel": "warning"},
    "inbounds": [],
    "outbounds": [
        {"protocol": "freedom", "tag": "direct"},
        {"protocol": "blackhole", "tag": "block"},
    ],
    "routing": {"rules": []},
}

CONFIG_LOAD_RETRY_DELAY = 1.0
CONFIG_LOAD_MAX_WAIT = 10.0


def failsafe_config_json() -> str:
    """Serialized form of FAILSAFE_CONFIG, suitable for save_config()."""
    return json.dumps(FAILSAFE_CONFIG, indent=2)


async def read_config_with_failsafe(
    config_path: str,
    save_config,
    *,
    retry_delay: float = CONFIG_LOAD_RETRY_DELAY,
    max_wait: float = CONFIG_LOAD_MAX_WAIT,
) -> str:
    """Read xray config from disk, fail-soft.

    On ``FileNotFoundError`` retry until ``max_wait`` seconds have
    elapsed (handles slow mounts). If still missing, persist a minimal
    failsafe config via ``save_config(...)`` and return it so xray can
    still start. ``save_config`` is the backend's own writer (kept
    injected so this module stays storage-agnostic).
    """
    deadline = asyncio.get_event_loop().time() + max_wait
    last_error: Exception | None = None
    while True:
        try:
            with open(config_path) as f:
                return f.read()
        except FileNotFoundError as e:
            last_error = e
            if asyncio.get_event_loop().time() >= deadline:
                break
            logger.warning(
                "xray config not found at %s, retrying in %.1fs",
                config_path,
                retry_delay,
            )
            await asyncio.sleep(retry_delay)
        except OSError as e:
            last_error = e
            break

    logger.error(
        "Could not read xray config at %s after %.1fs (%s); "
        "starting xray with a minimal failsafe config so the node "
        "stays reachable and the panel can push a real config",
        config_path,
        max_wait,
        last_error,
    )
    failsafe = failsafe_config_json()
    try:
        os.makedirs(os.path.dirname(config_path) or ".", exist_ok=True)
        save_config(failsafe)
    except OSError as e:
        logger.warning(
            "Could not persist failsafe config to %s: %s",
            config_path,
            e,
        )
    return failsafe
