#!/bin/bash
# Setup script for Network Slicing Captive Portal

set -e

echo "=================================="
echo "Network Slicing Captive Portal Setup"
echo "=================================="

# Check if running as root
if [ "$EUID" -ne 0 ]; then 
    echo "Please run as root (use sudo)"
    exit 1
fi

# Install required packages
echo -e "\n[1/7] Installing required packages..."
apt-get update
apt-get install -y \
    hostapd \
    dnsmasq \
    iptables \
    bridge-utils \
    iproute2 \
    wireless-tools \
    isc-dhcp-server

# Stop services during configuration
echo -e "\n[2/7] Stopping services..."
systemctl stop hostapd || true
systemctl stop dnsmasq || true

# Setup quarantine VLAN (VLAN 99)
echo -e "\n[3/7] Creating quarantine VLAN (br-vlan99)..."
if ! ip link show br-vlan99 &> /dev/null; then
    ip link add name br-vlan99 type bridge
    ip addr add 192.168.99.1/24 dev br-vlan99
    ip link set br-vlan99 up
    echo "Created br-vlan99"
else
    echo "br-vlan99 already exists"
fi

# Apply severe bandwidth restriction to quarantine VLAN (100 kbps)
echo -e "\n[4/7] Applying bandwidth restrictions to quarantine VLAN..."
tc qdisc del dev br-vlan99 root 2>/dev/null || true
tc qdisc add dev br-vlan99 root handle 1: htb default 1
tc class add dev br-vlan99 parent 1: classid 1:1 htb rate 100kbit burst 10k
tc qdisc add dev br-vlan99 parent 1:1 handle 10: netem delay 100ms
echo "Applied 100 kbps limit to br-vlan99"

# Setup iptables for captive portal redirect
echo -e "\n[5/7] Configuring iptables for captive portal..."

# Enable IP forwarding
echo 1 > /proc/sys/net/ipv4/ip_forward
if ! grep -q "net.ipv4.ip_forward=1" /etc/sysctl.conf; then
    echo "net.ipv4.ip_forward=1" >> /etc/sysctl.conf
fi

# Flush existing rules
iptables -t nat -F
iptables -t mangle -F

# Allow established connections
iptables -t nat -A PREROUTING -i br-vlan99 -m state --state ESTABLISHED,RELATED -j ACCEPT

# Redirect HTTP to Django captive portal (port 8000 for development)
# In production, use port 80 or configure nginx
DJANGO_IP="192.168.99.1"
DJANGO_PORT="8000"
iptables -t nat -A PREROUTING -i br-vlan99 -p tcp --dport 80 -j DNAT --to-destination ${DJANGO_IP}:${DJANGO_PORT}

# Redirect HTTPS to captive portal page (optional)
iptables -t nat -A PREROUTING -i br-vlan99 -p tcp --dport 443 -j DNAT --to-destination ${DJANGO_IP}:${DJANGO_PORT}

# Allow DNS queries
iptables -t nat -A PREROUTING -i br-vlan99 -p udp --dport 53 -j ACCEPT
iptables -t nat -A PREROUTING -i br-vlan99 -p tcp --dport 53 -j ACCEPT

# Masquerade for internet access
iptables -t nat -A POSTROUTING -o eth0 -j MASQUERADE

echo "iptables rules configured"

# Save iptables rules
echo -e "\n[6/7] Saving iptables rules..."
iptables-save > /etc/iptables/rules.v4 || mkdir -p /etc/iptables && iptables-save > /etc/iptables/rules.v4

# Configure DNSmasq for quarantine VLAN
echo -e "\n[7/7] Configuring DNSmasq..."
cat > /etc/dnsmasq.d/quarantine.conf <<EOF
# Quarantine VLAN DHCP
interface=br-vlan99
dhcp-range=192.168.99.100,192.168.99.200,12h
dhcp-option=br-vlan99,3,192.168.99.1
dhcp-option=br-vlan99,6,8.8.8.8,8.8.4.4

# Log DHCP assignments
log-dhcp
EOF

echo -e "\n=================================="
echo "Setup Complete!"
echo "=================================="
echo ""
echo "Next steps:"
echo "1. Configure hostapd for VLAN support (see config/hostapd.conf)"
echo "2. Run Django migrations: python manage.py migrate"
echo "3. Create network slices in Django admin"
echo "4. Start DNSmasq: systemctl start dnsmasq"
echo "5. Start hostapd: systemctl start hostapd"
echo "6. Start Django: python manage.py runserver 192.168.99.1:8000"
echo ""
echo "Quarantine VLAN: br-vlan99 (192.168.99.0/24)"
echo "Bandwidth limit: 100 kbps"
echo ""
