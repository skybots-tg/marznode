"""Lightweight, dependency-free system metrics for marznode.

Reads CPU / RAM / load avg / uptime from /proc and disk usage via
``shutil.disk_usage``. Results are cached in-process for a short TTL so
that frequent polling from the panel never hits /proc more than once
per ``CACHE_TTL_SECONDS`` per node.

CPU% is sampled by remembering the previous /proc/stat snapshot and
diffing it against the current one. The very first call after process
start returns a percent of ``0.0`` (no baseline yet), which is fine —
the next refresh produces a real number.

Why no psutil:
- Avoids a build-time dependency (alpine wheels for psutil require gcc
  + linux-headers, which inflates the image).
- All data we need is already exposed via /proc on Linux, which is the
  only platform marznode runs on (Docker container).

The module is safe to call from async code: ``collect_stats`` does
synchronous file reads (microseconds). If you want strict isolation
from the event loop, call it via ``asyncio.to_thread``.
"""

from __future__ import annotations

import logging
import os
import shutil
import time
from dataclasses import dataclass
from threading import Lock

logger = logging.getLogger(__name__)

CACHE_TTL_SECONDS = 10.0

# Path used for "disk free" reporting. Defaults to the marznode data
# volume so the number reflects the host filesystem (the volume is
# bind-mounted from the host), not the container's overlay layer which
# would be misleading.
DISK_PATH = os.environ.get("MARZNODE_DISK_PATH", "/var/lib/marznode")
_DISK_FALLBACKS = ("/var/lib/marznode", "/var", "/")


@dataclass
class SystemStatsSnapshot:
    cpu_percent: float
    cpu_count: int
    mem_total: int
    mem_used: int
    mem_available: int
    mem_percent: float
    disk_total: int
    disk_used: int
    disk_free: int
    disk_percent: float
    load_avg_1: float
    load_avg_5: float
    load_avg_15: float
    uptime_seconds: int
    collected_at: int
    disk_path: str


_lock = Lock()
_cached: SystemStatsSnapshot | None = None
_cached_at: float = 0.0
_prev_cpu_total: int = 0
_prev_cpu_idle: int = 0


def _read_proc_stat_cpu() -> tuple[int, int]:
    """Return (total_jiffies, idle_jiffies) from the aggregate ``cpu`` line.

    Idle includes both ``idle`` and ``iowait`` columns which is the
    standard convention used by ``top`` and ``htop``.
    """
    try:
        with open("/proc/stat", "r") as f:
            line = f.readline()
    except OSError:
        return 0, 0
    parts = line.split()
    if len(parts) < 5 or parts[0] != "cpu":
        return 0, 0
    try:
        nums = [int(x) for x in parts[1:]]
    except ValueError:
        return 0, 0
    total = sum(nums)
    idle = nums[3] + (nums[4] if len(nums) > 4 else 0)
    return total, idle


def _sample_cpu_percent() -> float:
    """Compute CPU% since the last call. First call returns 0."""
    global _prev_cpu_total, _prev_cpu_idle
    total, idle = _read_proc_stat_cpu()
    prev_total, prev_idle = _prev_cpu_total, _prev_cpu_idle
    _prev_cpu_total, _prev_cpu_idle = total, idle
    if prev_total == 0 or total <= prev_total:
        return 0.0
    dt = total - prev_total
    di = idle - prev_idle
    if dt <= 0:
        return 0.0
    used = dt - di
    pct = (used / dt) * 100.0
    if pct < 0:
        return 0.0
    if pct > 100:
        return 100.0
    return round(pct, 2)


def _read_meminfo() -> dict[str, int]:
    """Parse /proc/meminfo, return a dict of kB-valued fields as bytes."""
    result: dict[str, int] = {}
    try:
        with open("/proc/meminfo", "r") as f:
            for line in f:
                key, _, rest = line.partition(":")
                rest = rest.strip()
                if not rest:
                    continue
                num = rest.split()[0]
                try:
                    result[key] = int(num) * 1024
                except ValueError:
                    continue
    except OSError:
        pass
    return result


def _sample_memory() -> tuple[int, int, int, float]:
    info = _read_meminfo()
    total = info.get("MemTotal", 0)
    available = info.get(
        "MemAvailable",
        info.get("MemFree", 0) + info.get("Cached", 0) + info.get("Buffers", 0),
    )
    used = max(total - available, 0)
    pct = round((used / total) * 100.0, 2) if total else 0.0
    return total, used, available, pct


def _resolve_disk_path() -> str:
    candidates = (DISK_PATH, *_DISK_FALLBACKS)
    for p in candidates:
        if p and os.path.exists(p):
            return p
    return "/"


def _sample_disk() -> tuple[int, int, int, float, str]:
    path = _resolve_disk_path()
    try:
        usage = shutil.disk_usage(path)
    except OSError as exc:
        logger.warning("disk_usage(%r) failed: %s", path, exc)
        return 0, 0, 0, 0.0, path
    used = usage.total - usage.free
    pct = round((used / usage.total) * 100.0, 2) if usage.total else 0.0
    return usage.total, used, usage.free, pct, path


def _sample_load_avg() -> tuple[float, float, float]:
    try:
        a, b, c = os.getloadavg()
        return round(a, 2), round(b, 2), round(c, 2)
    except (OSError, AttributeError):
        return 0.0, 0.0, 0.0


def _sample_uptime() -> int:
    try:
        with open("/proc/uptime", "r") as f:
            up = float(f.read().split()[0])
            return int(up)
    except (OSError, ValueError):
        return 0


def _sample_cpu_count() -> int:
    try:
        return os.cpu_count() or 1
    except Exception:
        return 1


def _build_snapshot() -> SystemStatsSnapshot:
    cpu_pct = _sample_cpu_percent()
    mem_total, mem_used, mem_avail, mem_pct = _sample_memory()
    disk_total, disk_used, disk_free, disk_pct, disk_path = _sample_disk()
    la1, la5, la15 = _sample_load_avg()
    return SystemStatsSnapshot(
        cpu_percent=cpu_pct,
        cpu_count=_sample_cpu_count(),
        mem_total=mem_total,
        mem_used=mem_used,
        mem_available=mem_avail,
        mem_percent=mem_pct,
        disk_total=disk_total,
        disk_used=disk_used,
        disk_free=disk_free,
        disk_percent=disk_pct,
        load_avg_1=la1,
        load_avg_5=la5,
        load_avg_15=la15,
        uptime_seconds=_sample_uptime(),
        collected_at=int(time.time()),
        disk_path=disk_path,
    )


def collect_stats(force: bool = False) -> SystemStatsSnapshot:
    """Return a system snapshot, possibly served from the in-process cache.

    The cache TTL is ``CACHE_TTL_SECONDS``. Pass ``force=True`` to
    bypass it (rarely useful — the panel polls infrequently anyway).
    """
    global _cached, _cached_at
    now = time.monotonic()
    with _lock:
        if (
            not force
            and _cached is not None
            and (now - _cached_at) < CACHE_TTL_SECONDS
        ):
            return _cached
        snapshot = _build_snapshot()
        _cached = snapshot
        _cached_at = now
        return snapshot
