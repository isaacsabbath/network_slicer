# slicer/softap_manager.py
import subprocess
import threading
import time
import os
import random
import re
from django.conf import settings

class SoftAPManager:
    def __init__(self):
        self.hostapd_process = None
        self.dnsmasq_process = None
        self.wifi_interface = self._detect_wifi_interface()
        self.ap_subnet_cidr = None
        self.upstream_interface = None
        self.current_slice_id = None
        self.current_ssid = None
        
    def _detect_wifi_interface(self):
        """Detect available WiFi interface"""
        try:
            result = subprocess.run(['iwconfig'], capture_output=True, text=True)
            lines = result.stdout.split('\n')
            for line in lines:
                if 'IEEE 802.11' in line and 'no wireless' not in line:
                    interface = line.split()[0]
                    print(f"✅ Found WiFi interface: {interface}")
                    return interface
        except Exception as e:
            print(f"❌ WiFi interface detection failed: {e}")
        
        # Common interface names
        for interface in ['wlan0', '']:
            try:
                result = subprocess.run(['ip', 'link', 'show', interface], capture_output=True)
                if result.returncode == 0:
                    print(f"✅ Found WiFi interface: {interface}")
                    return interface
            except:
                continue
        
        print("❌ No WiFi interface found")
        return None
    
    def _generate_ssid_name(self, slice_instance):
        """Generate SSID name based on slice type"""
        type_map = {
            'URLLC': 'Gaming',
            'EMBB': 'Streaming', 
            'MMTC': 'IoT'
        }
        type_name = type_map.get(slice_instance.slice_type, 'Slice')
        uuid_str = str(slice_instance.id)
        return f"NetSlice_{type_name}_{uuid_str[:8]}"
    
    def _generate_wifi_password(self):
        """Generate a random WiFi password"""
        chars = "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789"
        return ''.join(random.choice(chars) for _ in range(12))

    def _detect_upstream_iface(self):
        """Detect default route outgoing interface (used for NAT)."""
        # Honor explicit setting
        explicit = getattr(settings, 'UPSTREAM_INTERFACE', None)
        if explicit:
            self.upstream_interface = explicit
            return explicit

        if self.upstream_interface:
            return self.upstream_interface
        try:
            # Try ip route get for 1.1.1.1
            r = subprocess.run(['ip', '-o', 'route', 'get', '1.1.1.1'], capture_output=True, text=True)
            if r.returncode == 0 and ' dev ' in r.stdout:
                # Parse ' dev <iface> '
                parts = r.stdout.strip().split()
                if 'dev' in parts:
                    idx = parts.index('dev')
                    iface = parts[idx+1]
                    self.upstream_interface = iface
                    return iface
        except Exception:
            pass
        # Fallbacks
        for candidate in ['eth0', 'enp0s25', 'enp2s0', 'wlan0']:
            try:
                if subprocess.run(['ip', 'link', 'show', candidate], capture_output=True).returncode == 0:
                    self.upstream_interface = candidate
                    return candidate
            except Exception:
                continue
        return 'eth0'
    
    def check_softap_support(self):
        """Check if system supports SoftAP"""
        if not self.wifi_interface:
            return {'supported': False, 'reason': 'No WiFi interface found'}
        
        try:
            # Check if hostapd is installed
            result = subprocess.run(['which', 'hostapd'], capture_output=True)
            if result.returncode != 0:
                return {'supported': False, 'reason': 'hostapd not installed'}
            
            # Check if interface supports AP mode
            result = subprocess.run(['iw', 'list'], capture_output=True, text=True)
            if 'AP' in result.stdout:
                return {'supported': True, 'interface': self.wifi_interface}
            else:
                return {'supported': False, 'reason': 'WiFi card does not support AP mode'}
        except Exception as e:
            return {'supported': False, 'reason': f'Cannot check WiFi capabilities: {e}'}
    
    def create_virtual_network(self, slice_instance):
        """Create a real virtual WiFi network"""
        if not self.wifi_interface:
            print("❌ No WiFi interface available")
            return False

        # If another slice is active, cleanup previous AP before starting new
        if self.current_slice_id and self.current_slice_id != slice_instance.id:
            try:
                print(f"🔁 Handoff: stopping previous slice AP (slice {self.current_slice_id})")
                self._cleanup_previous_ap()
            except Exception as e:
                print(f"⚠️  Previous AP cleanup warning: {e}")
        
        ssid = self._generate_ssid_name(slice_instance)
        password = self._generate_wifi_password()
        
        print(f"📡 Creating SoftAP: {ssid} on {self.wifi_interface}")
        
        # Save slice details first
        slice_instance.ssid_name = ssid
        slice_instance.wifi_password = password
        slice_instance.save()
        
        try:
            # Prepare interface: detach from NetworkManager/STA and flush addressing
            self._prepare_interface_for_ap()
            # Simple method - just start hostapd with the config
            if self._simple_hostapd_start(ssid, password):
                # Configure IP, DHCP, and NAT for internet access
                try:
                    self._configure_ap_network(slice_instance)
                except Exception as e:
                    print(f"⚠️  AP networking setup warning: {e}")

                # Track current slice/AP
                self.current_slice_id = slice_instance.id
                self.current_ssid = ssid
                print(f"✅ SoftAP created: {ssid}")
                print(f"🔑 Password: {password}")
                return True
            
            print("❌ SoftAP creation failed")
            return False
            
        except Exception as e:
            print(f"❌ SoftAP creation failed: {e}")
            return False
    
    def _simple_hostapd_start(self, ssid, password):
        """Simple hostapd start without complex network setup"""
        try:
            # Kill any existing hostapd processes
            subprocess.run(['sudo', 'pkill', 'hostapd'], capture_output=True)
            time.sleep(1)
            
            # Create hostapd config with static bridge
            country = getattr(settings, 'WIFI_COUNTRY_CODE', 'US')
            channel = str(getattr(settings, 'WIFI_CHANNEL', 6))
            hostapd_conf = f"""interface={self.wifi_interface}
driver=nl80211
bridge=br-netslice
country_code={country}
ieee80211d=1
ieee80211n=1
ssid={ssid}
hw_mode=g
channel={channel}
wmm_enabled=1
auth_algs=1
ignore_broadcast_ssid=0
wpa=2
wpa_passphrase={password}
wpa_key_mgmt=WPA-PSK
rsn_pairwise=CCMP
logger_stdout=-1
"""
            
            config_file = f'/tmp/hostapd_{ssid}.conf'
            with open(config_file, 'w') as f:
                f.write(hostapd_conf)
            
            print(f"🔄 Starting hostapd with config: {config_file}")
            
            # Bring interface up in AP mode lifecycle
            subprocess.run(['sudo', 'ip', 'link', 'set', self.wifi_interface, 'up'], capture_output=True)

            # Start hostapd in background
            self.hostapd_process = subprocess.Popen(
                ['sudo', 'hostapd', config_file],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True
            )
            
            # Wait a bit and check if it's running
            if self._wait_for_ap_ready(timeout=10):
                print("✅ hostapd is running and AP is ready")
                return True
            else:
                # Check for errors
                stdout, stderr = self.hostapd_process.communicate()
                print(f"❌ hostapd failed: {stderr}")
                return False
            
        except Exception as e:
            print(f"❌ Simple hostapd start failed: {e}")
            return False

    def _configure_ap_network(self, slice_instance):
        """Add wlan0 to br-netslice bridge and configure dnsmasq for the bridge."""
        try:
            # Compute /24 subnet for AP using slice id hash
            vlan_part = 100 + (hash(str(slice_instance.id)) % 155)
            base = f"172.21.{vlan_part}"
            gw_ip = f"{base}.1"
            cidr = f"{gw_ip}/24"
            self.ap_subnet_cidr = f"{base}.0/24"

            # Flush any IP from wlan0 (bridge members don't have IPs)
            subprocess.run(['sudo', 'ip', 'addr', 'flush', 'dev', self.wifi_interface], check=False)
            
            # Add wlan0 to the static bridge br-netslice
            subprocess.run(['sudo', 'brctl', 'addif', 'br-netslice', self.wifi_interface], check=False)
            subprocess.run(['sudo', 'ip', 'link', 'set', self.wifi_interface, 'up'], check=True)
            
            # Assign IP to the bridge (not wlan0)
            subprocess.run(['sudo', 'ip', 'addr', 'flush', 'dev', 'br-netslice'], check=False)
            subprocess.run(['sudo', 'ip', 'addr', 'add', cidr, 'dev', 'br-netslice'], check=True)
            subprocess.run(['sudo', 'ip', 'link', 'set', 'br-netslice', 'up'], check=True)
            
            # Wait for bridge to be fully ready
            for attempt in range(10):  # Up to 10 seconds
                time.sleep(1)
                try:
                    # Check if bridge has the expected IP
                    result = subprocess.run(['ip', 'addr', 'show', 'br-netslice'], 
                                          capture_output=True, text=True, check=True)
                    if f"inet {gw_ip}/24" in result.stdout:
                        print(f"✅ Bridge br-netslice ready with IP {gw_ip}")
                        break
                except Exception:
                    pass
                if attempt == 9:
                    raise Exception(f"Interface {self.wifi_interface} failed to get IP {gw_ip}")
            else:
                print(f"⚠️  Interface IP verification incomplete, proceeding anyway")

            # Start dnsmasq for DHCP on bridge interface
            leasefile = f"/tmp/dnsmasq_br-netslice.leases"
            dnsmasq_conf = f"""
interface=br-netslice
bind-interfaces
dhcp-authoritative
domain-needed
bogus-priv
no-resolv
dhcp-range={base}.10,{base}.200,255.255.255.0,12h
dhcp-option=3,{gw_ip}
dhcp-option=6,1.1.1.1,8.8.8.8
dhcp-leasefile={leasefile}
log-queries
log-dhcp
"""
            conf_path = f"/tmp/dnsmasq_br-netslice.conf"
            with open(conf_path, 'w') as f:
                f.write(dnsmasq_conf)
            # Stop any running dnsmasq processes (system-wide cleanup)
            subprocess.run(['sudo', 'pkill', '-f', 'dnsmasq'], capture_output=True)
            try:
                if self.dnsmasq_process:
                    self.dnsmasq_process.terminate()
                    self.dnsmasq_process.wait(timeout=3)
            except Exception:
                pass
            time.sleep(1)
            
            # Ensure lease file exists and is readable by web process
            try:
                subprocess.run(['sudo', 'touch', leasefile], capture_output=True)
                subprocess.run(['sudo', 'chmod', '666', leasefile], capture_output=True)
            except Exception:
                pass

            # Start dnsmasq
            self.dnsmasq_process = subprocess.Popen(
                ['sudo', 'dnsmasq', '--conf-file=' + conf_path, '--keep-in-foreground', '--log-dhcp'],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True
            )
            
            # Wait for dnsmasq to be ready and verify it can see the range
            time.sleep(3)
            if not self._check_process_running(self.dnsmasq_process):
                stdout, stderr = self.dnsmasq_process.communicate()
                raise Exception(f"dnsmasq failed to start: {stderr}")
            
            # Test that dnsmasq can see the address range by checking its status
            try:
                test_result = subprocess.run(['sudo', 'kill', '-USR1', str(self.dnsmasq_process.pid)], 
                                           capture_output=True, timeout=2)
            except Exception:
                pass

            # Apply QoS to the bridge interface
            try:
                self._apply_qos_to_bridge(slice_instance)
            except Exception as e:
                print(f"⚠️  QoS application warning: {e}")

            # Enable IP forwarding
            result = subprocess.run(['sudo', 'sysctl', '-w', 'net.ipv4.ip_forward=1'], capture_output=True, text=True)
            if result.returncode != 0:
                print(f"⚠️  IP forwarding enable failed: {result.stderr}")

            # NAT to detected upstream interface
            out_if = self._detect_upstream_iface()
            print(f"🔄 Setting up NAT via {out_if}")
            # POSTROUTING MASQUERADE (check first, add if missing)
            check_result = subprocess.run(['sudo', 'iptables', '-t', 'nat', '-C', 'POSTROUTING', '-s', self.ap_subnet_cidr, '-o', out_if, '-j', 'MASQUERADE'], capture_output=True)
            if check_result.returncode != 0:
                add_result = subprocess.run(['sudo', 'iptables', '-t', 'nat', '-A', 'POSTROUTING', '-s', self.ap_subnet_cidr, '-o', out_if, '-j', 'MASQUERADE'], capture_output=True)
                if add_result.returncode != 0:
                    print(f"⚠️  NAT rule add failed: {add_result.stderr.decode()}")
                else:
                    print(f"✅ Added NAT rule for {self.ap_subnet_cidr} -> {out_if}")
            
            # Forward rules
            fwd_rules = [
                ['FORWARD', '-i', out_if, '-o', self.wifi_interface, '-m', 'state', '--state', 'RELATED,ESTABLISHED', '-j', 'ACCEPT'],
                ['FORWARD', '-i', self.wifi_interface, '-o', out_if, '-j', 'ACCEPT']
            ]
            for rule in fwd_rules:
                check_result = subprocess.run(['sudo', 'iptables', '-C'] + rule, capture_output=True)
                if check_result.returncode != 0:
                    add_result = subprocess.run(['sudo', 'iptables', '-A'] + rule, capture_output=True)
                    if add_result.returncode != 0:
                        print(f"⚠️  Forward rule add failed: {add_result.stderr.decode()}")

            print(f"✅ AP network configured: {cidr} via {out_if}")
            print(f"🔄 DHCP range: {base}.10-{base}.200, gateway: {gw_ip}")
            
        except Exception as e:
            print(f"❌ AP network config failed: {e}")
            raise e
    
    def _check_process_running(self, process):
        """Check if a process is still running"""
        return process.poll() is None

    def _wait_for_ap_ready(self, timeout=10):
        """Wait until the interface reports AP mode and hostapd is alive."""
        start = time.time()
        while time.time() - start < timeout:
            # If hostapd died, stop waiting
            if not self._check_process_running(self.hostapd_process):
                return False
            try:
                inf = subprocess.run(['iw', 'dev', self.wifi_interface, 'info'], capture_output=True, text=True)
                if inf.returncode == 0 and ('type AP' in inf.stdout or 'AP' in inf.stdout):
                    return True
            except Exception:
                pass
            time.sleep(1)
        return False
    
    def _check_ap_running(self, ssid):
        """Check if access point is running"""
        try:
            result = subprocess.run(['iwconfig', self.wifi_interface], capture_output=True, text=True)
            return 'Mode:Master' in result.stdout
        except:
            return False
    
    def _apply_qos_to_bridge(self, slice_instance):
        """Apply QoS (bandwidth and latency) to the br-netslice bridge interface"""
        bridge_iface = 'br-netslice'
        bandwidth_mbps = slice_instance.bandwidth_mbps
        latency_ms = slice_instance.latency_ms or 0
        
        print(f"🔧 Applying QoS to {bridge_iface}: {bandwidth_mbps}Mbps bandwidth, {latency_ms}ms latency")
        
        try:
            # Clear any existing qdisc on the bridge
            subprocess.run(['sudo', 'tc', 'qdisc', 'del', 'dev', bridge_iface, 'root'], 
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            
            # Calculate burst size (recommended: rate in bytes / Hz, e.g., for 2Mbps -> ~3KB)
            burst_bytes = max(1600, int((bandwidth_mbps * 1000000) / (8 * 250)))  # 4ms worth of data
            
            if latency_ms and latency_ms > 0:
                # Use HTB + netem for both bandwidth and latency
                # 1. Create HTB root qdisc
                subprocess.run([
                    'sudo', 'tc', 'qdisc', 'add', 'dev', bridge_iface, 'root', 
                    'handle', '1:', 'htb', 'default', '1'
                ], check=True)
                
                # 2. Create HTB class with bandwidth limit
                subprocess.run([
                    'sudo', 'tc', 'class', 'add', 'dev', bridge_iface, 
                    'parent', '1:', 'classid', '1:1', 'htb',
                    'rate', f'{bandwidth_mbps}mbit',
                    'burst', str(burst_bytes)
                ], check=True)
                
                # 3. Add netem qdisc for latency under the HTB class
                subprocess.run([
                    'sudo', 'tc', 'qdisc', 'add', 'dev', bridge_iface,
                    'parent', '1:1', 'handle', '10:', 'netem',
                    'delay', f'{latency_ms}ms'
                ], check=True)
                
                print(f"✅ Applied HTB + netem QoS to {bridge_iface}")
            else:
                # Use TBF for bandwidth only
                subprocess.run([
                    'sudo', 'tc', 'qdisc', 'add', 'dev', bridge_iface, 'root',
                    'tbf', 'rate', f'{bandwidth_mbps}mbit',
                    'burst', str(burst_bytes),
                    'latency', '50ms'
                ], check=True)
                
                print(f"✅ Applied TBF QoS to {bridge_iface}")
                
        except subprocess.CalledProcessError as e:
            raise Exception(f"Failed to apply QoS: {e}")
    
    def stop_virtual_network(self, slice_instance):
        """Stop the virtual network"""
        try:
            print(f"🛑 Stopping SoftAP: {slice_instance.ssid_name}")
            
            # Kill hostapd process
            if self.hostapd_process:
                self.hostapd_process.terminate()
                self.hostapd_process.wait(timeout=5)
            
            # Kill any remaining hostapd processes
            subprocess.run(['sudo', 'pkill', 'hostapd'], capture_output=True)

            # Stop dnsmasq
            try:
                if self.dnsmasq_process:
                    self.dnsmasq_process.terminate()
                    self.dnsmasq_process.wait(timeout=5)
            except Exception:
                pass
            subprocess.run(['sudo', 'pkill', 'dnsmasq'], capture_output=True)

            # Remove QoS from bridge
            try:
                subprocess.run(['sudo', 'tc', 'qdisc', 'del', 'dev', 'br-netslice', 'root'], 
                             stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            except Exception:
                pass

            # Flush IP from AP interface
            subprocess.run(['sudo', 'ip', 'addr', 'flush', 'dev', self.wifi_interface], capture_output=True)

            # Restore NetworkManager control (best-effort)
            try:
                subprocess.run(['sudo', 'nmcli', 'dev', 'set', self.wifi_interface, 'managed', 'yes'], capture_output=True)
            except Exception:
                pass
            
            # Restart NetworkManager to restore normal WiFi
            subprocess.run(['sudo', 'systemctl', 'start', 'NetworkManager'], capture_output=True)
            
            print(f"✅ SoftAP stopped: {slice_instance.ssid_name}")
            if self.current_slice_id == slice_instance.id:
                self.current_slice_id = None
                self.current_ssid = None
            return True
            
        except Exception as e:
            print(f"❌ SoftAP stop failed: {e}")
            return False

    def get_connected_devices(self, slice_instance):
        """Get list of devices currently connected to this slice's AP network"""
        devices = []
        if not self.wifi_interface or not slice_instance.ssid_name:
            return devices

        # Expected subnet base for this slice (match _configure_ap_network logic)
        try:
            vlan_part = 100 + (hash(str(slice_instance.id)) % 155)
            expected_base = f"10.50.{vlan_part}."
        except Exception:
            expected_base = None

        try:
            # Parse dnsmasq DHCP leases
            lease_files = [
                f'/tmp/dnsmasq_{self.wifi_interface}.leases',
                '/var/lib/misc/dnsmasq.leases'
            ]
            for lease_file in lease_files:
                try:
                    with open(lease_file, 'r') as f:
                        for line in f:
                            line = line.strip()
                            if not line:
                                continue
                            # dnsmasq lease format: <expiry> <mac> <ip> <hostname> <client-id>
                            import re, time as _t
                            m = re.match(r'(\d+)\s+(\S+)\s+(\S+)\s*(\S*)\s*(.*)', line)
                            if not m:
                                continue
                            expiry, mac, ip, hostname, _client = m.groups()
                            try:
                                expiry_ts = int(expiry)
                            except Exception:
                                continue
                            if expiry_ts > _t.time() - 300 and ip:  # seen in last 5 minutes
                                if expected_base and not ip.startswith(expected_base):
                                    continue
                                if not any(d.get('ip') == ip for d in devices):
                                    devices.append({
                                        'ip': ip,
                                        'mac': mac,
                                        'hostname': hostname or 'Unknown',
                                        'last_seen': expiry_ts
                                    })
                except (FileNotFoundError, PermissionError):
                    continue

            # Neighbor table fallback (interface-scoped)
            try:
                neigh = subprocess.run(
                    ['ip', 'neigh', 'show', 'dev', self.wifi_interface],
                    capture_output=True, text=True
                )
                if neigh.returncode == 0:
                    import re, time as _t
                    for line in neigh.stdout.splitlines():
                        nm = re.search(r'(\d+\.\d+\.\d+\.\d+)\s+dev\s+\S+\s+lladdr\s+([0-9a-f:]{17})', line, re.I)
                        if nm:
                            ip, mac = nm.groups()
                            if expected_base and not ip.startswith(expected_base):
                                continue
                            if not any(d.get('ip') == ip for d in devices):
                                devices.append({
                                    'ip': ip,
                                    'mac': mac.lower(),
                                    'hostname': 'Unknown',
                                    'last_seen': int(_t.time())
                                })
            except Exception:
                pass

            # ARP fallback (broad, interface filtered)
            try:
                arp = subprocess.run(['arp', '-an'], capture_output=True, text=True)
                if arp.returncode == 0:
                    import re, time as _t
                    for line in arp.stdout.splitlines():
                        if f" on {self.wifi_interface}" not in line:
                            continue
                        am = re.search(r'\((\d+\.\d+\.\d+\.\d+)\)\s+at\s+([0-9a-f:]{17})', line, re.I)
                        if am:
                            ip, mac = am.groups()
                            if expected_base and not ip.startswith(expected_base):
                                continue
                            if not any(d.get('ip') == ip for d in devices):
                                devices.append({
                                    'ip': ip,
                                    'mac': mac.lower(),
                                    'hostname': 'Unknown',
                                    'last_seen': int(_t.time())
                                })
            except Exception:
                pass

        except Exception as e:
            print(f"⚠️  Error getting connected devices: {e}")

        return devices[:10]

    # Utility used for handoff when starting a new slice AP
    def _cleanup_previous_ap(self):
        try:
            subprocess.run(['sudo', 'pkill', 'hostapd'], capture_output=True)
            subprocess.run(['sudo', 'pkill', 'dnsmasq'], capture_output=True)
            if self.wifi_interface:
                subprocess.run(['sudo', 'ip', 'addr', 'flush', 'dev', self.wifi_interface], capture_output=True)
            # Best-effort flush neighbor entries for previous subnet to reduce stale devices (optional)
            try:
                neigh = subprocess.run(['ip', 'neigh', 'show', 'dev', self.wifi_interface], capture_output=True, text=True)
                for line in neigh.stdout.splitlines():
                    import re
                    nm = re.search(r'(\d+\.\d+\.\d+\.\d+)', line)
                    if nm:
                        ip = nm.group(1)
                        subprocess.run(['sudo', 'ip', 'neigh', 'del', ip, 'dev', self.wifi_interface], capture_output=True)
            except Exception:
                pass
        except Exception as e:
            print(f"⚠️  _cleanup_previous_ap error: {e}")

    # Backward compatibility with network_actions.remove_virtual_ssid call
    def remove_virtual_network(self, ssid_name):
        if self.current_ssid == ssid_name:
            # Construct a dummy slice-like object for stop_virtual_network
            class _Slice:
                def __init__(self, ssid):
                    self.ssid_name = ssid
                    self.id = None
            dummy = _Slice(ssid_name)
            self.stop_virtual_network(dummy)

    def diagnose_ap(self):
        """Return diagnostic info about AP state."""
        info = {
            'wifi_interface': self.wifi_interface,
            'ap_subnet': self.ap_subnet_cidr,
            'hostapd_running': False,
            'dnsmasq_running': False,
            'interface_ip': None,
            'nat_rule_present': False,
            'recent_leases': []
        }
        try:
            # Interface IP
            if self.wifi_interface:
                ip_show = subprocess.run(['ip', '-4', 'addr', 'show', self.wifi_interface], capture_output=True, text=True)
                m = re.search(r'inet (\d+\.\d+\.\d+\.\d+)', ip_show.stdout)
                if m:
                    info['interface_ip'] = m.group(1)
            # hostapd process
            ps = subprocess.run(['pgrep', '-f', f'hostapd .*{self.wifi_interface}'], capture_output=True, text=True)
            info['hostapd_running'] = ps.returncode == 0
            # dnsmasq process
            dps = subprocess.run(['pgrep', '-f', f'dnsmasq .*{self.wifi_interface}'], capture_output=True, text=True)
            info['dnsmasq_running'] = dps.returncode == 0
            # NAT rule present
            if self.ap_subnet_cidr:
                nat = subprocess.run(['sudo', 'iptables', '-t', 'nat', '-S', 'POSTROUTING'], capture_output=True, text=True)
                info['nat_rule_present'] = self.ap_subnet_cidr in nat.stdout
            # Leases
            lease_path = f'/tmp/dnsmasq_{self.wifi_interface}.leases'
            if os.path.exists(lease_path):
                try:
                    with open(lease_path) as lf:
                        for line in lf.readlines()[-10:]:
                            info['recent_leases'].append(line.strip())
                except Exception:
                    pass
        except Exception as e:
            info['error'] = str(e)
        return info

    def _prepare_interface_for_ap(self):
        """Detach Wi‑Fi interface from NetworkManager/STA and prepare for AP."""
        iface = self.wifi_interface
        if not iface:
            return
        # Best-effort: stop NM managing this iface and disconnect
        try:
            subprocess.run(['sudo', 'nmcli', 'dev', 'set', iface, 'managed', 'no'], capture_output=True)
        except Exception:
            pass
        try:
            subprocess.run(['sudo', 'nmcli', 'dev', 'disconnect', iface], capture_output=True)
        except Exception:
            pass
        # Flush any existing addresses from STA mode
        subprocess.run(['sudo', 'ip', 'addr', 'flush', 'dev', iface], capture_output=True)
        # Bring link down then up to ensure clean state
        subprocess.run(['sudo', 'ip', 'link', 'set', iface, 'down'], capture_output=True)
        subprocess.run(['sudo', 'ip', 'link', 'set', iface, 'up'], capture_output=True)