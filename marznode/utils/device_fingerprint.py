"""Device fingerprint calculation utilities.

The module computes stable, per-device identifiers that are used by the
Marzneshin device-limit feature.  It must stay byte-compatible with
``app/utils/device_fingerprint.py`` in the Marzneshin repository, so any
algorithm change here MUST be mirrored there (and vice-versa).

Two fingerprint algorithms are supported:

* ``v1`` - legacy format.  Components are joined with ``"|"`` and hashed with
  SHA256.  Kept for backwards compatibility with existing database records.
* ``v2`` - current default.  Components are serialized as canonical JSON with
  the version baked into the payload, and the hash uses
  ``errors="replace"`` to guarantee the encoder never raises on malformed
  Unicode.  The ``client_name`` component is normalized the same way as in
  Marzneshin (see :func:`normalize_client_name`).

Callers that need to match fingerprints against a set of allowed values
should compute *all* supported versions for the current request and check
for any overlap -- see :func:`build_device_fingerprints_all` and
:func:`is_device_allowed`.

NOTE (multi-node limit leak): when several marznode instances serve the same
user, every instance independently evaluates the device limit.  Each node
can therefore admit one "new" device before Marzneshin propagates the
updated ``allowed_fingerprints`` list, so the effective limit is
``N_nodes * device_limit``.  True enforcement of a hard cap requires the
decision to be made centrally on Marzneshin; here we only refuse devices
that are explicitly absent from the allowed list when the list is already
full.
"""

from __future__ import annotations

import hashlib
import json
import logging
from typing import Iterable, Mapping, Optional, Union

logger = logging.getLogger(__name__)

__all__ = [
    "DEFAULT_FINGERPRINT_VERSION",
    "SUPPORTED_FINGERPRINT_VERSIONS",
    "build_device_fingerprint",
    "build_device_fingerprints_all",
    "normalize_client_name",
    "is_device_allowed",
    "extract_device_info_from_meta",
]

DEFAULT_FINGERPRINT_VERSION: int = 2
SUPPORTED_FINGERPRINT_VERSIONS: tuple[int, ...] = (1, 2)


_CLIENT_NAME_NORMALIZATION: dict[str, str] = {
    "v2rayng": "v2rayNG",
    "v2rayn": "v2rayN",
    "clashx": "ClashX",
    "clash for windows": "Clash for Windows",
    "clash-for-windows": "Clash for Windows",
    "shadowrocket": "Shadowrocket",
    "quantumult": "Quantumult",
    "sing-box": "sing-box",
    "matsuri": "Matsuri",
    "sagernet": "SagerNet",
    "nekobox": "NekoBox",
}


def normalize_client_name(client_name: Optional[str]) -> Optional[str]:
    """Canonicalize ``client_name`` so small differences do not fork devices.

    Must stay identical to ``normalize_client_name`` in Marzneshin.
    Returns ``None`` if the input is empty.
    """
    if not client_name:
        return None
    name = client_name.lower().strip()
    return _CLIENT_NAME_NORMALIZATION.get(name, client_name)


def _safe_encode(source: str) -> bytes:
    """Encode to UTF-8 tolerating malformed surrogate characters."""
    return source.encode("utf-8", errors="replace")


def _build_v1(
    user_id: int,
    client_name: Optional[str],
    tls_fingerprint: Optional[str],
    os_guess: Optional[str],
    user_agent: Optional[str],
) -> str:
    """Legacy algorithm - do not change.

    Byte-compatible with v1 fingerprints stored before the v2 migration.
    The only behavioural fix vs. the original is ``errors="replace"`` so
    broken UTF-8 in a user agent can no longer crash the caller; this is a
    no-op for every valid string.
    """
    components = [
        str(user_id),
        client_name or "",
        tls_fingerprint or "",
        os_guess or "",
        user_agent or "",
    ]
    source = "|".join(components)
    return hashlib.sha256(_safe_encode(source)).hexdigest()


def _build_v2(
    user_id: int,
    client_name: Optional[str],
    tls_fingerprint: Optional[str],
    os_guess: Optional[str],
    user_agent: Optional[str],
) -> str:
    """Current algorithm.

    Improvements over v1:

    * The version is part of the hashed payload, so future migrations cannot
      collide with historical v1 hashes.
    * Components are serialized as canonical JSON, which escapes the
      separator and removes the ``"a|b" + "" == "a" + "b"`` ambiguity
      present in v1.
    * ``client_name`` is run through :func:`normalize_client_name` so
      cosmetic differences (case, known synonyms) do not create duplicates.
    * Leading/trailing whitespace is stripped from textual fields and
      ``tls_fingerprint``/``os_guess`` are lower-cased for stability.
    """
    payload: dict[str, Union[int, str]] = {
        "v": 2,
        "uid": int(user_id),
        "cn": (normalize_client_name(client_name) or "").strip(),
        "tls": (tls_fingerprint or "").strip().lower(),
        "os": (os_guess or "").strip().lower(),
        "ua": (user_agent or "").strip(),
    }
    source = json.dumps(
        payload,
        sort_keys=True,
        ensure_ascii=False,
        separators=(",", ":"),
    )
    return hashlib.sha256(_safe_encode(source)).hexdigest()


_VERSION_BUILDERS = {
    1: _build_v1,
    2: _build_v2,
}


def build_device_fingerprint(
    user_id: int,
    client_name: Optional[str] = None,
    tls_fingerprint: Optional[str] = None,
    os_guess: Optional[str] = None,
    user_agent: Optional[str] = None,
    *,
    version: Optional[int] = None,
) -> tuple[str, int]:
    """Build a device fingerprint compatible with Marzneshin.

    By default returns the current version (``DEFAULT_FINGERPRINT_VERSION``).
    Pass ``version=1`` explicitly to compute the legacy fingerprint.

    Returns:
        Tuple of ``(fingerprint_hex, version)``.

    Raises:
        ValueError: if ``version`` is not supported.
    """
    resolved_version = DEFAULT_FINGERPRINT_VERSION if version is None else int(version)
    try:
        builder = _VERSION_BUILDERS[resolved_version]
    except KeyError as exc:
        raise ValueError(
            f"Unsupported fingerprint version: {resolved_version!r} "
            f"(supported: {SUPPORTED_FINGERPRINT_VERSIONS})"
        ) from exc

    fingerprint = builder(
        user_id=user_id,
        client_name=client_name,
        tls_fingerprint=tls_fingerprint,
        os_guess=os_guess,
        user_agent=user_agent,
    )

    logger.debug(
        "fingerprint computed: uid=%s version=%s hash=%s...",
        user_id,
        resolved_version,
        fingerprint[:16],
    )
    return fingerprint, resolved_version


def build_device_fingerprints_all(
    user_id: int,
    client_name: Optional[str] = None,
    tls_fingerprint: Optional[str] = None,
    os_guess: Optional[str] = None,
    user_agent: Optional[str] = None,
) -> dict[int, str]:
    """Compute fingerprints for every supported version.

    Useful when matching a request against ``allowed_fingerprints`` coming
    from Marzneshin: the list may contain either v1 or v2 hashes depending
    on when the corresponding device record was created.
    """
    return {
        version: _VERSION_BUILDERS[version](
            user_id=user_id,
            client_name=client_name,
            tls_fingerprint=tls_fingerprint,
            os_guess=os_guess,
            user_agent=user_agent,
        )
        for version in SUPPORTED_FINGERPRINT_VERSIONS
    }


def is_device_allowed(
    fingerprints: Union[str, Iterable[str]],
    allowed_fingerprints: Iterable[str],
    device_limit: Optional[int] = None,
    enforce: bool = True,
) -> tuple[bool, str]:
    """Decide whether a device may connect based on its fingerprint.

    ``fingerprints`` can be a single hash string or an iterable of hashes
    (typically both v1 and v2 for the same device) -- the device is
    considered allowed if *any* of them appears in ``allowed_fingerprints``.

    Args:
        fingerprints: Fingerprint(s) describing the current request.
        allowed_fingerprints: Hashes known to Marzneshin for this user.
        device_limit: Hard cap on the number of devices (``None`` = unlimited,
            ``0`` = no devices permitted).
        enforce: When ``False`` the check short-circuits to ``True``; this is
            handy to gate the feature behind a per-user or global flag.

    Returns:
        Tuple of ``(is_allowed, reason)``.  ``reason`` is a short English
        tag, suitable for logging and metrics.

    Examples:
        >>> is_device_allowed("abc123", ["abc123", "def456"])
        (True, 'fingerprint in allowed list')
        >>> is_device_allowed("xyz789", ["abc123"], device_limit=1)
        (False, 'device limit exceeded (1/1)')
    """
    if not enforce:
        return True, "enforcement disabled"

    if device_limit is None:
        return True, "no device limit set"

    if device_limit == 0:
        return False, "devices not allowed for this user"

    if isinstance(fingerprints, str):
        current = {fingerprints}
    else:
        current = {fp for fp in fingerprints if fp}

    allowed_set = set(allowed_fingerprints)

    if current & allowed_set:
        return True, "fingerprint in allowed list"

    if len(allowed_set) < device_limit:
        return True, "under device limit (new device)"

    return False, f"device limit exceeded ({len(allowed_set)}/{device_limit})"


def extract_device_info_from_meta(meta: Mapping[str, object]) -> dict[str, str]:
    """Project a backend metadata dict onto the subset we care about.

    Centralized here so both the storage layer and fingerprint computation
    see an identical view of the backend-provided metadata.
    """
    def _as_str(key: str) -> str:
        value = meta.get(key, "")
        return value if isinstance(value, str) else str(value or "")

    return {
        "client_name": _as_str("client_name"),
        "user_agent": _as_str("user_agent"),
        "tls_fingerprint": _as_str("tls_fingerprint"),
        "os_guess": _as_str("os_guess"),
        "protocol": _as_str("protocol"),
        "remote_ip": _as_str("remote_ip"),
    }
