# slicer/network_actions.py
import requests
import subprocess
import time
import threading
import re
import psutil
import random
import qrcode
import io
import base64
from django.conf import settings
from bs4 import BeautifulSoup

# ADD THIS IMPORT
from .softap_manager import SoftAPManager
from .docker_manager import DockerVLANManager

class HomeNetworkManager:
    """A class to interact with your home router for QoS configuration"""
    
    def __init__(self):
        # Configure these in your settings.py for your specific router
        self.router_ip = getattr(settings, 'ROUTER_IP', '192.168.100.1')
        self.username = getattr(settings, 'ROUTER_USERNAME', 'telecomadmin')
        self.password = getattr(settings, 'ROUTER_PASSWORD', 'admintelecom')
        self.test_device_ip = getattr(settings, 'TEST_DEVICE_IP', '192.168.100.36')
        self.enable_router = getattr(settings, 'ENABLE_ROUTER_INTEGRATION', True)
        self.session = None
        
    def _huawei_login(self):
        """Authenticate with Huawei router using HTML form"""
        try:
            if self.session:
                return self.session
            
            # Quick connectivity check first
            if not self._check_router_connectivity():
                print(f"⚠️  Router at {self.router_ip} is not reachable, using simulation mode")
                return None
                
            self.session = requests.Session()
            
            # First, get the login page to extract CSRF token or form data
            login_page_url = f"http://{self.router_ip}/"
            response = self.session.get(login_page_url, timeout=5)
            
            # Huawei often uses basic authentication or form-based login
            # Try different login approaches
            
            # Approach 1: Try basic authentication
            auth_url = f"http://{self.router_ip}/"
            response = self.session.get(auth_url, auth=(self.username, self.password), timeout=10)
            
            if response.status_code == 200 and 'login' not in response.text.lower():
                print("✅ Successfully logged into Huawei router (Basic Auth)")
                return self.session
            
            # Approach 2: Try form-based login
            login_url = f"http://{self.router_ip}/api/system/user_login"
            login_data = {
                "UserName": self.username,
                "Password": self.password
            }
            
            headers = {
                'Content-Type': 'application/json',
                'Referer': f'http://{self.router_ip}/'
            }
            
            response = self.session.post(
                login_url, 
                json=login_data, 
                headers=headers,
                timeout=10
            )
            
            if response.status_code == 200:
                print("✅ Successfully logged into Huawei router (Form Auth)")
                return self.session
            else:
                print("⚠️  Using session without explicit login")
                return self.session
                
        except Exception as e:
            print(f"❌ Huawei login error: {e}")
            return None
    
    def _check_router_connectivity(self):
        """Quick check if router is reachable"""
        try:
            import socket
            sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            sock.settimeout(2)  # Quick timeout
            result = sock.connect_ex((self.router_ip, 80))
            sock.close()
            return result == 0
        except Exception:
            return False

    # ==================== VIRTUAL NETWORK METHODS ====================
    
    def _check_router_connectivity(self):
        """Quick check if router is reachable"""
        try:
            import socket
            sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            sock.settimeout(2)  # Quick timeout
            result = sock.connect_ex((self.router_ip, 80))
            sock.close()
            return result == 0
        except Exception:
            return False

    # ==================== VIRTUAL NETWORK METHODS ====================
    
    def create_virtual_ssid(self, slice_instance):
        """Create virtual network using SoftAP first, fallback to router/simulation"""
        print("🔄 Attempting virtual network creation...")
        
        # Try SoftAP first (most reliable)
        softap_mgr = SoftAPManager()
        capabilities = softap_mgr.check_softap_support()
        
        print(f" SoftAP capabilities: {capabilities}")
        
        if capabilities.get('supported', False):
            print("✅ System supports SoftAP, creating real virtual network...")
            if softap_mgr.create_virtual_network(slice_instance):
                # Also create a docker network to represent the VLAN slice
                try:
                    docker_mgr = DockerVLANManager()
                    vlan_id = docker_mgr.create_vlan_network(slice_instance)
                    if vlan_id:
                        slice_instance.vlan_id = vlan_id
                        slice_instance.save()
                        print(f"✅ Docker VLAN network created: {vlan_id}")
                except Exception as e:
                    print(f"⚠️  Docker VLAN creation failed: {e}")
                # EARLY activation if WiFi + VLAN succeeded and slice still provisioning/requested
                try:
                    if slice_instance.status in ['REQUESTED','PROVISIONING'] and slice_instance.ssid_name:
                        slice_instance.status = 'ACTIVE'
                        from django.utils import timezone
                        slice_instance.activated_at = timezone.now()
                        if not slice_instance.expires_at:
                            slice_instance.expires_at = timezone.now() + timezone.timedelta(hours=slice_instance.duration_hours)
                        slice_instance.save()
                        print(f"🚀 Early activation: slice {slice_instance.id} ACTIVE after network bring-up")
                except Exception as e:
                    print(f"⚠️  Early activation failed: {e}")
                return True
            else:
                print("❌ SoftAP creation failed, trying router...")
        else:
            print(f"❌ SoftAP not supported: {capabilities.get('reason', 'Unknown')}")
        
        # Fallback to Huawei router (if enabled)
        if not self.enable_router:
            print("⚠️  Router integration disabled, using Docker VLAN only")
            return self._create_simulated_ssid(slice_instance)
            
        try:
            session = self._huawei_login()
            if not session:
                print(f"⚠️  Router not available, creating simulated network with Docker VLAN")
                return self._create_simulated_ssid(slice_instance)
            
            ssid_name = self._generate_ssid_name(slice_instance)
            vlan_id = self._generate_vlan_id(slice_instance)
            password = self._generate_wifi_password()
            
            print(f" Trying Huawei router: {ssid_name} (VLAN {vlan_id})")
            
            # Try Huawei specific SSID creation
            if self._create_huawei_ssid(session, ssid_name, vlan_id, password, slice_instance):
                slice_instance.ssid_name = ssid_name
                slice_instance.vlan_id = vlan_id
                slice_instance.wifi_password = password
                slice_instance.save()
                
                # Create corresponding Docker network for this slice
                try:
                    docker_mgr = DockerVLANManager()
                    docker_vlan = docker_mgr.create_vlan_network(slice_instance)
                    if docker_vlan:
                        slice_instance.vlan_id = docker_vlan
                        slice_instance.save()
                        print(f"✅ Docker VLAN created: {docker_vlan}")
                except Exception as e:
                    print(f"⚠️  Docker VLAN creation failed: {e}")

                print(f"✅ Huawei virtual network created: {ssid_name}")
                print(f"🔑 Password: {password}")
                return True
            else:
                # Final fallback to simulation
                return self._create_simulated_ssid(slice_instance)
                
        except Exception as e:
            print(f"❌ Router SSID creation failed: {e}")
            return self._create_simulated_ssid(slice_instance)

    def _create_simulated_ssid(self, slice_instance):
        """Create simulated SSID when real creation fails"""
        ssid_name = self._generate_ssid_name(slice_instance)
        password = self._generate_wifi_password()
        
        slice_instance.ssid_name = ssid_name
        # Also attempt to create Docker VLAN to represent this slice
        docker_mgr = DockerVLANManager()
        docker_vlan = docker_mgr.create_vlan_network(slice_instance)
        if docker_vlan:
            slice_instance.vlan_id = docker_vlan
        else:
            slice_instance.vlan_id = random.randint(100, 200)
        slice_instance.wifi_password = password
        slice_instance.save()
        
        print(f"📡 SIMULATED WiFi network: {ssid_name}")
        print(f"🔑 Password: {password}")
        print(f"🐳 Docker VLAN network created with ID: {slice_instance.vlan_id}")
        print("💡 Note: WiFi is simulated, but Docker network isolation is real")
        return True

    def apply_slice_configuration(self, slice_instance):
        """Apply QoS and other slice configuration. Returns True on success."""
        try:
            # Attempt to configure QoS for the test device or the configured test IP
            device_ip = getattr(settings, 'TEST_DEVICE_IP', None) or self.test_device_ip
            if device_ip:
                success = self.configure_qos_for_device(device_ip, slice_instance.slice_type, slice_instance.bandwidth_mbps)
                return success
            return True
        except Exception as e:
            print(f"❌ apply_slice_configuration error: {e}")
            return False

    def start_network_monitoring(self, slice_instance, interval=30):
        """Start a background thread that periodically prints metrics for the slice."""
        def monitor():
            print(f"🔎 Starting monitoring for slice {slice_instance.id}")
            try:
                while True:
                    metrics = self.get_network_metrics()
                    print(f"📈 Metrics for slice {slice_instance.id}: {metrics}")
                    time.sleep(interval)
            except Exception as e:
                print(f"❌ Monitoring stopped for slice {slice_instance.id}: {e}")

        thread = threading.Thread(target=monitor, daemon=True)
        thread.start()

    def remove_virtual_ssid(self, slice_instance):
        """Remove virtual network resources for a slice (softap, router, docker)."""
        try:
            # Remove SoftAP if configured
            softap_mgr = SoftAPManager()
            if slice_instance.ssid_name:
                try:
                    softap_mgr.remove_virtual_network(slice_instance.ssid_name)
                except Exception:
                    pass

            # Remove from router
            try:
                session = self._huawei_login()
                if session and slice_instance.ssid_name:
                    self._remove_huawei_ssid(session, slice_instance.ssid_name)
            except Exception:
                pass

            # Remove docker network representation
            try:
                docker_mgr = DockerVLANManager()
                docker_mgr.remove_vlan_network(slice_instance)
            except Exception as e:
                print(f"❌ Docker VLAN removal error: {e}")

            return True
        except Exception as e:
            print(f"❌ remove_virtual_ssid error: {e}")
            return False

    def _generate_ssid_name(self, slice_instance):
        """Generate SSID name based on slice type"""
        type_map = {
            'GAMING': 'Gaming',
            'CORP': 'Corporate',
            'GUEST': 'Guest',
            'IOT': 'IoT'
        }
        type_name = type_map.get(slice_instance.slice_type, 'Slice')
        
        # Convert UUID to string before slicing
        uuid_str = str(slice_instance.id)
        return f"NetSlice_{type_name}_{uuid_str[:8]}"

    def _generate_vlan_id(self, slice_instance):
        """Generate VLAN ID for the slice"""
        return 100 + (hash(str(slice_instance.id)) % 100)

    def _generate_wifi_password(self):
        """Generate a random WiFi password"""
        chars = "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789"
        return ''.join(random.choice(chars) for _ in range(12))

    def _create_huawei_ssid(self, session, ssid_name, vlan_id, password, slice_instance):
        """Create SSID on Huawei router"""
        try:
            # Try multiple possible WLAN configuration endpoints
            endpoints = [
                '/api/wlan/ssid',
                '/api/wlan/multi-ssid',
                '/api/wlan/guest-network'
            ]
            
            for endpoint in endpoints:
                wlan_url = f"http://{self.router_ip}{endpoint}"
                
                # Configure based on slice type
                config = {
                    "SSID": ssid_name,
                    "VLANID": vlan_id,
                    "Security": "WPA2",
                    "Password": password,
                    "Enable": "1"       
                }
                
                # Add slice-specific QoS settings
                if slice_instance.slice_type == 'URLLC':
                    config["QoS"] = "High"
                    config["Priority"] = "7"  # Highest priority
                elif slice_instance.slice_type == 'EMBB':
                    config["QoS"] = "Medium" 
                    config["Priority"] = "4"
                else:  # MMTC
                    config["QoS"] = "Low"
                    config["Priority"] = "1"
                
                headers = {
                    'Content-Type': 'application/json',
                    'Referer': f'http://{self.router_ip}/'
                }
                
                print(f"🔄 Trying endpoint: {endpoint}")
                response = session.post(wlan_url, json=config, headers=headers, timeout=10)
                
                if response.status_code == 200:
                    print(f"✅ Successfully created SSID via {endpoint}")
                    return True
                else:
                    print(f"❌ Endpoint {endpoint} failed: {response.status_code}")
            
            # If all endpoints failed, try the HTML form approach
            return self._create_huawei_ssid_html(session, ssid_name, password)
            
        except Exception as e:
            print(f"Huawei SSID creation error: {e}")
            return False

    def _create_huawei_ssid_html(self, session, ssid_name, password):
        """Alternative method using HTML form submission for Huawei routers"""
        try:
            # Get the WLAN configuration page
            wlan_page = f"http://{self.router_ip}/html/ssid.html"
            response = session.get(wlan_page, timeout=10)
            
            if response.status_code != 200:
                return False
            
            # Parse the HTML to find form elements
            soup = BeautifulSoup(response.text, 'html.parser')
            
            # Look for SSID configuration form
            form = soup.find('form', {'id': 'ssid_form'}) or soup.find('form', {'action': re.compile('ssid')})
            
            if not form:
                print("❌ Could not find SSID configuration form")
                return False
            
            # Extract form data and build submission
            form_data = {}
            for input_tag in form.find_all('input'):
                name = input_tag.get('name')
                value = input_tag.get('value', '')
                if name and 'ssid' in name.lower():
                    form_data[name] = ssid_name
                elif name and 'password' in name.lower() or 'key' in name.lower():
                    form_data[name] = password
                elif name and 'enable' in name.lower():
                    form_data[name] = '1'
                elif name:
                    form_data[name] = value
            
            # Submit the form
            action_url = form.get('action')
            if action_url.startswith('/'):
                submit_url = f"http://{self.router_ip}{action_url}"
            else:
                submit_url = f"http://{self.router_ip}/{action_url}"
            
            response = session.post(submit_url, data=form_data, timeout=10)
            return response.status_code == 200
            
        except Exception as e:
            print(f"❌ HTML form SSID creation failed: {e}")
            return False

    # ==================== QOS CONFIGURATION METHODS ====================

    def configure_qos_for_device(self, device_ip, slice_type, bandwidth_limit=None):
        """Configure QoS settings for a specific device based on slice type"""
        try:
            session = self._huawei_login()
            if not session:
                return self._simulate_qos_config(device_ip, slice_type, bandwidth_limit)
            
            # Map slice type to QoS parameters
            qos_config = self._get_qos_parameters(slice_type, bandwidth_limit)
            
            # Try different QoS configuration endpoints
            endpoints = [
                '/api/device/qos',
                '/api/qos/device',
                '/api/network/bandwidth'
            ]
            
            for endpoint in endpoints:
                qos_url = f"http://{self.router_ip}{endpoint}"
                
                config = {
                    "DeviceIP": device_ip,
                    **qos_config
                }
                
                headers = {
                    'Content-Type': 'application/json',
                    'Referer': f'http://{self.router_ip}/'
                }
                
                response = session.post(qos_url, json=config, headers=headers, timeout=10)
                
                if response.status_code == 200:
                    print(f"✅ QoS configured for {device_ip} via {endpoint}")
                    return True
                else:
                    print(f"❌ QoS endpoint {endpoint} failed: {response.status_code}")
            
            return self._simulate_qos_config(device_ip, slice_type, bandwidth_limit)
            
        except Exception as e:
            print(f"❌ QoS configuration error: {e}")
            return self._simulate_qos_config(device_ip, slice_type, bandwidth_limit)

    def _get_qos_parameters(self, slice_type, bandwidth_limit=None):
        """Get QoS parameters based on slice type"""
        base_params = {}
        
        if slice_type == 'GAMING':
            base_params = {
                "Priority": "7",
                "DSCP": "46",    # EF
                "MinBandwidth": "20%",
                "MaxBandwidth": "100%",
                "Latency": "low",
                "Jitter": "low"
            }
        elif slice_type == 'CORP':
            base_params = {
                "Priority": "5",
                "DSCP": "34",    # AF41
                "MinBandwidth": "30%",
                "MaxBandwidth": "80%",
                "Latency": "medium",
                "Jitter": "medium"
            }
        elif slice_type == 'GUEST':
            base_params = {
                "Priority": "2",
                "DSCP": "10",    # AF11 / low
                "MinBandwidth": "0%",
                "MaxBandwidth": "30%",
                "Latency": "high",
                "Jitter": "high"
            }
        else:  # IOT
            base_params = {
                "Priority": "3",
                "DSCP": "18",    # AF21
                "MinBandwidth": "5%",
                "MaxBandwidth": "40%",
                "Latency": "medium",
                "Jitter": "medium"
            }
        
        # Override bandwidth if specified
        if bandwidth_limit:
            base_params["MaxBandwidth"] = f"{bandwidth_limit}Mbps"
            
        return base_params

    def _simulate_qos_config(self, device_ip, slice_type, bandwidth_limit=None):
        """Simulate QoS configuration when real configuration fails"""
        params = self._get_qos_parameters(slice_type, bandwidth_limit)
        print(f"📊 SIMULATED QoS for {device_ip}: {params}")
        print("💡 Note: In production, this would configure real router QoS")
        return True

    # ==================== NETWORK MONITORING METHODS ====================

    def get_network_metrics(self, device_ip=None):
        """Get current network metrics for monitoring"""
        try:
            metrics = {
                'timestamp': time.time(),
                'device_ip': device_ip or self.test_device_ip
            }
            
            # Get system network stats
            net_io = psutil.net_io_counters()
            metrics.update({
                'bytes_sent': net_io.bytes_sent,
                'bytes_recv': net_io.bytes_recv,
                'packets_sent': net_io.packets_sent,
                'packets_recv': net_io.packets_recv,
                'error_in': net_io.errin,
                'error_out': net_io.errout,
                'drop_in': net_io.dropin,
                'drop_out': net_io.dropout
            })
            
            # Try to get router-specific metrics
            router_metrics = self._get_router_metrics(device_ip)
            metrics.update(router_metrics)
            
            return metrics
            
        except Exception as e:
            print(f"❌ Network metrics error: {e}")
            return self._get_simulated_metrics(device_ip)

    # def _get_router_metrics(self, device_ip=None):
    #     """Get metrics from Huawei router"""
    #     try:
    #         session = self._huawei_login()
    #         if not session:
    #             return {}
            
    #         metrics_url = f"http://{self.router_ip}/api/monitoring/traffic-statistics"
    #         response = session.get(metrics_url, timeout=10)
            
    #         if response.status_code == 200:
    #             return response.json()
    #         else:
    #             return {}
                
    #     except Exception as e:
    #         print(f"❌ Router metrics error: {e}")
    #         return {}

    def _get_simulated_metrics(self, device_ip=None):
        """Generate simulated network metrics"""
        return {
            'timestamp': time.time(),
            'device_ip': device_ip or self.test_device_ip,
            'bytes_sent': random.randint(1000, 100000),
            'bytes_recv': random.randint(1000, 100000),
            'packets_sent': random.randint(10, 1000),
            'packets_recv': random.randint(10, 1000),
            'latency': random.uniform(1, 50),
            'jitter': random.uniform(0.1, 5.0),
            'packet_loss': random.uniform(0, 0.5),
            'simulated': True
        }

    # ==================== UTILITY METHODS ====================

    def generate_wifi_qr_code(self, ssid_name, password, security='WPA'):
        """Generate QR code for WiFi connection"""
        try:
            # Format: WIFI:S:<SSID>;T:<security>;P:<password>;;
            wifi_config = f"WIFI:S:{ssid_name};T:{security};P:{password};;"
            
            qr = qrcode.QRCode(
                version=1,
                error_correction=qrcode.constants.ERROR_CORRECT_L,
                box_size=10,
                border=4,
            )
            qr.add_data(wifi_config)
            qr.make(fit=True)
            
            img = qr.make_image(fill_color="black", back_color="white")
            
            # Convert to base64 for web display
            buffer = io.BytesIO()
            img.save(buffer, format="PNG")
            img_str = base64.b64encode(buffer.getvalue()).decode()
            
            return f"data:image/png;base64,{img_str}"
            
        except Exception as e:
            print(f"❌ QR code generation error: {e}")
            return None

    def test_network_connectivity(self, target_ip=None):
        """Test network connectivity to a target"""
        try:
            target = target_ip or "8.8.8.8"  # Google DNS
            result = subprocess.run(
                ['ping', '-c', '3', target],
                capture_output=True,
                text=True,
                timeout=10
            )
            
            if result.returncode == 0:
                # Parse ping results
                lines = result.stdout.split('\n')
                if len(lines) >= 4:
                    stats_line = lines[-2]
                    return {
                        'success': True,
                        'target': target,
                        'output': result.stdout
                    }
            
            return {
                'success': False,
                'target': target,
                'output': result.stderr or result.stdout
            }
            
        except Exception as e:
            return {
                'success': False,
                'target': target_ip,
                'error': str(e)
            }

    def cleanup_network_slice(self, slice_instance):
        """Clean up network resources when slice is deleted"""
        try:
            print(f"🧹 Starting cleanup for slice {slice_instance.id}")
            
            # Clean up Docker VLAN networks and discovery containers first
            docker_mgr = DockerVLANManager()
            docker_mgr.remove_vlan_network(slice_instance)
            
            # Clean up SoftAP virtual network if exists
            softap_mgr = SoftAPManager()
            if slice_instance.ssid_name:
                try:
                    softap_mgr.remove_virtual_network(slice_instance.ssid_name)
                except Exception as e:
                    print(f"⚠️  SoftAP cleanup warning: {e}")
            
            # Clean up router configuration
            try:
                session = self._huawei_login()
                if session and slice_instance.ssid_name:
                    self._remove_huawei_ssid(session, slice_instance.ssid_name)
            except Exception as e:
                print(f"⚠️  Router cleanup warning: {e}")
            
            print(f"✅ Cleaned up network resources for slice {slice_instance.id}")
            return True
            
        except Exception as e:
            print(f"❌ Cleanup error: {e}")
            return False

    # ==================== SLICE SPEED TEST ====================
    def measure_slice_speed(self, slice_instance, duration=3):
        """Approximate slice throughput and latency.
        Attempts iperf3 if available; falls back to host net I/O delta + ping gateway.
        This is a coarse estimate (single interface, no per-slice shaping isolation)."""
        result = {
            'slice_id': str(slice_instance.id),
            'slice_name': slice_instance.name,
            'allocated_bandwidth_mbps': slice_instance.bandwidth_mbps,
            'method': None,
            'measured_throughput_mbps': None,
            'avg_latency_ms': None,
            'details': {}
        }
        # Derive expected gateway from slice id (matches SoftAPManager logic)
        gateway_ip = None
        try:
            vlan_part = 100 + (hash(str(slice_instance.id)) % 155)
            gateway_ip = f"10.50.{vlan_part}.1"
        except Exception:
            pass

        # Try iperf3 client to gateway if server assumed running
        if gateway_ip and subprocess.run(['which', 'iperf3'], capture_output=True).returncode == 0:
            result['method'] = 'iperf3'
            try:
                # Run short iperf3 test
                iperf = subprocess.run(['iperf3', '-c', gateway_ip, '-t', str(duration), '-J'], capture_output=True, text=True, timeout=duration+5)
                if iperf.returncode == 0 and iperf.stdout:
                    import json as _json
                    j = _json.loads(iperf.stdout)
                    bits_per_second = j.get('end', {}).get('sum_received', {}).get('bits_per_second') or j.get('end', {}).get('sum', {}).get('bits_per_second')
                    if bits_per_second:
                        result['measured_throughput_mbps'] = round(bits_per_second / 1_000_000, 2)
                    # Latency not provided; do ping separately
            except Exception as e:
                result['details']['iperf3_error'] = str(e)
        else:
            # Fallback simple measurement
            result['method'] = 'net_io_delta'
            import psutil
            start = psutil.net_io_counters()
            # Generate light traffic (ping gateway or public DNS)
            target = gateway_ip or '1.1.1.1'
            try:
                subprocess.run(['ping', '-c', '5', target], capture_output=True, text=True, timeout=10)
            except Exception:
                pass
            time.sleep(duration)
            end = psutil.net_io_counters()
            bytes_delta = (end.bytes_recv - start.bytes_recv) + (end.bytes_sent - start.bytes_sent)
            # Convert to Mbps over duration
            result['measured_throughput_mbps'] = round((bytes_delta * 8) / (duration * 1_000_000), 2)

        # Latency measurement via ping
        if gateway_ip:
            try:
                ping = subprocess.run(['ping', '-c', '5', gateway_ip], capture_output=True, text=True, timeout=10)
                if ping.returncode == 0:
                    # Parse average latency
                    import re
                    m = re.search(r' = ([\d\.]+)/([\d\.]+)/([\d\.]+)/([\d\.]+) ms', ping.stdout)
                    if m:
                        avg = m.group(2)
                        result['avg_latency_ms'] = float(avg)
            except Exception:
                pass

        return result

    def _remove_huawei_ssid(self, session, ssid_name):
        """Remove SSID from Huawei router"""
        try:
            remove_url = f"http://{self.router_ip}/api/wlan/ssid"
            config = {
                "SSID": ssid_name,
                "Enable": "0"
            }
            
            headers = {
                'Content-Type': 'application/json',
                'Referer': f'http://{self.router_ip}/'
            }
            
            response = session.post(remove_url, json=config, headers=headers, timeout=10)
            return response.status_code == 200
            
        except Exception as e:
            print(f"❌ SSID removal error: {e}")
            return False