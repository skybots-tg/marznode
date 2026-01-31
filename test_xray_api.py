#!/usr/bin/env python3
"""
Test script to directly query Xray API and see registered users.
This helps diagnose why connections are rejected - if users are not
registered in Xray API, they will be rejected.
"""

import asyncio
import json
import os
import sys

# Add project to path
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from marznode.backends.xray.api import XrayAPI


async def list_users_from_api(api_port: int = None):
    """List all users registered in Xray API"""
    
    if api_port is None:
        # Try to find API port from running xray
        print("Trying to find Xray API port...")
        
        import subprocess
        result = subprocess.run(
            ["netstat", "-tlnp"],
            capture_output=True, text=True
        )
        
        # Look for xray listening ports
        for line in result.stdout.split('\n'):
            if 'xray' in line.lower() or '/xray' in line:
                print(f"  Found: {line.strip()}")
        
        print("\nNote: API port is typically a random port set at startup.")
        print("Check marznode logs for the actual API port.")
        return
    
    print(f"\n{'='*60}")
    print(f" Connecting to Xray API at 127.0.0.1:{api_port}")
    print(f"{'='*60}")
    
    try:
        api = XrayAPI("127.0.0.1", api_port)
        
        # Get stats (this lists all users)
        stats = await api.get_users_stats(reset=False)
        
        print(f"\n✓ Connected to Xray API")
        print(f"Total stats entries: {len(stats)}")
        
        # Group by user
        users = {}
        for stat in stats:
            # Format: "user>>>uid.username>>>traffic>>>uplink/downlink"
            parts = stat.name.split(">>>")
            if len(parts) >= 2:
                user_email = parts[1]
                if user_email not in users:
                    users[user_email] = {"uplink": 0, "downlink": 0}
                
                if len(parts) >= 4:
                    if parts[3] == "uplink":
                        users[user_email]["uplink"] += stat.value
                    elif parts[3] == "downlink":
                        users[user_email]["downlink"] += stat.value
        
        print(f"\nRegistered users ({len(users)}):")
        if not users:
            print("  ⚠ NO USERS REGISTERED!")
            print("  This is why connections are rejected - Xray doesn't know any users.")
            print("  Marzneshin needs to sync users via RepopulateUsers/SyncUsers.")
        else:
            for email, traffic in list(users.items())[:20]:
                up = traffic["uplink"]
                down = traffic["downlink"]
                print(f"  - {email}: up={up}, down={down}")
            
            if len(users) > 20:
                print(f"  ... and {len(users) - 20} more users")
        
    except ConnectionRefusedError:
        print(f"✗ Cannot connect to Xray API at port {api_port}")
        print("  The port might be wrong or Xray is not running")
    except Exception as e:
        print(f"✗ Error: {e}")


async def main():
    import argparse
    parser = argparse.ArgumentParser(description='Test Xray API connection')
    parser.add_argument('--port', type=int, help='Xray API port (check marznode logs)')
    args = parser.parse_args()
    
    print("""
╔═══════════════════════════════════════════════════════════════╗
║               XRAY API USER DIAGNOSTIC                        ║
╚═══════════════════════════════════════════════════════════════╝
""")
    
    await list_users_from_api(args.port)
    
    print("""
═══════════════════════════════════════════════════════════════
HOW TO FIND THE API PORT:

1. Check marznode startup logs:
   docker logs marznode 2>&1 | grep -i "api"
   
2. Or look for XrayAPI initialization in logs

3. The port changes on each restart (it's dynamically assigned)

═══════════════════════════════════════════════════════════════
""")


if __name__ == "__main__":
    asyncio.run(main())
