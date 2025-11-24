#!/bin/bash
# Quick setup script for development/testing

set -e

echo "=================================="
echo "Captive Portal Network Slicer"
echo "Quick Development Setup"
echo "=================================="

# Check if running as root
if [ "$EUID" -eq 0 ]; then 
    echo "ERROR: Do not run this script as root"
    echo "The script will use sudo when needed"
    exit 1
fi

# Check Python version
python_version=$(python3 --version 2>&1 | awk '{print $2}')
echo -e "\n[✓] Python version: $python_version"

# Install Python dependencies
echo -e "\n[1/5] Installing Python dependencies..."
pip install -q -r requirements.txt
echo "[✓] Python dependencies installed"

# Run migrations
echo -e "\n[2/5] Running database migrations..."
python manage.py makemigrations
python manage.py migrate
echo "[✓] Database migrated"

# Create superuser (if needed)
echo -e "\n[3/5] Checking for superuser..."
if python manage.py shell -c "from django.contrib.auth import get_user_model; User = get_user_model(); exit(0 if User.objects.filter(is_superuser=True).exists() else 1)" 2>/dev/null; then
    echo "[✓] Superuser already exists"
else
    echo "Creating superuser (you'll be prompted)..."
    python manage.py createsuperuser
fi

# Setup network infrastructure (requires sudo)
echo -e "\n[4/5] Setting up network infrastructure..."
echo "This requires sudo access and will:"
echo "  - Install system packages (hostapd, dnsmasq, etc.)"
echo "  - Create quarantine VLAN (br-vlan99)"
echo "  - Configure iptables"
echo ""
read -p "Continue? (y/N) " -n 1 -r
echo
if [[ $REPLY =~ ^[Yy]$ ]]; then
    sudo bash scripts/setup_quarantine.sh
    echo "[✓] Network infrastructure setup complete"
else
    echo "[!] Skipped network setup - you'll need to run it manually later:"
    echo "    sudo bash scripts/setup_quarantine.sh"
fi

# Create example network slices
echo -e "\n[5/5] Creating example network slices..."
read -p "Create example slices? (y/N) " -n 1 -r
echo
if [[ $REPLY =~ ^[Yy]$ ]]; then
    echo "Creating VLAN 10 (Corporate - 50 Mbps)..."
    sudo bash scripts/create_slice_vlan.sh 10 10.0.10.0/24 10.0.10.1 50 20
    
    echo "Creating VLAN 20 (Guest - 10 Mbps)..."
    sudo bash scripts/create_slice_vlan.sh 20 10.0.20.0/24 10.0.20.1 10 50
    
    echo "Creating VLAN 30 (IoT - 5 Mbps)..."
    sudo bash scripts/create_slice_vlan.sh 30 10.0.30.0/24 10.0.30.1 5 100
    
    echo "[✓] Example VLANs created"
    
    # Update hostapd.vlan
    echo "Updating /etc/hostapd/hostapd.vlan..."
    sudo cp config/hostapd.vlan /etc/hostapd/
    echo "[✓] hostapd.vlan updated"
    
    # Create Django NetworkSlice objects
    echo "Creating NetworkSlice objects in Django..."
    python manage.py shell <<EOF
from slicer.core.models import NetworkSlice

# Corporate slice
NetworkSlice.objects.get_or_create(
    vlan_id=10,
    defaults={
        'name': 'Corporate',
        'description': 'High-speed network for corporate devices',
        'slice_type': 'CORP',
        'bridge_interface': 'br-vlan10',
        'bandwidth_mbps': 50,
        'latency_ms': 20,
        'priority': 'HIGH',
        'subnet': '10.0.10.0/24',
        'gateway': '10.0.10.1',
        'max_devices': 50,
        'is_active': True,
        'is_default': True
    }
)

# Guest slice
NetworkSlice.objects.get_or_create(
    vlan_id=20,
    defaults={
        'name': 'Guest',
        'description': 'Guest network with moderate bandwidth',
        'slice_type': 'GUEST',
        'bridge_interface': 'br-vlan20',
        'bandwidth_mbps': 10,
        'latency_ms': 50,
        'priority': 'MEDIUM',
        'subnet': '10.0.20.0/24',
        'gateway': '10.0.20.1',
        'max_devices': 100,
        'is_active': True
    }
)

# IoT slice
NetworkSlice.objects.get_or_create(
    vlan_id=30,
    defaults={
        'name': 'IoT',
        'description': 'Low-bandwidth network for IoT devices',
        'slice_type': 'IOT',
        'bridge_interface': 'br-vlan30',
        'bandwidth_mbps': 5,
        'latency_ms': 100,
        'priority': 'LOW',
        'subnet': '10.0.30.0/24',
        'gateway': '10.0.30.1',
        'max_devices': 200,
        'is_active': True
    }
)

print("Created 3 network slices")
EOF
    echo "[✓] Django NetworkSlice objects created"
else
    echo "[!] Skipped slice creation - you can create them later via admin panel"
fi

echo ""
echo "=================================="
echo "Setup Complete!"
echo "=================================="
echo ""
echo "Next steps:"
echo ""
echo "1. Configure hostapd (if you have a wireless interface):"
echo "   sudo nano /etc/hostapd/hostapd.conf"
echo "   (Set interface, SSID password, etc.)"
echo ""
echo "2. Start services:"
echo "   sudo systemctl start hostapd"
echo "   sudo systemctl start dnsmasq"
echo ""
echo "3. Start Django development server:"
echo "   python manage.py runserver 192.168.99.1:8000"
echo ""
echo "4. Access admin panel:"
echo "   http://192.168.99.1:8000/admin/"
echo ""
echo "5. Connect a device to 'MultiSlice' WiFi and test!"
echo ""
echo "Documentation:"
echo "  - Quick Start: CAPTIVE_PORTAL_README.md"
echo "  - Migration: MIGRATION_GUIDE.md"
echo ""
