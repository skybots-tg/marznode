"""Device fingerprint calculation utilities

This module provides device fingerprint calculation that is compatible
with Marzneshin's fingerprinting logic.

CRITICAL: The algorithm MUST be identical to app/utils/device_fingerprint.py in Marzneshin
"""

import hashlib
import logging
from typing import Optional

logger = logging.getLogger(__name__)


def build_device_fingerprint(
    user_id: int,
    client_name: Optional[str] = None,
    tls_fingerprint: Optional[str] = None,
    os_guess: Optional[str] = None,
    user_agent: Optional[str] = None,
) -> tuple[str, int]:
    """
    Build a device fingerprint from connection metadata.
    
    This function MUST produce the same fingerprint as Marzneshin's
    build_device_fingerprint function to ensure compatibility.
    
    Args:
        user_id: User ID
        client_name: Client name (e.g., "v2rayNG", "Shadowrocket")
        tls_fingerprint: TLS fingerprint from connection
        os_guess: Guessed operating system
        user_agent: User agent string
        
    Returns:
        Tuple of (fingerprint_hash, version)
        
    Example:
        >>> fingerprint, version = build_device_fingerprint(
        ...     user_id=123,
        ...     client_name="v2rayNG",
        ...     tls_fingerprint="abc123",
        ... )
        >>> print(fingerprint)
        'a1b2c3d4e5f6...'
    """
    version = 1
    
    # Build components list - order matters!
    components = [
        str(user_id),
        client_name or "",
        tls_fingerprint or "",
        os_guess or "",
        user_agent or "",
    ]
    
    # Join with pipe separator
    source = "|".join(components)
    
    # Calculate SHA256 hash
    fingerprint = hashlib.sha256(source.encode()).hexdigest()
    
    logger.debug(
        f"Generated fingerprint for user {user_id}: "
        f"source='{source[:100]}...', fingerprint={fingerprint[:16]}..."
    )
    
    return fingerprint, version


def calculate_device_fingerprint_simple(
    remote_ip: str,
    client_name: str,
    user_agent: Optional[str] = None,
    tls_fingerprint: Optional[str] = None,
) -> str:
    """
    Simplified fingerprint calculation using only available metadata.
    
    This is used when we don't have user_id in the context.
    
    Args:
        remote_ip: Remote IP address (not included in hash for privacy)
        client_name: Client name
        user_agent: User agent string
        tls_fingerprint: TLS fingerprint
        
    Returns:
        Fingerprint hash as hex string
    """
    components = [
        client_name or "",
        tls_fingerprint or "",
        user_agent or "",
    ]
    
    source = "|".join(components)
    fingerprint = hashlib.sha256(source.encode()).hexdigest()
    
    return fingerprint


def is_device_allowed(
    fingerprint: str,
    allowed_fingerprints: list[str],
    device_limit: Optional[int] = None,
    enforce: bool = True,
) -> tuple[bool, str]:
    """
    Check if a device is allowed to connect based on fingerprint.
    
    Args:
        fingerprint: Device fingerprint to check
        allowed_fingerprints: List of allowed fingerprints
        device_limit: Device limit (None = no limit)
        enforce: Whether to enforce device limit
        
    Returns:
        Tuple of (is_allowed, reason)
        
    Examples:
        >>> is_device_allowed("abc123", ["abc123", "def456"])
        (True, "fingerprint in allowed list")
        
        >>> is_device_allowed("xyz789", ["abc123"], device_limit=1, enforce=True)
        (False, "device limit exceeded")
    """
    # If enforcement is disabled, allow all
    if not enforce:
        return True, "enforcement disabled"
    
    # If no device limit is set, allow all
    if device_limit is None:
        return True, "no device limit set"
    
    # If device limit is 0, block all
    if device_limit == 0:
        return False, "devices not allowed for this user"
    
    # Check if fingerprint is in allowed list
    if fingerprint in allowed_fingerprints:
        return True, "fingerprint in allowed list"
    
    # Check if we're under the limit
    if len(allowed_fingerprints) < device_limit:
        # This is a new device and we're under the limit
        # In this case, we should allow it and let Marzneshin register it
        return True, "under device limit (new device)"
    
    # Device limit exceeded
    return False, f"device limit exceeded ({len(allowed_fingerprints)}/{device_limit})"


def extract_device_info_from_meta(meta: dict) -> dict:
    """
    Extract device information from metadata dictionary.
    
    Args:
        meta: Metadata dictionary from backend
        
    Returns:
        Dictionary with extracted device info
    """
    return {
        "client_name": meta.get("client_name", ""),
        "user_agent": meta.get("user_agent", ""),
        "tls_fingerprint": meta.get("tls_fingerprint", ""),
        "protocol": meta.get("protocol", ""),
        "remote_ip": meta.get("remote_ip", ""),
    }



