# slicer/admin_views.py
from django.shortcuts import render, get_object_or_404, redirect
from django.contrib.admin.views.decorators import staff_member_required
from django.contrib.auth.decorators import login_required, user_passes_test
from django.contrib import messages
from django.http import JsonResponse
from django.views.decorators.http import require_http_methods
from django.views.decorators.csrf import csrf_exempt
from django.utils.decorators import method_decorator
from django.views.generic import TemplateView
from django.db.models import Avg, Sum, Count
from .models import NetworkSlice
from .network_actions import HomeNetworkManager
from .docker_manager import DockerVLANManager
import json
from datetime import datetime, timedelta
from django.utils import timezone

@method_decorator([login_required, staff_member_required], name='dispatch')
class QoSControllerView(TemplateView):
    template_name = 'slicer/qos_controller.html'
    
    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        
        # Get all slices with analytics
        slices = NetworkSlice.objects.all().order_by('-created_at')
        
        # Calculate statistics
        total_bandwidth = slices.filter(status='ACTIVE').aggregate(Sum('bandwidth_mbps'))['bandwidth_mbps__sum'] or 0
        avg_latency = slices.filter(status='ACTIVE').aggregate(Avg('latency_ms'))['latency_ms__avg'] or 0
        
        # Slice type distribution
        slice_distribution = slices.values('slice_type').annotate(count=Count('id'))
        
        # Recent activity (last 24 hours)
        recent_slices = slices.filter(
            created_at__gte=timezone.now() - timedelta(hours=24)
        ).count()
        
        # Resource utilization by type (GAMING, CORP, GUEST, IOT)
        resource_usage = {}
        for slice_type in ['GAMING', 'CORP', 'GUEST', 'IOT']:
            type_slices = slices.filter(slice_type=slice_type, status='ACTIVE')
            resource_usage[slice_type] = {
                'count': type_slices.count(),
                'bandwidth': type_slices.aggregate(Sum('bandwidth_mbps'))['bandwidth_mbps__sum'] or 0,
                'avg_latency': type_slices.aggregate(Avg('latency_ms'))['latency_ms__avg'] or 0
            }
        
        context.update({
            'slices': slices,
            'total_bandwidth': total_bandwidth,
            'avg_latency': round(avg_latency, 2),
            'slice_distribution': slice_distribution,
            'recent_slices': recent_slices,
            'resource_usage': resource_usage,
            'active_slices': slices.filter(status='ACTIVE'),
            'provisioning_slices': slices.filter(status='PROVISIONING'),
            'inactive_slices': slices.filter(status='INACTIVE'),
        })
        
        return context

@login_required
@staff_member_required
@require_http_methods(["POST"])
def adjust_qos(request, slice_id):
    """Adjust QoS parameters for a specific slice"""
    slice_obj = get_object_or_404(NetworkSlice, id=slice_id)
    
    try:
        data = json.loads(request.body)
        
        # Update slice parameters
        if 'bandwidth_mbps' in data:
            slice_obj.bandwidth_mbps = int(data['bandwidth_mbps'])
        if 'latency_ms' in data:
            slice_obj.latency_ms = int(data['latency_ms'])
        
        slice_obj.save()
        
        # Apply QoS changes based on slice type
        if slice_obj.ssid_name:  # WiFi slice
            try:
                from .softap_manager import SoftAPManager
                mgr = SoftAPManager()
                mgr._apply_qos_to_bridge(slice_obj)
                messages.success(request, f'QoS updated for WiFi slice {slice_obj.name}')
                return JsonResponse({'status': 'success', 'message': 'QoS parameters updated for WiFi slice'})
            except Exception as e:
                return JsonResponse({'status': 'error', 'message': f'Failed to apply QoS: {str(e)}'})
        else:  # Docker/container slice or router-based slice
            from .docker_manager import DockerVLANManager
            from django.conf import settings
            
            # Check if using default bridge (docker0)
            if getattr(settings, 'USE_DEFAULT_BRIDGE', False):
                try:
                    docker_mgr = DockerVLANManager()
                    docker_mgr._apply_qos_to_docker0(slice_obj.bandwidth_mbps, slice_obj.latency_ms or 0)
                    messages.success(request, f'QoS updated for container slice {slice_obj.name}')
                    return JsonResponse({'status': 'success', 'message': 'QoS parameters updated for container slice'})
                except Exception as e:
                    return JsonResponse({'status': 'error', 'message': f'Failed to apply QoS: {str(e)}'})
            else:
                # Fallback to router-based QoS
                network_mgr = HomeNetworkManager()
                success = network_mgr.configure_qos_for_device(
                    network_mgr.test_device_ip,
                    slice_obj.slice_type,
                    slice_obj.bandwidth_mbps
                )
                
                if success:
                    messages.success(request, f'QoS updated for slice {slice_obj.name}')
                    return JsonResponse({'status': 'success', 'message': 'QoS parameters updated'})
                else:
                    return JsonResponse({'status': 'warning', 'message': 'QoS update simulated'})
            
    except Exception as e:
        messages.error(request, f'Failed to update QoS: {str(e)}')
        return JsonResponse({'status': 'error', 'message': str(e)})

@login_required
@staff_member_required
@require_http_methods(["POST"])
def priority_control(request, slice_id):
    """Adjust slice priority for traffic management"""
    slice_obj = get_object_or_404(NetworkSlice, id=slice_id)
    
    try:
        data = json.loads(request.body)
        priority = data.get('priority', 'normal')
        
        # Map priority to bandwidth adjustment
        priority_multipliers = {
            'high': 1.5,
            'normal': 1.0,
            'low': 0.7
        }
        
        multiplier = priority_multipliers.get(priority, 1.0)
        new_bandwidth = int(slice_obj.bandwidth_mbps * multiplier)
        
        # Apply priority change based on slice type
        if slice_obj.ssid_name:  # WiFi slice
            try:
                from .softap_manager import SoftAPManager
                mgr = SoftAPManager()
                # Temporarily update bandwidth for priority
                original_bandwidth = slice_obj.bandwidth_mbps
                slice_obj.bandwidth_mbps = new_bandwidth
                mgr._apply_qos_to_bridge(slice_obj)
                slice_obj.bandwidth_mbps = original_bandwidth  # Restore original
                
                return JsonResponse({
                    'status': 'success',
                    'message': f'Priority set to {priority} for WiFi slice',
                    'new_bandwidth': new_bandwidth
                })
            except Exception as e:
                return JsonResponse({'status': 'error', 'message': str(e)})
        else:  # Container or router-based slice
            network_mgr = HomeNetworkManager()
            success = network_mgr.configure_qos_for_device(
                network_mgr.test_device_ip,
                slice_obj.slice_type,
                new_bandwidth
            )
            
            return JsonResponse({
                'status': 'success' if success else 'warning',
                'message': f'Priority set to {priority}',
                'new_bandwidth': new_bandwidth
            })
        
    except Exception as e:
        return JsonResponse({'status': 'error', 'message': str(e)})

@login_required
@staff_member_required
def network_topology(request):
    """Get network topology information"""
    docker_mgr = DockerVLANManager()
    from .softap_manager import SoftAPManager
    sm = SoftAPManager()
    upstream = sm._detect_upstream_iface() if hasattr(sm, '_detect_upstream_iface') else 'eth0'
    
    topology = {
        'nodes': [
            {'id': 'upstream', 'name': upstream, 'type': 'UPSTREAM'}
        ],
        'links': []
    }
    
    # Add slices as nodes
    active_slices = NetworkSlice.objects.filter(status='ACTIVE')
    for slice_obj in active_slices:
        network_info = docker_mgr.get_slice_network_info(slice_obj)
        # Connected device count (best effort)
        ccount = 0
        try:
            ccount = len(sm.get_connected_devices(slice_obj))
        except Exception:
            ccount = 0
        
        topology['nodes'].append({
            'id': str(slice_obj.id),
            'name': slice_obj.name,
            'type': slice_obj.slice_type,
            'bandwidth_mbps': slice_obj.bandwidth_mbps,
            'latency_ms': slice_obj.latency_ms,
            'network_info': network_info,
            'ssid_name': slice_obj.ssid_name,
            'vlan_id': slice_obj.vlan_id,
            'connected_devices_count': ccount
        })
        topology['links'].append({'source': str(slice_obj.id), 'target': 'upstream'})
    
    return JsonResponse(topology)

@login_required
@staff_member_required
def live_metrics(request):
    """Get real-time metrics for all slices"""
    network_mgr = HomeNetworkManager()
    metrics = network_mgr.get_network_metrics()
    
    # Add per-slice metrics
    slice_metrics = []
    for slice_obj in NetworkSlice.objects.filter(status='ACTIVE'):
        slice_metrics.append({
            'id': str(slice_obj.id),
            'name': slice_obj.name,
            'type': slice_obj.slice_type,
            'bandwidth_allocated': slice_obj.bandwidth_mbps,
            'latency_target': slice_obj.latency_ms,
            'vlan_id': slice_obj.vlan_id,
            'created_at': slice_obj.created_at.isoformat(),
            # Add simulated real-time metrics
            'current_throughput': slice_obj.bandwidth_mbps * 0.8,  # 80% utilization
            'current_latency': slice_obj.latency_ms * 1.1,  # Slightly higher than target
            'packet_loss': 0.01,  # 0.01% packet loss
            'connected_devices': 1
        })
    
    return JsonResponse({
        'system_metrics': metrics,
        'slice_metrics': slice_metrics,
        'timestamp': datetime.now().isoformat()
    })