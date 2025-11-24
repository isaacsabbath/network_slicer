"""
VLAN Manager for dynamic device assignment
"""
import subprocess
import logging
from typing import Optional, Tuple

logger = logging.getLogger(__name__)


class VLANManager:
    """Manage VLAN bridges and device assignments"""
    
    QUARANTINE_VLAN = 99
    QUARANTINE_BRIDGE = "br-vlan99"
    
    @staticmethod
    def run_command(command: list, check=True) -> Tuple[bool, str]:
        """Execute shell command and return success status and output"""
        try:
            result = subprocess.run(
                command,
                capture_output=True,
                text=True,
                check=check
            )
            return True, result.stdout
        except subprocess.CalledProcessError as e:
            logger.error(f"Command failed: {' '.join(command)}\nError: {e.stderr}")
            return False, e.stderr
    
    @classmethod
    def create_vlan_bridge(cls, vlan_id: int, subnet: str, gateway: str) -> bool:
        """
        Create a VLAN bridge interface
        
        Args:
            vlan_id: VLAN ID (1-4094)
            subnet: Subnet CIDR (e.g., "10.0.10.0/24")
            gateway: Gateway IP address
        
        Returns:
            True if successful, False otherwise
        """
        bridge_name = f"br-vlan{vlan_id}"
        
        # Check if bridge already exists
        success, output = cls.run_command(['ip', 'link', 'show', bridge_name], check=False)
        if success and bridge_name in output:
            logger.info(f"Bridge {bridge_name} already exists")
            return True
        
        # Create bridge
        success, _ = cls.run_command(['ip', 'link', 'add', 'name', bridge_name, 'type', 'bridge'])
        if not success:
            return False
        
        # Assign IP address to bridge
        success, _ = cls.run_command(['ip', 'addr', 'add', f"{gateway}/{subnet.split('/')[1]}", 'dev', bridge_name])
        if not success:
            return False
        
        # Bring bridge up
        success, _ = cls.run_command(['ip', 'link', 'set', bridge_name, 'up'])
        if not success:
            return False
        
        logger.info(f"Created bridge {bridge_name} with IP {gateway}")
        return True
    
    @classmethod
    def apply_bandwidth_limit(cls, bridge_name: str, bandwidth_mbps: int, latency_ms: int = 50) -> bool:
        """
        Apply traffic control (tc) bandwidth limits to a bridge
        
        Args:
            bridge_name: Bridge interface name
            bandwidth_mbps: Maximum bandwidth in Mbps
            latency_ms: Target latency in milliseconds
        
        Returns:
            True if successful, False otherwise
        """
        # Remove existing qdisc if present
        cls.run_command(['tc', 'qdisc', 'del', 'dev', bridge_name, 'root'], check=False)
        
        # Calculate rate and burst
        rate_kbit = bandwidth_mbps * 1024
        burst_kb = rate_kbit // 8  # 1 second worth of data
        
        # Create HTB root qdisc
        success, _ = cls.run_command([
            'tc', 'qdisc', 'add', 'dev', bridge_name, 'root', 'handle', '1:', 'htb', 'default', '1'
        ])
        if not success:
            return False
        
        # Create HTB class with bandwidth limit
        success, _ = cls.run_command([
            'tc', 'class', 'add', 'dev', bridge_name, 'parent', '1:', 'classid', '1:1',
            'htb', 'rate', f"{rate_kbit}kbit", 'burst', f"{burst_kb}k"
        ])
        if not success:
            return False
        
        # Add netem for latency (optional)
        if latency_ms > 0:
            success, _ = cls.run_command([
                'tc', 'qdisc', 'add', 'dev', bridge_name, 'parent', '1:1', 'handle', '10:',
                'netem', 'delay', f"{latency_ms}ms"
            ])
            if not success:
                logger.warning(f"Failed to apply latency to {bridge_name}, but bandwidth limit is active")
        
        logger.info(f"Applied {bandwidth_mbps} Mbps limit to {bridge_name}")
        return True
    
    @classmethod
    def setup_quarantine_vlan(cls) -> bool:
        """
        Setup quarantine VLAN (VLAN 99) with severe bandwidth restriction
        
        Returns:
            True if successful, False otherwise
        """
        success = cls.create_vlan_bridge(
            vlan_id=cls.QUARANTINE_VLAN,
            subnet="192.168.99.0/24",
            gateway="192.168.99.1"
        )
        if not success:
            return False
        
        # Apply very restrictive bandwidth (100 kbps)
        return cls.apply_bandwidth_limit(cls.QUARANTINE_BRIDGE, bandwidth_mbps=0.1, latency_ms=100)
    
    @classmethod
    def move_device_to_vlan(cls, mac_address: str, from_vlan: int, to_vlan: int) -> bool:
        """
        Move a device from one VLAN to another
        
        This is conceptual - actual implementation depends on hostapd integration
        For now, this logs the action. In production, this would trigger hostapd
        dynamic VLAN reassignment.
        
        Args:
            mac_address: Device MAC address
            from_vlan: Current VLAN ID
            to_vlan: Target VLAN ID
        
        Returns:
            True if successful, False otherwise
        """
        logger.info(f"Moving device {mac_address} from VLAN {from_vlan} to VLAN {to_vlan}")
        
        # In production, this would:
        # 1. Update hostapd VLAN assignment file
        # 2. Trigger hostapd reload or dynamic VLAN update
        # 3. Force device reassociation if needed
        
        # For now, just log
        return True
    
    @classmethod
    def get_bridge_stats(cls, bridge_name: str) -> Optional[dict]:
        """
        Get statistics for a bridge interface
        
        Args:
            bridge_name: Bridge interface name
        
        Returns:
            Dictionary with rx/tx bytes and packets, or None if failed
        """
        success, output = cls.run_command(['ip', '-s', 'link', 'show', bridge_name], check=False)
        if not success:
            return None
        
        # Parse output (simplified)
        lines = output.strip().split('\n')
        stats = {}
        
        try:
            # Find RX and TX lines
            for i, line in enumerate(lines):
                if 'RX:' in line and i + 1 < len(lines):
                    rx_data = lines[i + 1].split()
                    stats['rx_bytes'] = int(rx_data[0])
                    stats['rx_packets'] = int(rx_data[1])
                elif 'TX:' in line and i + 1 < len(lines):
                    tx_data = lines[i + 1].split()
                    stats['tx_bytes'] = int(tx_data[0])
                    stats['tx_packets'] = int(tx_data[1])
        except (IndexError, ValueError) as e:
            logger.error(f"Failed to parse bridge stats: {e}")
            return None
        
        return stats
    
    @classmethod
    def verify_qos(cls, bridge_name: str) -> Optional[dict]:
        """
        Verify QoS settings on a bridge
        
        Args:
            bridge_name: Bridge interface name
        
        Returns:
            Dictionary with QoS parameters, or None if not configured
        """
        success, output = cls.run_command(['tc', 'class', 'show', 'dev', bridge_name], check=False)
        if not success or not output:
            return None
        
        # Parse tc output to extract rate
        qos_info = {'configured': False}
        
        for line in output.split('\n'):
            if 'htb' in line and 'rate' in line:
                # Extract rate (simplified parsing)
                parts = line.split()
                for i, part in enumerate(parts):
                    if part == 'rate' and i + 1 < len(parts):
                        rate_str = parts[i + 1]
                        # Convert to Mbps
                        if 'Kbit' in rate_str:
                            rate_kbit = int(rate_str.replace('Kbit', ''))
                            qos_info['bandwidth_mbps'] = rate_kbit / 1024
                        elif 'Mbit' in rate_str:
                            qos_info['bandwidth_mbps'] = int(rate_str.replace('Mbit', ''))
                        qos_info['configured'] = True
        
        return qos_info if qos_info['configured'] else None
