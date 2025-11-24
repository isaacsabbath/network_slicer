# slicer/qos_monitor.py
import subprocess
import re
import logging
from typing import Dict, Optional, Tuple

logger = logging.getLogger(__name__)

class QoSMonitor:
    """Monitor and verify actual QoS settings for network slices"""
    
    @staticmethod
    def get_interface_qos_info(interface_name: str) -> Dict[str, any]:
        """Get current QoS information for a network interface"""
        try:
            # Get qdisc information
            result = subprocess.run(
                ['tc', 'qdisc', 'show', 'dev', interface_name],
                capture_output=True,
                text=True,
                check=True
            )
            
            qos_info = {
                'interface': interface_name,
                'qdisc_configured': False,
                'bandwidth_limit': None,
                'latency_setting': None,
                'raw_output': result.stdout.strip()
            }
            
            if result.stdout:
                qos_info['qdisc_configured'] = True
                
                # Parse bandwidth from tbf qdisc
                tbf_match = re.search(r'tbf.*?rate (\d+(?:\.\d+)?)([KMG]?)bit', result.stdout)
                if tbf_match:
                    rate = float(tbf_match.group(1))
                    unit = tbf_match.group(2) or ''
                    
                    # Convert to Mbps
                    if unit == 'K':
                        rate = rate / 1000
                    elif unit == 'G':
                        rate = rate * 1000
                    # M is default, no conversion needed
                    
                    qos_info['bandwidth_limit'] = rate
                
                # Also check tc class for HTB (used on docker0)
                if not qos_info['bandwidth_limit']:
                    try:
                        class_result = subprocess.run(
                            ['tc', 'class', 'show', 'dev', interface_name],
                            capture_output=True,
                            text=True,
                            check=True
                        )
                        htb_match = re.search(r'htb.*?rate (\d+(?:\.\d+)?)([KMG]?)bit', class_result.stdout)
                        if htb_match:
                            rate = float(htb_match.group(1))
                            unit = htb_match.group(2) or ''
                            
                            # Convert to Mbps
                            if unit == 'K':
                                rate = rate / 1000
                            elif unit == 'G':
                                rate = rate * 1000
                            
                            qos_info['bandwidth_limit'] = rate
                    except:
                        pass
                
                # Parse latency from netem
                netem_match = re.search(r'netem.*?delay (\d+(?:\.\d+)?)ms', result.stdout)
                if netem_match:
                    qos_info['latency_setting'] = float(netem_match.group(1))
            
            return qos_info
            
        except subprocess.CalledProcessError as e:
            logger.warning(f"Failed to get QoS info for {interface_name}: {e}")
            return {
                'interface': interface_name,
                'qdisc_configured': False,
                'error': str(e)
            }
        except Exception as e:
            logger.error(f"Unexpected error getting QoS info: {e}")
            return {
                'interface': interface_name,
                'qdisc_configured': False,
                'error': str(e)
            }
    
    @staticmethod
    def verify_slice_qos(slice_instance) -> Dict[str, any]:
        """Verify that QoS settings match the slice configuration"""
        from django.conf import settings
        
        # Check if using default docker0 bridge
        use_default_bridge = getattr(settings, 'USE_DEFAULT_BRIDGE', False)
        
        if use_default_bridge:
            # Monitor docker0 interface
            interface = 'docker0'
        else:
            # Original macvlan logic
            if not slice_instance.vlan_id:
                return {
                    'verified': False,
                    'error': 'No VLAN ID assigned to slice'
                }
            interface = f"mvlan{slice_instance.vlan_id}"
        
        qos_info = QoSMonitor.get_interface_qos_info(interface)
        
        verification_result = {
            'slice_id': str(slice_instance.id),
            'interface': interface,
            'requested_bandwidth': slice_instance.bandwidth_mbps,
            'requested_latency': slice_instance.latency_ms,
            'qos_configured': qos_info.get('qdisc_configured', False),
            'verified': False,
            'issues': [],
            'using_default_bridge': use_default_bridge
        }
        
        if qos_info.get('qdisc_configured'):
            actual_bandwidth = qos_info.get('bandwidth_limit')
            actual_latency = qos_info.get('latency_setting')
            
            verification_result.update({
                'actual_bandwidth': actual_bandwidth,
                'actual_latency': actual_latency,
            })
            
            # Check bandwidth allocation
            if actual_bandwidth:
                if abs(actual_bandwidth - slice_instance.bandwidth_mbps) < 0.1:
                    verification_result['bandwidth_verified'] = True
                else:
                    verification_result['bandwidth_verified'] = False
                    verification_result['issues'].append(
                        f"Bandwidth mismatch: requested {slice_instance.bandwidth_mbps}Mbps, "
                        f"actual {actual_bandwidth}Mbps"
                    )
            else:
                verification_result['bandwidth_verified'] = False
                verification_result['issues'].append("No bandwidth limit configured")
            
            # Check latency setting
            if actual_latency:
                if abs(actual_latency - slice_instance.latency_ms) < 1:
                    verification_result['latency_verified'] = True
                else:
                    verification_result['latency_verified'] = False
                    verification_result['issues'].append(
                        f"Latency mismatch: requested {slice_instance.latency_ms}ms, "
                        f"actual {actual_latency}ms"
                    )
            else:
                verification_result['latency_verified'] = False
                verification_result['issues'].append("No latency setting configured")
            
            # Overall verification
            verification_result['verified'] = (
                verification_result.get('bandwidth_verified', False) and 
                verification_result.get('latency_verified', False)
            )
        else:
            verification_result['issues'].append("No QoS configuration found on interface")
        
        return verification_result
    
    @staticmethod
    def get_interface_stats(interface_name: str) -> Dict[str, any]:
        """Get network interface statistics"""
        try:
            # Get interface statistics
            result = subprocess.run(
                ['cat', f'/proc/net/dev'],
                capture_output=True,
                text=True,
                check=True
            )
            
            for line in result.stdout.split('\n'):
                if interface_name in line:
                    parts = line.split()
                    if len(parts) >= 10:
                        return {
                            'interface': interface_name,
                            'rx_bytes': int(parts[1]),
                            'rx_packets': int(parts[2]),
                            'tx_bytes': int(parts[9]),
                            'tx_packets': int(parts[10]),
                        }
            
            return {'interface': interface_name, 'error': 'Interface not found'}
            
        except Exception as e:
            logger.error(f"Failed to get interface stats: {e}")
            return {'interface': interface_name, 'error': str(e)}