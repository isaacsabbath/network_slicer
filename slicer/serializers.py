# slicer/serializers.py
from rest_framework import serializers
from .models import NetworkSlice, Device, GuestCredential

class NetworkSliceSerializer(serializers.ModelSerializer):
    qr_code = serializers.SerializerMethodField()
    connection_info = serializers.SerializerMethodField()
    connected_devices_count = serializers.SerializerMethodField()
    connected_devices = serializers.SerializerMethodField()
    
    class Meta:
        model = NetworkSlice
        fields = '__all__'
        read_only_fields = (
            'id', 'status', 'created_at', 'activated_at', 
            'expires_at', 'ssid_name', 'vlan_id', 'wifi_password'
        )
    
    def get_qr_code(self, obj):
        if obj.status == 'ACTIVE' and obj.ssid_name:
            from .network_actions import HomeNetworkManager
            network_mgr = HomeNetworkManager()
            return network_mgr.generate_wifi_qr_code(obj.ssid_name, obj.wifi_password)
        return None
        
    def get_connection_info(self, obj):
        if obj.status == 'ACTIVE' and obj.ssid_name:
            return {
                'ssid': obj.ssid_name,
                'password': obj.wifi_password,
                'instructions': f'Connect to WiFi: {obj.ssid_name}'
            }
        return None

    def get_connected_devices_count(self, obj):
        if obj.status != 'ACTIVE' or not obj.ssid_name:
            return 0
        try:
            from .softap_manager import SoftAPManager
            mgr = SoftAPManager()
            devices = mgr.get_connected_devices(obj)
            return len(devices)
        except Exception:
            return 0

    def get_connected_devices(self, obj):
        if obj.status != 'ACTIVE' or not obj.ssid_name:
            return []
        try:
            from .softap_manager import SoftAPManager
            mgr = SoftAPManager()
            devices = mgr.get_connected_devices(obj)
            # Return simplified structure
            return [
                {
                    'ip': d.get('ip'),
                    'mac': d.get('mac'),
                    'hostname': d.get('hostname'),
                    'last_seen': d.get('last_seen')
                } for d in devices
            ]
        except Exception:
            return []


class DeviceSerializer(serializers.ModelSerializer):
    class Meta:
        model = Device
        fields = '__all__'
        read_only_fields = ('id', 'created_at', 'last_seen')


class GuestCredentialSerializer(serializers.ModelSerializer):
    class Meta:
        model = GuestCredential
        fields = '__all__'
        read_only_fields = ('id', 'created_at', 'used')
    
    def get_connection_info(self, obj):
        if obj.status == 'ACTIVE' and obj.ssid_name:
            return {
                'ssid': obj.ssid_name,
                'password': obj.wifi_password,
                'instructions': f'Connect to WiFi: {obj.ssid_name}'
            }
        return None