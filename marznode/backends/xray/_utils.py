"""xray utilities"""

import logging
import re
import subprocess
from typing import Dict

logger = logging.getLogger(__name__)


def get_version(xray_path: str) -> str | None:
    """
    get xray version by running its executable
    :param xray_path:
    :return: xray version
    """
    cmd = [xray_path, "version"]
    output = subprocess.check_output(cmd, stderr=subprocess.STDOUT).decode()
    match = re.match(r"^Xray (\d+\.\d+\.\d+)", output)
    if match:
        return match.group(1)
    return None


# Historically Xray-core has renamed the public-key label several times:
#   v1.8:    "Private key" / "Public key"
#   v24+:    "PrivateKey"  / "PublicKey"
#   v25+:    "PrivateKey"  / "Password"
#   v26.2+:  "PrivateKey"  / "Password (PublicKey)"
# To avoid breaking on future renames we parse every "key: value" line into
# a dict and look up well-known private/public key labels.
_PRIVATE_KEY_LABELS = ("privatekey", "private key")
_PUBLIC_KEY_LABELS = (
    "password (publickey)",
    "password(publickey)",
    "password",
    "publickey",
    "public key",
)


def _parse_x25519_output(output: str) -> Dict[str, str]:
    """Parse ``xray x25519`` stdout into a normalized key-value dict.

    Keys are lowercased and stripped of surrounding whitespace so that the
    caller can look them up regardless of the current Xray-core label style.
    """
    parsed: Dict[str, str] = {}
    for raw_line in output.splitlines():
        line = raw_line.strip()
        if not line or ":" not in line:
            continue
        key, _, value = line.partition(":")
        key = key.strip().lower()
        value = value.strip()
        if key and value and key not in parsed:
            parsed[key] = value
    return parsed


def get_x25519(xray_path: str, private_key: str = None) -> Dict[str, str] | None:
    """
    get x25519 public key using the private key
    :param xray_path:
    :param private_key:
    :return: x25519 publickey with private_key and public_key
    """
    result, _ = get_x25519_with_error(xray_path, private_key)
    return result


def get_x25519_with_error(
    xray_path: str, private_key: str = None
) -> tuple[Dict[str, str] | None, str | None]:
    """
    Same as :func:`get_x25519` but additionally returns a human-readable
    reason string when key derivation fails. The reason contains xray's own
    stderr/stdout (when available) so the caller can surface it to the user.
    """
    try:
        cmd = [xray_path, "x25519"]
        if private_key:
            cmd.extend(["-i", private_key])
        output = subprocess.check_output(cmd, stderr=subprocess.STDOUT).decode("utf-8")

        parsed = _parse_x25519_output(output)

        private = next(
            (parsed[label] for label in _PRIVATE_KEY_LABELS if label in parsed),
            None,
        )
        public = next(
            (parsed[label] for label in _PUBLIC_KEY_LABELS if label in parsed),
            None,
        )

        if private and public:
            return {"private_key": private, "public_key": public}, None

        reason = (
            "xray x25519 output did not contain recognizable private/public key "
            f"labels. Raw output:\n{output.strip()}"
        )
        logger.warning(reason)
        return None, reason
    except FileNotFoundError as e:
        reason = f"xray executable not found at '{xray_path}': {e}"
        logger.error(reason)
        return None, reason
    except subprocess.CalledProcessError as e:
        xray_output = (
            e.output.decode("utf-8", errors="replace").strip() if e.output else ""
        )
        reason = (
            f"`xray x25519` exited with code {e.returncode}."
            + (f" Output:\n{xray_output}" if xray_output else "")
        )
        logger.error(reason)
        return None, reason
    except Exception as e:
        logger.exception("Unexpected error in get_x25519: %s", e)
        return None, f"Unexpected error while running xray x25519: {e}"
