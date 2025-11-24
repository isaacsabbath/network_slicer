# slicer/views.py
from rest_framework import viewsets, status
from rest_framework.decorators import action
from rest_framework.response import Response
from .models import NetworkSlice, Device, GuestCredential
from .serializers import NetworkSliceSerializer, DeviceSerializer, GuestCredentialSerializer
from .network_actions import HomeNetworkManager
from .qos_monitor import QoSMonitor
from django.utils import timezone
from datetime import timedelta
import threading
import time


class NetworkSliceViewSet(viewsets.ModelViewSet):
    # Provide base queryset so DRF router can derive basename
    queryset = NetworkSlice.objects.all().order_by('-created_at')
    def get_queryset(self):
        qs = NetworkSlice.objects.all().order_by('-created_at')
        user = getattr(self.request, 'user', None)
        if not user or not user.is_authenticated:
            return qs.none()
        if not user.is_staff:
            return qs.filter(owner=user)
        return qs
    serializer_class = NetworkSliceSerializer
    
    def create(self, request):
        """Create a new slice request and simulate provisioning"""
        serializer = self.get_serializer(data=request.data)
        if serializer.is_valid():
            slice_instance = serializer.save(owner=request.user if request.user.is_authenticated else None)
            
            # Start the provisioning process in background
            self._simulate_provisioning(slice_instance.id)
            
            return Response(serializer.data, status=status.HTTP_201_CREATED)
        return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)
    
    def _simulate_provisioning(self, slice_id):
        """Background task to simulate network provisioning with REAL QoS"""
        def provision():
            print(f"🚀 Starting provisioning for slice {slice_id}")
            time.sleep(2)  # Initial delay
            
            slice_obj = NetworkSlice.objects.get(id=slice_id)
            slice_obj.status = 'PROVISIONING'
            slice_obj.save()
            print(f"🔄 Slice {slice_obj.name} is PROVISIONING")
            
            # 🔥 CREATE VIRTUAL NETWORK FIRST
            network_mgr = HomeNetworkManager()
            
            # Step 1: Create virtual SSID
            ssid_created = network_mgr.create_virtual_ssid(slice_obj)
            
            if ssid_created:
                print(f"✅ Virtual network created: {slice_obj.ssid_name}")
            else:
                print("⚠️  Using simulation mode for virtual network")
            
            # Step 2: Apply QoS configuration
            config_success = network_mgr.apply_slice_configuration(slice_obj)
            
            if config_success:
                print(f"✅ Network configuration applied for {slice_obj.name}")
            else:
                print(f"⚠️  Using simulation mode for {slice_obj.name}")
            
            time.sleep(3)  # Simulate more provisioning time
            
            # Activate the slice
            slice_obj.status = 'ACTIVE'
            slice_obj.activated_at = timezone.now()
            slice_obj.expires_at = timezone.now() + timedelta(hours=slice_obj.duration_hours)
            slice_obj.save()
            
            print(f" Slice {slice_obj.name} is now ACTIVE!")
            print(f" Connect to WiFi: {slice_obj.ssid_name}")
            print(f" Password: {slice_obj.wifi_password}")
            
            # Start monitoring
            network_mgr.start_network_monitoring(slice_obj)
            
            # Schedule deactivation
            threading.Timer(
                slice_obj.duration_hours * 3600,
                self._deactivate_slice,
                args=[slice_obj.id]
            ).start()
        
        thread = threading.Thread(target=provision)
        thread.daemon = True
        thread.start()
    
    def _deactivate_slice(self, slice_id):
        """Deactivate slice after duration expires"""
        try:
            slice_obj = NetworkSlice.objects.get(id=slice_id)
            slice_obj.status = 'INACTIVE'
            slice_obj.save()
            
            # Remove virtual network
            network_mgr = HomeNetworkManager()
            network_mgr.remove_virtual_ssid(slice_obj)
            
            print(f"🛑 Slice {slice_obj.name} has been deactivated")
        except NetworkSlice.DoesNotExist:
            print(f"❌ Could not find slice {slice_id} for deactivation")
    
    def destroy(self, request, *args, **kwargs):
        """Override destroy to ensure proper cleanup before deletion"""
        slice_obj = self.get_object()
        
        # Cleanup network resources
        network_mgr = HomeNetworkManager()
        network_mgr.cleanup_network_slice(slice_obj)
        
        print(f"🗑️ Deleting slice {slice_obj.name}")
        return super().destroy(request, *args, **kwargs)
    
    @action(detail=True, methods=['get'])
    def qos_status(self, request, pk=None):
        """Get QoS verification status for a slice"""
        slice_instance = self.get_object()
        verification_result = QoSMonitor.verify_slice_qos(slice_instance)
        return Response(verification_result)
    
    @action(detail=True, methods=['post'])
    def terminate(self, request, pk=None):
        """Custom action to manually terminate a slice"""
        slice_obj = self.get_object()
        slice_obj.status = 'INACTIVE'
        slice_obj.save()
        
        # Remove virtual network
        network_mgr = HomeNetworkManager()
        network_mgr.remove_virtual_ssid(slice_obj)
        
        print(f"🛑 Slice {slice_obj.name} manually terminated")
        
        # Redirect to dashboard instead of returning JSON
        from django.shortcuts import redirect
        return redirect('dashboard')
    
    @action(detail=False, methods=['get'])
    def metrics(self, request):
        """Get comprehensive network metrics from Huawei router"""
        network_mgr = HomeNetworkManager()
        
        try:
            metrics = network_mgr.get_network_metrics()
            return Response(metrics)
        except Exception as e:
            return Response({
                'error': str(e),
                'message': 'Failed to get router metrics'
            }, status=500)
    
    @action(detail=True, methods=['get'])
    def qr_code(self, request, pk=None):
        """Get QR code for slice WiFi connection"""
        slice_obj = self.get_object()
        
        if slice_obj.status != 'ACTIVE':
            return Response({'error': 'Slice is not active'}, status=400)
        
        network_mgr = HomeNetworkManager()
        qr_code_data = network_mgr.generate_wifi_qr_code(
            slice_obj.ssid_name, 
            slice_obj.wifi_password
        )
        
        return Response({
            'ssid': slice_obj.ssid_name,
            'password': slice_obj.wifi_password,
            'qr_code': qr_code_data,
            'connection_instructions': f'Connect to WiFi: {slice_obj.ssid_name}'
        })
    
    @action(detail=False, methods=['get'])
    def router_capabilities(self, request):
        """Check router capabilities for virtual networks"""
        network_mgr = HomeNetworkManager()
        capabilities = network_mgr.check_router_capabilities()
        return Response(capabilities)
    
    # ADD THIS NEW ENDPOINT
    @action(detail=False, methods=['get'])
    def softap_capabilities(self, request):
        """Check SoftAP capabilities for virtual networks"""
        network_mgr = HomeNetworkManager()
        capabilities = network_mgr.check_softap_capabilities()
        return Response(capabilities)
    
    @action(detail=True, methods=['get'])
    def connected_devices(self, request, pk=None):
        """Get devices connected to this slice's network"""
        slice_obj = self.get_object()
        
        if slice_obj.status != 'ACTIVE' or not slice_obj.ssid_name:
            return Response({'connected_devices': [], 'message': 'Slice not active or no network'})
        
        try:
            from .softap_manager import SoftAPManager
            softap_mgr = SoftAPManager()
            devices = softap_mgr.get_connected_devices(slice_obj)
            
            return Response({
                'slice_id': slice_obj.id,
                'slice_name': slice_obj.name,
                'ssid': slice_obj.ssid_name,
                'connected_devices': devices,
                'device_count': len(devices)
            })
        except Exception as e:
            return Response({
                'connected_devices': [],
                'error': str(e),
                'message': 'Failed to get connected devices'
            }, status=500)

    @action(detail=True, methods=['get'])
    def ap_diagnostics(self, request, pk=None):
        """Return diagnostic information about the slice's access point."""
        slice_obj = self.get_object()
        from .softap_manager import SoftAPManager
        mgr = SoftAPManager()
        diag = mgr.diagnose_ap()
        diag['slice_id'] = str(slice_obj.id)
        diag['slice_status'] = slice_obj.status
        diag['ssid_name'] = slice_obj.ssid_name
        return Response(diag)

    @action(detail=True, methods=['get'])
    def speed_test(self, request, pk=None):
        """Run a quick throughput/latency test for this slice (approximate)."""
        slice_obj = self.get_object()
        if slice_obj.status != 'ACTIVE':
            return Response({'error': 'Slice not active'}, status=400)
        network_mgr = HomeNetworkManager()
        result = network_mgr.measure_slice_speed(slice_obj)
        return Response(result)

    @action(detail=False, methods=['get'])
    def topology(self, request):
        """Return network topology of active slices (nodes + links)."""
        from .softap_manager import SoftAPManager
        mgr = SoftAPManager()
        nodes = []
        links = []
        upstream = mgr._detect_upstream_iface() if hasattr(mgr, '_detect_upstream_iface') else 'eth0'
        nodes.append({
            'id': 'upstream',
            'name': upstream,
            'type': 'UPSTREAM'
        })
        for sl in NetworkSlice.objects.filter(status='ACTIVE').order_by('-created_at'):
            # Connected device count (best effort)
            ccount = 0
            try:
                ccount = len(mgr.get_connected_devices(sl))
            except Exception:
                ccount = 0
            nodes.append({
                'id': str(sl.id),
                'name': sl.name,
                'type': sl.slice_type,
                'bandwidth_mbps': sl.bandwidth_mbps,
                'latency_ms': sl.latency_ms,
                'vlan_id': sl.vlan_id,
                'ssid_name': sl.ssid_name,
                'connected_devices_count': ccount
            })
            links.append({'source': str(sl.id), 'target': 'upstream'})
        return Response({'nodes': nodes, 'links': links})

    @action(detail=False, methods=['get'])
    def status_snapshot(self, request):
        """Return lightweight status + device counts for all slices for rapid polling."""
        data = []
        from .softap_manager import SoftAPManager
        mgr = SoftAPManager()
        for sl in NetworkSlice.objects.all().order_by('-created_at'):
            count = 0
            if sl.status == 'ACTIVE' and sl.ssid_name:
                try:
                    count = len(mgr.get_connected_devices(sl))
                except Exception:
                    count = 0
            data.append({
                'id': str(sl.id),
                'name': sl.name,
                'status': sl.status,
                'slice_type': sl.slice_type,
                'ssid_name': sl.ssid_name,
                'connected_devices_count': count,
                # Include wifi_password only when ACTIVE so UI can populate immediately without extra fetch
                'wifi_password': sl.wifi_password if sl.status == 'ACTIVE' else None
            })
        return Response({'results': data})

from django.views.generic import TemplateView
from .docker_manager import DockerVLANManager

from django.contrib.auth.mixins import LoginRequiredMixin

class SliceDashboardView(LoginRequiredMixin, TemplateView):
    template_name = 'slicer/dashboard.html'
    
    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        user = self.request.user
        if user.is_staff:
            context['slices'] = NetworkSlice.objects.all().order_by('-created_at')
        else:
            context['slices'] = NetworkSlice.objects.filter(owner=user).order_by('-created_at')
        
        # Add QR codes, connection info, and network discovery info for active slices
        network_mgr = HomeNetworkManager()
        from .docker_manager import DockerVLANManager
        docker_mgr = DockerVLANManager()
        
        for slice_obj in context['slices']:
            if slice_obj.ssid_name and slice_obj.wifi_password:
                slice_obj.qr_code = network_mgr.generate_wifi_qr_code(
                    slice_obj.ssid_name, 
                    slice_obj.wifi_password
                )
                slice_obj.connection_info = {
                    'ssid': slice_obj.ssid_name,
                    'password': slice_obj.wifi_password
                }
                
                # Add Docker network discovery info
                slice_obj.network_info = docker_mgr.get_slice_network_info(slice_obj)
        
        return context

    def post(self, request, *args, **kwargs):
        """Handle form submission from dashboard to create a new slice."""
        # Check for delete action first
        if request.POST.get('action') == 'delete':
            slice_id = request.POST.get('slice_id')
            if slice_id:
                try:
                    slice_obj = NetworkSlice.objects.get(id=slice_id)
                    # Check ownership - users can only delete their own slices
                    if request.user == slice_obj.owner or request.user.is_staff:
                        slice_obj.delete()  # This will trigger our cleanup
                        print(f"🗑️ Deleted slice {slice_obj.name}")
                    else:
                        print(f"❌ User {request.user.username} not authorized to delete slice {slice_id}")
                except NetworkSlice.DoesNotExist:
                    print(f"❌ Slice {slice_id} not found")
            # Redirect to dashboard to prevent form resubmission
            from django.shortcuts import redirect
            return redirect('dashboard')
        
        # Handle slice creation
        name = request.POST.get('name') or 'New Slice'
        slice_type = request.POST.get('slice_type') or 'CORP'
        bandwidth = int(request.POST.get('bandwidth_mbps') or 10)
        latency = int(request.POST.get('latency_ms') or 50)
        duration = int(request.POST.get('duration_hours') or 1)

        # Prevent duplicate creation by checking if slice with same name exists recently
        existing = NetworkSlice.objects.filter(
            name=name, 
            created_at__gte=timezone.now() - timedelta(seconds=30)
        ).first()
        
        if existing:
            print(f"⚠️ Slice '{name}' was created recently, skipping duplicate")
            return self.get(request, *args, **kwargs)

        slice_obj = NetworkSlice.objects.create(
            name=name,
            slice_type=slice_type,
            bandwidth_mbps=bandwidth,
            latency_ms=latency,
            duration_hours=duration,
            status='REQUESTED',
            owner=request.user if request.user.is_authenticated else None
        )

        # Create docker network to represent VLAN slice
        try:
            docker_mgr = DockerVLANManager()
            vlan_id = docker_mgr.create_vlan_network(slice_obj)
            if vlan_id:
                slice_obj.vlan_id = vlan_id
                slice_obj.save()
        except Exception as e:
            print(f"❌ Docker network creation error: {e}")

        # Start provisioning in background using the existing viewset helper
        try:
            viewset = NetworkSliceViewSet()
            viewset._simulate_provisioning(slice_obj.id)
        except Exception as e:
            print(f"⚠️  Could not start provisioning thread: {e}")

        # Redirect back to the dashboard (render same template with updated context)
        return self.get(request, *args, **kwargs)


class DeviceViewSet(viewsets.ModelViewSet):
    queryset = Device.objects.all().order_by('-created_at')
    serializer_class = DeviceSerializer

    @action(detail=False, methods=['post'])
    def bulk_import(self, request):
        """Import devices from CSV (mac_address,slice_id?,device_type?,hostname?)"""
        import csv, io
        content = request.body.decode('utf-8')
        reader = csv.reader(io.StringIO(content))
        created, updated = 0, 0
        for row in reader:
            if not row or row[0].startswith('#'):
                continue
            mac = row[0].strip().lower()
            slice_id = row[1].strip() if len(row) > 1 and row[1] else None
            device_type = row[2].strip() if len(row) > 2 and row[2] else None
            hostname = row[3].strip() if len(row) > 3 and row[3] else None

            device, created_flag = Device.objects.get_or_create(mac_address=mac)
            if slice_id:
                try:
                    sl = NetworkSlice.objects.get(id=slice_id)
                    device.slice = sl
                except NetworkSlice.DoesNotExist:
                    pass
            if device_type:
                device.device_type = device_type
            if hostname:
                device.hostname = hostname
            device.save()
            created += 1 if created_flag else 0
            updated += 0 if created_flag else 1

        return Response({"created": created, "updated": updated})

    @action(detail=False, methods=['post'])
    def refresh(self, request):
        """Refresh device last_seen and IP from ARP/neigh tables (best-effort)."""
        import subprocess, re
        now = timezone.now()
        try:
            out = subprocess.check_output(['ip', 'neigh'], text=True)
            for line in out.splitlines():
                m = re.search(r'(?P<ip>\d+\.\d+\.\d+\.\d+) dev .* lladdr (?P<mac>[0-9a-f:]{17})', line)
                if m:
                    mac = m.group('mac').lower()
                    ip = m.group('ip')
                    dev, _ = Device.objects.get_or_create(mac_address=mac)
                    dev.ip_address = ip
                    dev.last_seen = now
                    dev.save()
        except Exception:
            pass
        return Response({"status": "ok"})


class GuestCredentialViewSet(viewsets.ModelViewSet):
    queryset = GuestCredential.objects.all().order_by('-created_at')
    serializer_class = GuestCredentialSerializer

    @action(detail=False, methods=['post'])
    def generate(self, request):
        """Generate a guest credential tied to a Guest slice with expiration."""
        from django.utils.crypto import get_random_string
        from datetime import timedelta

        slice_id = request.data.get('slice_id')
        hours = int(request.data.get('hours', 8))
        if not slice_id:
            return Response({"error": "slice_id required"}, status=400)
        try:
            sl = NetworkSlice.objects.get(id=slice_id, slice_type='GUEST')
        except NetworkSlice.DoesNotExist:
            return Response({"error": "Guest slice not found"}, status=404)

        code = get_random_string(12)
        cred = GuestCredential.objects.create(
            code=code,
            slice=sl,
            expires_at=timezone.now() + timedelta(hours=hours)
        )
        return Response(GuestCredentialSerializer(cred).data, status=201)