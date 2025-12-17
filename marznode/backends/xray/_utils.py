"""xray utilities"""

import re
import subprocess
from typing import Dict


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


def get_x25519(xray_path: str, private_key: str = None) -> Dict[str, str] | None:
    """
    get x25519 public key using the private key
    :param xray_path:
    :param private_key:
    :return: x25519 publickey with private_key and public_key
    """
    try:
        cmd = [xray_path, "x25519"]
        if private_key:
            cmd.extend(["-i", private_key])
        output = subprocess.check_output(cmd, stderr=subprocess.STDOUT).decode("utf-8")
        
        # New format (Xray 1.8+):
        # PrivateKey: ...
        # Password: ...  (this is the public key for Reality)
        # Hash32: ...
        new_format = re.search(r"PrivateKey:\s*(\S+)\s+Password:\s*(\S+)", output, re.IGNORECASE)
        if new_format:
            private, public = new_format.groups()
            return {"private_key": private.strip(), "public_key": public.strip()}
        
        # Old format (Xray < 1.8):
        # Private key: ...
        # Public key: ...
        old_format = re.search(r"Private key:\s*(\S+)\s+Public key:\s*(\S+)", output, re.IGNORECASE)
        if old_format:
            private, public = old_format.groups()
            return {"private_key": private.strip(), "public_key": public.strip()}
        
        # If no pattern matched, print output for debugging
        print(f"Warning: Could not parse x25519 output: {output}")
        return None
    except subprocess.CalledProcessError as e:
        print(f"Error executing xray x25519: {e}")
        print(f"Command output: {e.output.decode('utf-8') if e.output else 'N/A'}")
        return None
    except Exception as e:
        print(f"Unexpected error in get_x25519: {e}")
        return None
