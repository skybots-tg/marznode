#!/usr/bin/env python3
"""
Test script to diagnose why Xray is rejecting connections.
This script checks:
1. Xray process status
2. Registered users in Xray API
3. Access log for rejected connections
4. gRPC service health
"""

import asyncio
import json
import os
import sys
import re
from datetime import datetime

# Configuration
XRAY_CONFIG_PATH = os.getenv("XRAY_CONFIG_PATH", "/var/lib/marznode/xray_config.json")
ACCESS_LOG_PATH = "/var/log/xray/access.log"
GRPC_HOST = "127.0.0.1"
GRPC_PORT = int(os.getenv("SERVICE_PORT", "53042"))


def print_section(title: str):
    print(f"\n{'='*60}")
    print(f" {title}")
    print(f"{'='*60}")


def check_xray_process():
    """Check if Xray process is running"""
    print_section("1. XRAY PROCESS STATUS")
    
    import subprocess
    result = subprocess.run(
        ["pgrep", "-a", "xray"],
        capture_output=True, text=True
    )
    
    if result.returncode == 0:
        print(f"✓ Xray is running:")
        for line in result.stdout.strip().split('\n'):
            print(f"  {line}")
    else:
        print("✗ Xray is NOT running!")
        return False
    
    return True


def check_xray_config():
    """Check Xray config file and list inbounds"""
    print_section("2. XRAY CONFIG")
    
    if not os.path.exists(XRAY_CONFIG_PATH):
        print(f"✗ Config file not found: {XRAY_CONFIG_PATH}")
        return
    
    try:
        with open(XRAY_CONFIG_PATH) as f:
            config = json.load(f)
        
        print(f"✓ Config loaded from: {XRAY_CONFIG_PATH}")
        
        # Check inbounds
        inbounds = config.get("inbounds", [])
        print(f"\nInbounds ({len(inbounds)}):")
        for i, inbound in enumerate(inbounds):
            tag = inbound.get("tag", "no-tag")
            protocol = inbound.get("protocol", "unknown")
            port = inbound.get("port", "no-port")
            listen = inbound.get("listen", "0.0.0.0")
            
            # Count users in settings
            settings = inbound.get("settings", {})
            clients = settings.get("clients", [])
            
            print(f"  [{i}] {tag}: {protocol} @ {listen}:{port}, clients: {len(clients)}")
            
            # Show first few clients
            for client in clients[:3]:
                client_id = client.get("id", "no-id")[:8]
                email = client.get("email", "no-email")
                print(f"      - {email} (id: {client_id}...)")
            
            if len(clients) > 3:
                print(f"      ... and {len(clients) - 3} more")
        
        # Check API config
        api = config.get("api", {})
        if api:
            api_tag = api.get("tag", "no-tag")
            services = api.get("services", [])
            print(f"\nAPI: tag={api_tag}, services={services}")
        else:
            print("\n⚠ No API configuration found!")
            
    except Exception as e:
        print(f"✗ Error reading config: {e}")


def check_access_log():
    """Check access log for rejected connections"""
    print_section("3. ACCESS LOG ANALYSIS")
    
    if not os.path.exists(ACCESS_LOG_PATH):
        print(f"✗ Access log not found: {ACCESS_LOG_PATH}")
        return
    
    try:
        with open(ACCESS_LOG_PATH, 'rb') as f:
            f.seek(0, 2)  # End of file
            size = f.tell()
            print(f"Log file size: {size} bytes")
            
            # Read last 50KB or whole file
            f.seek(max(0, size - 50000))
            lines = f.readlines()
        
        # Parse lines
        rejected = []
        accepted = []
        
        for line in lines[-200:]:  # Last 200 lines
            try:
                text = line.decode('utf-8', errors='ignore').strip()
                if not text:
                    continue
                
                if "rejected" in text.lower():
                    rejected.append(text)
                elif "accepted" in text.lower() or "email:" in text.lower():
                    accepted.append(text)
            except:
                pass
        
        print(f"\nLast 200 lines: {len(accepted)} accepted, {len(rejected)} rejected")
        
        # Show rejected with UUIDs
        if rejected:
            print(f"\n⚠ Rejected connections (last 10):")
            
            # Extract UUIDs from rejected messages
            uuid_pattern = re.compile(r'user id: ([0-9a-fA-F-]{36})')
            rejected_uuids = set()
            
            for line in rejected[-10:]:
                # Truncate long lines
                if len(line) > 120:
                    line = line[:120] + "..."
                print(f"  {line}")
                
                match = uuid_pattern.search(line)
                if match:
                    rejected_uuids.add(match.group(1))
            
            if rejected_uuids:
                print(f"\n⚠ UUIDs that were rejected:")
                for uuid in rejected_uuids:
                    print(f"  - {uuid}")
        
        # Show accepted
        if accepted:
            print(f"\n✓ Recent accepted connections (last 5):")
            for line in accepted[-5:]:
                if len(line) > 120:
                    line = line[:120] + "..."
                print(f"  {line}")
        else:
            print("\n⚠ No accepted user connections found!")
            
    except Exception as e:
        print(f"✗ Error reading access log: {e}")


async def check_grpc_service():
    """Check gRPC service health"""
    print_section("4. GRPC SERVICE STATUS")
    
    try:
        # Try to connect to gRPC
        import socket
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.settimeout(2)
        result = sock.connect_ex((GRPC_HOST, GRPC_PORT))
        sock.close()
        
        if result == 0:
            print(f"✓ gRPC port {GRPC_PORT} is open")
        else:
            print(f"✗ gRPC port {GRPC_PORT} is NOT reachable (error: {result})")
            return
            
    except Exception as e:
        print(f"✗ Error checking gRPC: {e}")


def check_memory_storage():
    """Check if users are loaded in memory"""
    print_section("5. STORAGE STATUS (from logs)")
    
    # Look for recent logs about user sync
    log_patterns = [
        "FetchUsersStats",
        "RepopulateUsers", 
        "SyncUsers",
        "adding user",
        "removing user",
        "Total users in response"
    ]
    
    print("Check docker logs for these patterns:")
    for pattern in log_patterns:
        print(f"  docker logs marznode 2>&1 | grep -i '{pattern}' | tail -5")


def print_diagnosis():
    """Print diagnosis and suggestions"""
    print_section("DIAGNOSIS & SUGGESTIONS")
    
    print("""
When marznode docker restarts:
1. MemoryStorage is cleared - all users are lost
2. Xray starts without any users configured
3. Marzneshin must call RepopulateUsers/SyncUsers to re-add users
4. Until sync completes, ALL connections are rejected!

SOLUTIONS:

1. Check if Marzneshin syncs users after node restart:
   - Look for "RepopulateUsers" or "SyncUsers" in logs
   
2. Manually trigger user sync in Marzneshin:
   - Restart the connection to this node in Marzneshin admin panel
   - Or restart Marzneshin service

3. Check Marzneshin logs for errors connecting to this node

4. Verify SSL certificates are correct:
   - /var/lib/marznode/client.pem should match Marzneshin's certificate

QUICK FIX:
After node restart, go to Marzneshin admin panel and:
- Navigate to Nodes
- Select this node
- Click "Reconnect" or wait for auto-reconnect
""")


async def main():
    print(f"""
╔══════════════════════════════════════════════════════════════╗
║          MARZNODE CONNECTION DIAGNOSTIC TOOL                 ║
║                                                               ║
║  Diagnosing why Xray rejects connections after restart       ║
╚══════════════════════════════════════════════════════════════╝
Time: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}
""")
    
    check_xray_process()
    check_xray_config()
    check_access_log()
    await check_grpc_service()
    check_memory_storage()
    print_diagnosis()


if __name__ == "__main__":
    asyncio.run(main())
