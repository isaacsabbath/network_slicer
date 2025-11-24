#!/bin/bash
# Quick verification script for network slice with internet access

echo "========================================="
echo "Network Slice Verification"
echo "========================================="
echo ""

# 1. Check docker0 exists
echo "1. Checking docker0 bridge..."
if ip addr show docker0 > /dev/null 2>&1; then
    echo "   ✅ docker0 exists"
    ip addr show docker0 | grep "inet "
else
    echo "   ❌ docker0 not found - is Docker running?"
    exit 1
fi
echo ""

# 2. Check QoS on docker0
echo "2. Checking QoS configuration..."
if tc qdisc show dev docker0 | grep -q "htb"; then
    echo "   ✅ QoS configured on docker0"
    tc qdisc show dev docker0
    echo ""
    tc class show dev docker0
else
    echo "   ⚠️  No QoS on docker0 yet - create a slice first"
fi
echo ""

# 3. Check NAT rules
echo "3. Checking NAT rules..."
if sudo iptables -t nat -L POSTROUTING -n | grep -q "172.17.0.0/16"; then
    echo "   ✅ NAT rule exists for docker0"
else
    echo "   ❌ NAT rule missing - containers won't have internet"
    echo "   Run: sudo iptables -t nat -A POSTROUTING -s 172.17.0.0/16 -o eth0 -j MASQUERADE"
fi
echo ""

# 4. Test container internet
echo "4. Testing container internet connectivity..."
if docker run --rm alpine ping -c 3 8.8.8.8 > /dev/null 2>&1; then
    echo "   ✅ Containers can reach internet"
else
    echo "   ❌ Containers cannot reach internet"
    echo "   Check:"
    echo "     - IP forwarding: cat /proc/sys/net/ipv4/ip_forward (should be 1)"
    echo "     - NAT rules: sudo iptables -t nat -L POSTROUTING -n"
    echo "     - FORWARD rules: sudo iptables -L FORWARD -n"
fi
echo ""

# 5. Check Django server
echo "5. Checking Django server..."
if curl -s http://localhost:8000 > /dev/null 2>&1; then
    echo "   ✅ Django is running at http://localhost:8000"
    echo "   Dashboard: http://localhost:8000/dashboard/"
else
    echo "   ❌ Django not running"
    echo "   Start with: python manage.py runserver 0.0.0.0:8000"
fi
echo ""

# 6. Check discovery containers
echo "6. Checking discovery containers..."
DISCOVERY_CONTAINERS=$(docker ps --filter "label=slice_discovery=true" --format "{{.Names}}")
if [ -n "$DISCOVERY_CONTAINERS" ]; then
    echo "   ✅ Discovery containers running:"
    echo "$DISCOVERY_CONTAINERS" | sed 's/^/      - /'
else
    echo "   ⚠️  No discovery containers - create a slice first"
fi
echo ""

echo "========================================="
echo "Quick Test Commands"
echo "========================================="
echo ""
echo "# Test container internet:"
echo "docker run --rm alpine ping -c 3 8.8.8.8"
echo ""
echo "# Test bandwidth (requires iperf3 server):"
echo "docker run --rm networkstatic/iperf3 -c <server_ip> -t 30"
echo ""
echo "# Check QoS status:"
echo "tc qdisc show dev docker0"
echo "tc class show dev docker0"
echo ""
echo "# Monitor eth0 bandwidth:"
echo "sudo iftop -i eth0"
echo ""
echo "# Access dashboard:"
echo "firefox http://localhost:8000/dashboard/ &"
echo ""
