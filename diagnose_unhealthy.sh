#!/bin/bash
#
# Diagnose why marznode becomes unhealthy after restart
#
# Usage: ./diagnose_unhealthy.sh
#        docker exec -it marznode bash -c "cd /app && ./diagnose_unhealthy.sh"
#

echo "=============================================="
echo "  MARZNODE UNHEALTHY DIAGNOSTIC"
echo "=============================================="
echo ""

# 1. Check if Xray is running
echo "1. Checking Xray process..."
if pgrep -x "xray" > /dev/null; then
    echo "   ✓ Xray is running"
    pgrep -a xray
else
    echo "   ✗ Xray is NOT running!"
fi
echo ""

# 2. Check access log for rejected connections
echo "2. Checking access log for rejected UUIDs..."
if [ -f /var/log/xray/access.log ]; then
    echo "   Log file size: $(wc -c < /var/log/xray/access.log) bytes"
    echo ""
    echo "   Last 5 rejected connections:"
    grep -i "rejected" /var/log/xray/access.log | tail -5 | while read line; do
        echo "   $line"
    done
    echo ""
    echo "   Rejected UUIDs:"
    grep -oP 'user id: \K[0-9a-fA-F-]{36}' /var/log/xray/access.log | sort -u | head -10
else
    echo "   ✗ Access log not found: /var/log/xray/access.log"
fi
echo ""

# 3. Check marznode logs for user sync
echo "3. Checking for user synchronization in logs..."
echo "   Looking for RepopulateUsers/SyncUsers calls..."
docker logs marznode 2>&1 | grep -E "(RepopulateUsers|SyncUsers|adding user|Total users)" | tail -20
echo ""

# 4. Check gRPC port
echo "4. Checking gRPC service..."
GRPC_PORT=${SERVICE_PORT:-53042}
if nc -z 127.0.0.1 $GRPC_PORT 2>/dev/null; then
    echo "   ✓ gRPC port $GRPC_PORT is open"
else
    echo "   ✗ gRPC port $GRPC_PORT is NOT open"
fi
echo ""

# 5. Show recent marznode errors
echo "5. Recent marznode errors:"
docker logs marznode 2>&1 | grep -iE "(error|warning|failed)" | tail -10
echo ""

# 6. Diagnosis
echo "=============================================="
echo "  DIAGNOSIS"
echo "=============================================="
echo ""
echo "If you see 'invalid request user id' errors, it means:"
echo "  1. Xray started without users"
echo "  2. Marzneshin hasn't synced users yet"
echo ""
echo "SOLUTIONS:"
echo "  1. Wait 30-60 seconds after restart for Marzneshin to sync"
echo "  2. In Marzneshin admin panel, go to Nodes and click 'Reconnect'"
echo "  3. Restart Marzneshin: docker restart marzneshin"
echo ""
echo "To manually test connection:"
echo "  docker exec -it marznode python3 test_connection.py"
echo ""
