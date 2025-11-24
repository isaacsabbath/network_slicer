# Network Slicer

A Django-based network slicing management system that provides QoS-controlled network slices using Docker containers and WiFi access points.

## Features

- **Network Slicing**: Create isolated network slices with guaranteed bandwidth and latency
- **WiFi Slices**: Real WiFi networks via hostapd with per-slice SSID and password
- **Container Slices**: Docker-based network isolation with QoS on docker0 bridge
- **QoS Management**: Traffic shaping using Linux tc (HTB + netem)
- **Internet Gateway**: NAT routing through eth0 with automatic firewall rules
- **Real-time Monitoring**: Live QoS verification and slice metrics
- **RESTful API**: Full API for slice management and monitoring

## Architecture

### Network Types

1. **WiFi Slices** (SoftAP)
   - Bridge: `br-netslice` (172.21.x.x/24)
   - AP Mode: hostapd on wlan0
   - DHCP: dnsmasq
   - QoS: Applied to br-netslice interface

2. **Container Slices** (Docker)
   - Bridge: `docker0` (172.17.0.0/16)
   - Isolation: Network namespaces
   - QoS: Applied to docker0 interface

### Traffic Flow

```
Device/Container → Bridge (QoS applied) → NAT (MASQUERADE) → eth0 → Internet
```

## Requirements

- Python 3.8+
- Django 4.2+
- Docker
- hostapd
- dnsmasq
- iproute2 (tc command)
- iptables
- WiFi interface supporting AP mode

## Installation

1. Clone the repository:
```bash
git clone <repository-url>
cd network_slicer
```

2. Create virtual environment:
```bash
python3 -m venv venv
source venv/bin/activate
```

3. Install dependencies:
```bash
pip install -r requirements.txt
```

4. Configure settings:
```bash
cp network_slicer/settings.py.example network_slicer/settings.py
# Edit settings as needed
```

5. Run migrations:
```bash
python manage.py migrate
```

6. Create superuser:
```bash
python manage.py createsuperuser
```

## Configuration

Key settings in `network_slicer/settings.py`:

```python
# Docker networking mode
USE_MACVLAN_NETWORKS = False  # Use default docker0 bridge
USE_DEFAULT_BRIDGE = True

# Network interfaces
VLAN_PARENT_INTERFACE = 'wlan0'  # WiFi interface for AP mode
UPSTREAM_INTERFACE = 'eth0'      # Internet gateway interface

# WiFi settings
WIFI_COUNTRY_CODE = 'US'
WIFI_CHANNEL = 6
```

## Usage

1. Start the server:
```bash
sudo -E env PATH="$PATH" python manage.py runserver 0.0.0.0:8000
```

2. Access the dashboard:
```
http://localhost:8000
```

3. Create a network slice:
   - Choose slice type (URLLC, eMBB, mMTC)
   - Set bandwidth (Mbps) and latency (ms)
   - Set duration
   - Click "Create Slice"

4. Connect to WiFi slice:
   - SSID and password shown on dashboard
   - Connect your device
   - Internet access with QoS applied

## Testing

Run the verification script:
```bash
sudo ./verify_setup.sh
```

Follow the testing guide:
```bash
cat TESTING_GUIDE.sh
```

## API Endpoints

- `GET /api/slices/` - List all slices
- `POST /api/slices/` - Create new slice
- `GET /api/slices/{id}/` - Get slice details
- `PATCH /api/slices/{id}/` - Update slice
- `DELETE /api/slices/{id}/` - Delete slice
- `GET /api/slices/{id}/connected_devices/` - List connected devices
- `POST /api/admin/qos-control/{id}/` - Update QoS parameters

## QoS Implementation

### WiFi Slices
```bash
# HTB (Hierarchical Token Bucket) for bandwidth
tc qdisc add dev br-netslice root handle 1: htb default 1
tc class add dev br-netslice parent 1: classid 1:1 htb rate 2mbit

# netem for latency
tc qdisc add dev br-netslice parent 1:1 handle 10: netem delay 10ms
```

### Container Slices
```bash
# Same approach on docker0
tc qdisc add dev docker0 root handle 1: htb default 1
tc class add dev docker0 parent 1: classid 1:1 htb rate 2mbit
tc qdisc add dev docker0 parent 1:1 handle 10: netem delay 10ms
```

## Troubleshooting

### WiFi slice has no internet
1. Check hostapd is running: `ps aux | grep hostapd`
2. Check bridge is UP: `ip addr show br-netslice`
3. Verify NAT rules: `sudo iptables -t nat -L POSTROUTING -n -v`
4. Check UPSTREAM_INTERFACE setting points to eth0

### QoS not applied
1. Verify tc rules: `sudo tc qdisc show dev br-netslice`
2. Check bandwidth limit: `sudo tc class show dev br-netslice`
3. Restart Django server after settings changes

## License

MIT

## Contributing

Pull requests welcome!
