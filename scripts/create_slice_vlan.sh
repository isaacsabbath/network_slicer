#!/bin/bash
# Create and configure a network slice VLAN

if [ "$#" -lt 4 ]; then
    echo "Usage: $0 <vlan_id> <subnet> <gateway> <bandwidth_mbps> [latency_ms]"
    echo "Example: $0 10 10.0.10.0/24 10.0.10.1 50 20"
    exit 1
fi

VLAN_ID=$1
SUBNET=$2
GATEWAY=$3
BANDWIDTH_MBPS=$4
LATENCY_MS=${5:-50}

BRIDGE_NAME="br-vlan${VLAN_ID}"

# Check if running as root
if [ "$EUID" -ne 0 ]; then 
    echo "Please run as root (use sudo)"
    exit 1
fi

echo "Creating network slice VLAN ${VLAN_ID}..."

# Create bridge
if ! ip link show ${BRIDGE_NAME} &> /dev/null; then
    ip link add name ${BRIDGE_NAME} type bridge
    echo "Created bridge ${BRIDGE_NAME}"
else
    echo "Bridge ${BRIDGE_NAME} already exists"
fi

# Assign IP address
SUBNET_MASK=$(echo ${SUBNET} | cut -d'/' -f2)
ip addr flush dev ${BRIDGE_NAME}
ip addr add ${GATEWAY}/${SUBNET_MASK} dev ${BRIDGE_NAME}
echo "Assigned IP ${GATEWAY}/${SUBNET_MASK} to ${BRIDGE_NAME}"

# Bring bridge up
ip link set ${BRIDGE_NAME} up
echo "Bridge ${BRIDGE_NAME} is up"

# Apply bandwidth limits using tc (HTB + netem)
echo "Applying QoS: ${BANDWIDTH_MBPS} Mbps bandwidth, ${LATENCY_MS} ms latency"

# Remove existing qdisc
tc qdisc del dev ${BRIDGE_NAME} root 2>/dev/null || true

# Calculate rate in kbit
RATE_KBIT=$((BANDWIDTH_MBPS * 1024))
BURST_KB=$((RATE_KBIT / 8))

# Create HTB root qdisc
tc qdisc add dev ${BRIDGE_NAME} root handle 1: htb default 1

# Create HTB class with bandwidth limit
tc class add dev ${BRIDGE_NAME} parent 1: classid 1:1 htb rate ${RATE_KBIT}kbit burst ${BURST_KB}k

# Add netem for latency
if [ ${LATENCY_MS} -gt 0 ]; then
    tc qdisc add dev ${BRIDGE_NAME} parent 1:1 handle 10: netem delay ${LATENCY_MS}ms
fi

echo "QoS configured successfully"

# Verify QoS
echo ""
echo "Verifying QoS configuration:"
tc class show dev ${BRIDGE_NAME}

# Configure DNSmasq for this VLAN
DHCP_START=$(echo ${SUBNET} | cut -d'/' -f1 | sed 's/\.[0-9]*$/.100/')
DHCP_END=$(echo ${SUBNET} | cut -d'/' -f1 | sed 's/\.[0-9]*$/.200/')

echo ""
echo "Creating DNSmasq configuration..."
cat > /etc/dnsmasq.d/vlan${VLAN_ID}.conf <<EOF
# VLAN ${VLAN_ID} DHCP
interface=${BRIDGE_NAME}
dhcp-range=${DHCP_START},${DHCP_END},12h
dhcp-option=${BRIDGE_NAME},3,${GATEWAY}
dhcp-option=${BRIDGE_NAME},6,8.8.8.8,8.8.4.4
EOF

echo "Created /etc/dnsmasq.d/vlan${VLAN_ID}.conf"

# Restart DNSmasq
systemctl restart dnsmasq
echo "DNSmasq restarted"

# Setup NAT for internet access
iptables -t nat -A POSTROUTING -o eth0 -j MASQUERADE

echo ""
echo "=================================="
echo "Network Slice VLAN ${VLAN_ID} Created Successfully!"
echo "=================================="
echo "Bridge: ${BRIDGE_NAME}"
echo "Subnet: ${SUBNET}"
echo "Gateway: ${GATEWAY}"
echo "Bandwidth: ${BANDWIDTH_MBPS} Mbps"
echo "Latency: ${LATENCY_MS} ms"
echo "DHCP Range: ${DHCP_START} - ${DHCP_END}"
echo ""
