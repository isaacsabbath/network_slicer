# slicer/docker_manager.py
import subprocess
import random
import time
import logging
import json
from django.conf import settings

try:
    import docker
    DOCKER_PY_AVAILABLE = True
except Exception:
    DOCKER_PY_AVAILABLE = False
    docker = None  # Set to None for error handling

logger = logging.getLogger(__name__)

class DockerVLANManager:
    """Manage Docker networks used as VLAN slices.

    This is a lightweight manager that will try to use the docker SDK
    when available, and fall back to the docker CLI via subprocess.
    The implementation creates a Docker network with a name including
    the VLAN id and labels the network with the slice id for discovery.
    """

    def __init__(self):
        self.client = None
        if DOCKER_PY_AVAILABLE:
            try:
                self.client = docker.from_env()
            except Exception:
                self.client = None
        
        # Configuration for networks
        self.parent_interface = getattr(settings, 'VLAN_PARENT_INTERFACE', 'wlan0')
        self.subnet_base = getattr(settings, 'VLAN_SUBNET_BASE', '172.17')
        self.use_macvlan = getattr(settings, 'USE_MACVLAN_NETWORKS', False)
        self.use_default_bridge = getattr(settings, 'USE_DEFAULT_BRIDGE', True)
        self.macvlan_mode = getattr(settings, 'VLAN_MACVLAN_MODE', 'bridge')  # bridge|private|vepa|passthru

    def _generate_vlan_id(self, slice_instance):
        # Keep VLAN IDs in 100-999 range
        return 100 + (hash(str(slice_instance.id)) % 900)

    def create_vlan_network(self, slice_instance):
        """Setup Docker for a slice (using default docker0 bridge).

        Returns the assigned vlan_id on success, or None on failure.
        """
        vlan_id = self._generate_vlan_id(slice_instance)
        
        # When using default bridge, we don't create networks
        if self.use_default_bridge:
            logger.info(f"Using default docker0 bridge for slice {slice_instance.id}")
            # Just apply QoS to docker0 and create discovery container
            try:
                bw = getattr(slice_instance, 'bandwidth_mbps', None)
                lat = getattr(slice_instance, 'latency_ms', None)
                # Setup NAT for internet access
                self._setup_docker0_nat()
                
                if bw and lat:
                    self._apply_qos_to_docker0(bw, lat)
                    
                # Create discovery container on default bridge
                self._create_discovery_container_on_default_bridge(slice_instance, vlan_id)
                return vlan_id
            except Exception as e:
                logger.error(f"Failed to setup docker0 for slice {slice_instance.id}: {e}")
                return None
        
        # Original macvlan/custom bridge logic
        name = f"slice_vlan_{vlan_id}_{str(slice_instance.id)[:8]}"
        
        # Check if network already exists and remove it first
        self._cleanup_existing_network(name)
        
        subnet = f"{self.subnet_base}.{vlan_id % 255}.0/24"
        # Use .254 as gateway so containers route via host mvlan interface
        gateway = f"{self.subnet_base}.{vlan_id % 255}.254"

        labels = {
            'network_slice_id': str(slice_instance.id),
            'vlan_id': str(vlan_id),
            'slice_name': slice_instance.name,
            'slice_type': slice_instance.slice_type,
            'ssid_name': slice_instance.ssid_name or '',
            'wifi_password': slice_instance.wifi_password or ''
        }

        # Try docker SDK first
        try:
            if self.client:
                if self.use_macvlan:
                    # Create macvlan network for better isolation
                    ipam_pool = docker.types.IPAMPool(
                        subnet=subnet,
                        gateway=gateway
                    )
                    ipam_config = docker.types.IPAMConfig(
                        pool_configs=[ipam_pool]
                    )
                    
                    network = self.client.networks.create(
                        name, 
                        driver='macvlan',
                        labels=labels,
                        ipam=ipam_config,
                        options={'parent': self.parent_interface, 'macvlan_mode': self.macvlan_mode}
                    )
                    logger.info(f"Created macvlan network {name} (vlan {vlan_id}) on {self.parent_interface}")
                else:
                    # Create bridge network with custom subnet
                    ipam_pool = docker.types.IPAMPool(
                        subnet=subnet,
                        gateway=gateway
                    )
                    ipam_config = docker.types.IPAMConfig(
                        pool_configs=[ipam_pool]
                    )
                    
                    network = self.client.networks.create(
                        name, 
                        driver='bridge',
                        labels=labels,
                        ipam=ipam_config
                    )
                    logger.info(f"Created bridge network {name} (vlan {vlan_id})")
                
                # Store network info for discovery
                self._create_discovery_container(slice_instance, network, vlan_id)
                # Try to attach host-side macvlan for reachability from host
                try:
                    self._attach_host_macvlan_interface(vlan_id, subnet)
                except Exception as e:
                    logger.warning(f"Host macvlan attach failed (non-fatal): {e}")
                else:
                    try:
                        # Enable routing + NAT for container subnet to reach internet via parent interface
                        self._enable_routing_and_nat(subnet)
                    except Exception as e:
                        logger.warning(f"NAT setup failed (non-fatal): {e}")
                    try:
                        # Apply QoS shaping if bandwidth/latency defined
                        bw = getattr(slice_instance, 'bandwidth_mbps', None)
                        lat = getattr(slice_instance, 'latency_ms', None)
                        if bw and lat:
                            self._apply_qos_to_interface(vlan_id, bw, lat)
                    except Exception as e:
                        logger.warning(f"QoS shaping failed (non-fatal): {e}")
                return vlan_id

            # Fallback to CLI
            return self._create_network_cli(slice_instance, name, vlan_id, subnet, gateway, labels)

        except Exception as e:
            logger.error(f"Failed to create docker network for slice {slice_instance.id}: {e}")
            return None
    
    def _cleanup_existing_network(self, network_name):
        """Remove existing network with the same name if it exists"""
        try:
            if self.client:
                try:
                    existing_network = self.client.networks.get(network_name)
                    existing_network.remove()
                    logger.info(f"Removed existing network: {network_name}")
                except Exception as e:
                    if 'not found' in str(e).lower() or 'NotFound' in str(e):
                        pass  # Network doesn't exist, which is fine
                    else:
                        logger.warning(f"Error removing existing network: {e}")
            else:
                # CLI fallback
                try:
                    subprocess.run(['docker', 'network', 'rm', network_name], 
                                 check=False, timeout=10, capture_output=True)
                except Exception:
                    pass  # Network might not exist
        except Exception as e:
            logger.warning(f"Error cleaning up existing network {network_name}: {e}")

    def _create_network_cli(self, slice_instance, name, vlan_id, subnet, gateway, labels):
        """Create network using Docker CLI as fallback"""
        try:
            cmd = ['docker', 'network', 'create']
            
            if self.use_macvlan:
                cmd.extend(['--driver', 'macvlan', '--opt', f'parent={self.parent_interface}', '--opt', f'macvlan_mode={self.macvlan_mode}'])
            else:
                cmd.extend(['--driver', 'bridge'])
            
            cmd.extend([
                '--subnet', subnet,
                '--gateway', gateway
            ])
            
            # Add labels
            for key, value in labels.items():
                cmd.extend(['--label', f'{key}={value}'])
            
            cmd.append(name)
            
            result = subprocess.run(cmd, check=True, timeout=20, capture_output=True, text=True)
            logger.info(f"Created network {name} (vlan {vlan_id}) via CLI")
            
            # Create discovery container via CLI
            self._create_discovery_container_cli(slice_instance, name, vlan_id)
            # Try to attach host-side macvlan for reachability from host
            try:
                self._attach_host_macvlan_interface(vlan_id, subnet)
            except Exception as e:
                logger.warning(f"Host macvlan attach failed (non-fatal): {e}")
            else:
                try:
                    self._enable_routing_and_nat(subnet)
                except Exception as e:
                    logger.warning(f"NAT setup failed (non-fatal): {e}")
                try:
                    bw = getattr(slice_instance, 'bandwidth_mbps', None)
                    lat = getattr(slice_instance, 'latency_ms', None)
                    if bw and lat:
                        self._apply_qos_to_interface(vlan_id, bw, lat)
                except Exception as e:
                    logger.warning(f"QoS shaping failed (non-fatal): {e}")
            return vlan_id
            
        except subprocess.CalledProcessError as e:
            logger.error(f"CLI network creation failed: {e.stderr}")
            return None

    def _create_discovery_container(self, slice_instance, network, vlan_id):
        """Create a lightweight container for network discovery and WiFi simulation"""
        try:
            container_name = f"slice_discovery_{str(slice_instance.id)[:8]}"
            
            # Create a simple container that serves WiFi info
            wifi_info = {
                'ssid': slice_instance.ssid_name or f"NetSlice_{slice_instance.slice_type}_{str(slice_instance.id)[:8]}",
                'password': slice_instance.wifi_password,
                'slice_id': str(slice_instance.id),
                'slice_type': slice_instance.slice_type,
                'vlan_id': vlan_id,
                'network_name': network.name
            }
            
            # Create a simpler Python script
            python_script = f"""
import http.server
import json
import socketserver

data = {json.dumps(wifi_info)}

class Handler(http.server.SimpleHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.send_header('Content-type', 'application/json')
        self.end_headers()
        self.wfile.write(json.dumps(data).encode())

httpd = socketserver.TCPServer(('', 8080), Handler)
print('Network slice discovery server started on port 8080')
httpd.serve_forever()
"""
            
            # Run the HTTP server container
            container = self.client.containers.run(
                'python:3.9-alpine',
                ['python3', '-c', python_script],
                name=container_name,
                network=network.name,
                detach=True,
                remove=True,
                labels={
                    'slice_id': str(slice_instance.id),
                    'slice_discovery': 'true'
                }
            )
            
            logger.info(f"Created discovery container {container_name} for slice {slice_instance.id}")
            
        except Exception as e:
            logger.warning(f"Failed to create discovery container: {e}")

    def _create_discovery_container_on_default_bridge(self, slice_instance, vlan_id):
        """Create discovery container on default docker0 bridge"""
        try:
            container_name = f"slice_discovery_{str(slice_instance.id)[:8]}"
            
            wifi_info = {
                'ssid': slice_instance.ssid_name or f"NetSlice_{slice_instance.slice_type}_{str(slice_instance.id)[:8]}",
                'password': slice_instance.wifi_password,
                'slice_id': str(slice_instance.id),
                'slice_type': slice_instance.slice_type,
                'vlan_id': vlan_id,
                'network_name': 'bridge (docker0)',
                'bandwidth_mbps': slice_instance.bandwidth_mbps,
                'latency_ms': slice_instance.latency_ms
            }
            
            python_script = f"""
import http.server
import json
import socketserver

data = {json.dumps(wifi_info)}

class Handler(http.server.SimpleHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.send_header('Content-type', 'application/json')
        self.end_headers()
        self.wfile.write(json.dumps(data).encode())

httpd = socketserver.TCPServer(('', 8080), Handler)
print('Network slice discovery server started on port 8080')
httpd.serve_forever()
"""
            
            if self.client:
                # Use SDK
                container = self.client.containers.run(
                    'python:3.9-alpine',
                    ['python3', '-c', python_script],
                    name=container_name,
                    network='bridge',  # Default bridge (docker0)
                    detach=True,
                    remove=True,
                    labels={
                        'slice_id': str(slice_instance.id),
                        'slice_discovery': 'true'
                    }
                )
                logger.info(f"Created discovery container {container_name} on docker0")
            else:
                # Use CLI
                cmd = [
                    'docker', 'run', '-d', '--rm',
                    '--name', container_name,
                    '--network', 'bridge',
                    '--label', f'slice_id={slice_instance.id}',
                    '--label', 'slice_discovery=true',
                    'python:3.9-alpine',
                    'python3', '-c', python_script
                ]
                subprocess.run(cmd, check=True, timeout=30)
                logger.info(f"Created discovery container {container_name} on docker0 via CLI")
                
        except Exception as e:
            logger.warning(f"Failed to create discovery container on docker0: {e}")

    def _create_discovery_container_cli(self, slice_instance, network_name, vlan_id):
        """Create discovery container using CLI"""
        try:
            container_name = f"slice_discovery_{str(slice_instance.id)[:8]}"
            
            wifi_info = {
                'ssid': slice_instance.ssid_name or f"NetSlice_{slice_instance.slice_type}_{str(slice_instance.id)[:8]}",
                'password': slice_instance.wifi_password,
                'slice_id': str(slice_instance.id),
                'slice_type': slice_instance.slice_type,
                'vlan_id': vlan_id,
                'network_name': network_name
            }
            
            # Create a simpler Python script that avoids complex escaping
            python_script = f"""
import http.server
import json
import socketserver

data = {json.dumps(wifi_info)}

class Handler(http.server.SimpleHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.send_header('Content-type', 'application/json')
        self.end_headers()
        self.wfile.write(json.dumps(data).encode())

httpd = socketserver.TCPServer(('', 8080), Handler)
print('Network slice discovery server started on port 8080')
httpd.serve_forever()
"""
            
            cmd = [
                'docker', 'run', '-d', '--rm',
                '--name', container_name,
                '--network', network_name,
                '--label', f'slice_id={slice_instance.id}',
                '--label', 'slice_discovery=true',
                'python:3.9-alpine',
                'python3', '-c', python_script
            ]
            
            subprocess.run(cmd, check=True, timeout=30)
            logger.info(f"Created discovery container {container_name} via CLI")
            
        except Exception as e:
            logger.warning(f"Failed to create discovery container via CLI: {e}")

    def remove_vlan_network(self, slice_instance):
        """Remove Docker network and discovery containers for given slice instance."""
        try:
            # Remove discovery containers first
            self._remove_discovery_containers(slice_instance)
            
            # Remove networks
            label = f"network_slice_id={slice_instance.id}"
            if self.client:
                networks = self.client.networks.list(filters={'label': label})
                for net in networks:
                    try:
                        net.remove()
                        logger.info(f"Removed docker network {net.name}")
                    except Exception:
                        logger.exception(f"Failed to remove docker network {net.name}")
                return True

            # CLI fallback: list networks and remove matching
            result = subprocess.run(['docker', 'network', 'ls', '--format', '{{.ID}} {{.Name}}'],
                                    capture_output=True, text=True, timeout=10)
            for line in result.stdout.splitlines():
                parts = line.split()
                if len(parts) >= 2:
                    net_name = parts[1]
                    if str(slice_instance.id)[:8] in net_name or 'slice_vlan_' in net_name:
                        try:
                            subprocess.run(['docker', 'network', 'rm', net_name], check=True, timeout=10)
                            logger.info(f"Removed docker network {net_name} via CLI")
                        except Exception:
                            logger.exception(f"Failed to remove docker network {net_name}")
            return True

        except Exception as e:
            logger.error(f"Error removing docker networks for slice {slice_instance.id}: {e}")
            return False

    def _remove_discovery_containers(self, slice_instance):
        """Remove discovery containers for a slice"""
        try:
            if self.client:
                containers = self.client.containers.list(
                    all=True,
                    filters={'label': f'slice_id={slice_instance.id}'}
                )
                for container in containers:
                    try:
                        container.stop(timeout=5)
                        container.remove()
                        logger.info(f"Removed discovery container {container.name}")
                    except Exception:
                        logger.exception(f"Failed to remove container {container.name}")
            else:
                # CLI fallback
                container_name = f"slice_discovery_{str(slice_instance.id)[:8]}"
                try:
                    subprocess.run(['docker', 'stop', container_name], timeout=10)
                    subprocess.run(['docker', 'rm', container_name], timeout=10)
                    logger.info(f"Removed discovery container {container_name} via CLI")
                except Exception:
                    pass  # Container might not exist
                    
        except Exception as e:
            logger.warning(f"Error removing discovery containers: {e}")

    def get_slice_network_info(self, slice_instance):
        """Get network information for a slice including discovery endpoint"""
        try:
            if self.client:
                networks = self.client.networks.list(
                    filters={'label': f'network_slice_id={slice_instance.id}'}
                )
                if networks:
                    network = networks[0]
                    containers = self.client.containers.list(
                        filters={'label': f'slice_id={slice_instance.id}'}
                    )
                    
                    info = {
                        'network_name': network.name,
                        'network_id': network.id,
                        'driver': network.attrs.get('Driver', 'unknown'),
                        'subnet': network.attrs.get('IPAM', {}).get('Config', [{}])[0].get('Subnet', 'unknown'),
                        'gateway': network.attrs.get('IPAM', {}).get('Config', [{}])[0].get('Gateway', 'unknown'),
                        'discovery_containers': [c.name for c in containers],
                        'discoverable': len(containers) > 0
                    }
                    
                    if containers:
                        # Get IP of discovery container
                        container = containers[0]
                        networks_info = container.attrs.get('NetworkSettings', {}).get('Networks', {})
                        for net_name, net_info in networks_info.items():
                            if 'slice_vlan_' in net_name:
                                info['discovery_ip'] = net_info.get('IPAddress')
                                info['discovery_url'] = f"http://{info['discovery_ip']}:8080"
                                break
                    
                    return info
            
            return None
            
        except Exception as e:
            logger.error(f"Error getting network info for slice {slice_instance.id}: {e}")
            return None

    def _setup_docker0_nat(self):
        """Setup NAT and forwarding rules for docker0 bridge to access internet via eth0/wlan0"""
        try:
            # Enable IP forwarding
            subprocess.run(['sysctl', '-w', 'net.ipv4.ip_forward=1'], check=False, capture_output=True)
            
            # Detect outbound interface
            out_if = self._detect_upstream_iface()
            docker0_subnet = '172.17.0.0/16'
            
            # Check if MASQUERADE rule exists, add if not
            check = subprocess.run(
                ['iptables', '-t', 'nat', '-C', 'POSTROUTING', '-s', docker0_subnet, '-o', out_if, '-j', 'MASQUERADE'],
                capture_output=True, check=False
            )
            if check.returncode != 0:
                subprocess.run(['iptables', '-t', 'nat', '-A', 'POSTROUTING', '-s', docker0_subnet, '-o', out_if, '-j', 'MASQUERADE'], check=True)
                logger.info(f"Added NAT rule for docker0 → {out_if}")
            
            # Check if FORWARD rules exist
            check = subprocess.run(
                ['iptables', '-C', 'FORWARD', '-s', docker0_subnet, '-o', out_if, '-j', 'ACCEPT'],
                capture_output=True, check=False
            )
            if check.returncode != 0:
                subprocess.run(['iptables', '-A', 'FORWARD', '-s', docker0_subnet, '-o', out_if, '-j', 'ACCEPT'], check=True)
                logger.info(f"Added FORWARD rule for docker0 → {out_if}")
            
            check = subprocess.run(
                ['iptables', '-C', 'FORWARD', '-d', docker0_subnet, '-i', out_if, '-m', 'state', '--state', 'ESTABLISHED,RELATED', '-j', 'ACCEPT'],
                capture_output=True, check=False
            )
            if check.returncode != 0:
                subprocess.run(['iptables', '-A', 'FORWARD', '-d', docker0_subnet, '-i', out_if, '-m', 'state', '--state', 'ESTABLISHED,RELATED', '-j', 'ACCEPT'], check=True)
                logger.info(f"Added FORWARD return rule for {out_if} → docker0")
                
        except Exception as e:
            logger.warning(f"Failed to setup NAT for docker0: {e}")

    def _attach_host_macvlan_interface(self, vlan_id: int, subnet: str):
        """Attach a host-side macvlan sub-interface so the host can reach containers.
        This requires CAP_NET_ADMIN and may not work on all Wi-Fi drivers.
        Non-fatal if it fails.
        """
        try:
            # Compute /24 base like '172.20.X'
            base = subnet.split('/')[0].rsplit('.', 1)[0]
            host_ip = f"{base}.254/24"
            mv_name = f"mvlan{vlan_id}"

            # Delete if exists
            subprocess.run(['ip', 'link', 'del', mv_name], check=False, capture_output=True)
            # Create and bring up
            subprocess.run(['ip', 'link', 'add', mv_name, 'link', self.parent_interface, 'type', 'macvlan', 'mode', self.macvlan_mode], check=True)
            subprocess.run(['ip', 'addr', 'add', host_ip, 'dev', mv_name], check=True)
            subprocess.run(['ip', 'link', 'set', mv_name, 'up'], check=True)
        except Exception as e:
            raise e

    def _enable_routing_and_nat(self, subnet_cidr: str):
        """Enable IPv4 forwarding and set up NAT for the given subnet to egress via parent interface."""
        try:
            # Enable forwarding
            subprocess.run(['sysctl', '-w', 'net.ipv4.ip_forward=1'], check=False, capture_output=True)

            out_if = self._detect_upstream_iface()
            # Idempotent-ish iptables setup
            # MASQUERADE
            subprocess.run(['iptables', '-t', 'nat', '-C', 'POSTROUTING', '-s', subnet_cidr, '-o', out_if, '-j', 'MASQUERADE'], capture_output=True)
            subprocess.run(['iptables', '-t', 'nat', '-A', 'POSTROUTING', '-s', subnet_cidr, '-o', out_if, '-j', 'MASQUERADE'], check=False)
            # Forward rules
            mv_cidr_base = subnet_cidr  # We don't know device name here; rely on stateful rule
            subprocess.run(['iptables', '-C', 'FORWARD', '-s', mv_cidr_base, '-o', out_if, '-j', 'ACCEPT'], capture_output=True)
            subprocess.run(['iptables', '-A', 'FORWARD', '-s', mv_cidr_base, '-o', out_if, '-j', 'ACCEPT'], check=False)
            subprocess.run(['iptables', '-C', 'FORWARD', '-d', mv_cidr_base, '-i', out_if, '-m', 'state', '--state', 'ESTABLISHED,RELATED', '-j', 'ACCEPT'], capture_output=True)
            subprocess.run(['iptables', '-A', 'FORWARD', '-d', mv_cidr_base, '-i', out_if, '-m', 'state', '--state', 'ESTABLISHED,RELATED', '-j', 'ACCEPT'], check=False)
        except Exception as e:
            raise e

    def _detect_upstream_iface(self) -> str:
        # Honor explicit setting first
        explicit = getattr(settings, 'UPSTREAM_INTERFACE', None)
        if explicit:
            return explicit
        try:
            r = subprocess.run(['ip', '-o', 'route', 'get', '1.1.1.1'], capture_output=True, text=True)
            if r.returncode == 0 and ' dev ' in r.stdout:
                parts = r.stdout.strip().split()
                if 'dev' in parts:
                    return parts[parts.index('dev')+1]
        except Exception:
            pass
        # Fallback to configured parent or common names
        for candidate in [self.parent_interface, 'eth0', 'enp0s25', 'enp2s0', 'wlan0']:
            try:
                if subprocess.run(['ip', 'link', 'show', candidate], capture_output=True).returncode == 0:
                    return candidate
            except Exception:
                continue
        return self.parent_interface

    def _apply_qos_to_docker0(self, bandwidth_mbps: int, latency_ms: int):
        """Apply bandwidth and latency constraints using tc on docker0 bridge.
        This will affect ALL containers on the default bridge.
        Uses HTB for bandwidth shaping and netem for latency.
        """
        bridge_name = 'docker0'
        
        if bandwidth_mbps <= 0:
            return
            
        # Calculate burst size (1.5x the rate for a 100ms buffer)
        burst_kb = max(32, int(bandwidth_mbps * 1024 * 0.15))  # 15% of rate
        
        try:
            # Remove any existing qdisc
            subprocess.run(['tc', 'qdisc', 'del', 'dev', bridge_name, 'root'], 
                         check=False, capture_output=True)
            
            # Create HTB root qdisc
            subprocess.run(['tc', 'qdisc', 'add', 'dev', bridge_name, 'root', 
                          'handle', '1:', 'htb', 'default', '1'], check=True)
            
            # Create HTB class with bandwidth limit
            subprocess.run(['tc', 'class', 'add', 'dev', bridge_name, 'parent', '1:', 
                          'classid', '1:1', 'htb', 'rate', f'{bandwidth_mbps}mbit', 
                          'burst', f'{burst_kb}k'], check=True)
            
            # Add netem for latency as child of HTB class
            subprocess.run(['tc', 'qdisc', 'add', 'dev', bridge_name, 'parent', '1:1', 
                          'handle', '10:', 'netem', 'delay', f'{latency_ms}ms'], check=True)
            
            logger.info(f"Applied QoS to {bridge_name}: rate={bandwidth_mbps}mbit latency={latency_ms}ms burst={burst_kb}k")
            
        except subprocess.CalledProcessError as e:
            logger.error(f"Failed to apply QoS to {bridge_name}: {e}")
            raise

    def _apply_qos_to_interface(self, vlan_id: int, bandwidth_mbps: int, latency_ms: int):
        """Apply bandwidth and latency constraints using tc on host macvlan interface.
        Uses a netem root qdisc for latency and a child tbf for bandwidth.
        Non-fatal if tc is unavailable or permissions are insufficient.
        """
        mv_name = f"mvlan{vlan_id}"
        # Basic sanity
        if bandwidth_mbps <= 0:
            return
        # Burst: choose 32k or proportional
        burst = '32k'
        try:
            # Remove any existing qdisc
            subprocess.run(['tc', 'qdisc', 'del', 'dev', mv_name, 'root'], check=False, capture_output=True)
            # Add netem for latency
            subprocess.run(['tc', 'qdisc', 'add', 'dev', mv_name, 'root', 'handle', '1:', 'netem', 'delay', f'{latency_ms}ms'], check=True)
            # Add tbf for bandwidth shaping
            subprocess.run(['tc', 'qdisc', 'add', 'dev', mv_name, 'parent', '1:', 'handle', '10:', 'tbf', 'rate', f'{bandwidth_mbps}mbit', 'burst', burst, 'latency', f'{latency_ms}ms'], check=True)
            logger.info(f"Applied QoS to {mv_name}: rate={bandwidth_mbps}mbit latency={latency_ms}ms")

            # Optional bidirectional ingress shaping via IFB
            if getattr(settings, 'ENABLE_BIDIRECTIONAL_QOS', False):
                ifb_name = f"ifb{vlan_id}"
                # Clean existing
                subprocess.run(['tc', 'qdisc', 'del', 'dev', mv_name, 'ingress'], check=False, capture_output=True)
                # Create IFB device if missing
                if not self._interface_exists(ifb_name):
                    subprocess.run(['ip', 'link', 'add', ifb_name, 'type', 'ifb'], check=True)
                    subprocess.run(['ip', 'link', 'set', ifb_name, 'up'], check=True)
                # Add ingress qdisc and redirect traffic to IFB
                subprocess.run(['tc', 'qdisc', 'add', 'dev', mv_name, 'handle', 'ffff:', 'ingress'], check=True)
                subprocess.run(['tc', 'filter', 'add', 'dev', mv_name, 'parent', 'ffff:', 'protocol', 'all', 'u32', 'match', 'u32', '0', '0', 'action', 'mirred', 'egress', 'redirect', 'dev', ifb_name], check=True)

                # Shape on IFB for ingress: latency + bandwidth
                subprocess.run(['tc', 'qdisc', 'del', 'dev', ifb_name, 'root'], check=False, capture_output=True)
                subprocess.run(['tc', 'qdisc', 'add', 'dev', ifb_name, 'root', 'handle', '1:', 'netem', 'delay', f'{latency_ms}ms'], check=True)
                subprocess.run(['tc', 'qdisc', 'add', 'dev', ifb_name, 'parent', '1:', 'handle', '10:', 'tbf', 'rate', f'{bandwidth_mbps}mbit', 'burst', burst, 'latency', f'{latency_ms}ms'], check=True)
                logger.info(f"Applied ingress QoS via {ifb_name}: rate={bandwidth_mbps}mbit latency={latency_ms}ms")
        except Exception as e:
            logger.warning(f"tc QoS setup failed on {mv_name}: {e}")

    def _interface_exists(self, name: str) -> bool:
        try:
            import os
            return os.path.exists(f"/sys/class/net/{name}")
        except Exception:
            return False
