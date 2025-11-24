# slicer/models.py (Remove the custom save method)
from django.db import models
from django.contrib.auth import get_user_model
import uuid

class NetworkSlice(models.Model):
    SLICE_TYPES = [
        ('CORP', 'Corporate'),
        ('GUEST', 'Guest'),
        ('IOT', 'IoT'),
        ('GAMING', 'Gaming'),
    ]
    STATUS_CHOICES = [
        ('REQUESTED', 'Requested'),
        ('PROVISIONING', 'Provisioning'),
        ('ACTIVE', 'Active'),
        ('INACTIVE', 'Inactive'),
        ('FAILED', 'Failed'),
    ]
    
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    name = models.CharField(max_length=100)
    slice_type = models.CharField(max_length=6, choices=SLICE_TYPES)
    bandwidth_mbps = models.IntegerField()
    latency_ms = models.IntegerField()
    duration_hours = models.IntegerField()
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default='REQUESTED')
    owner = models.ForeignKey(get_user_model(), on_delete=models.CASCADE, related_name='slices', null=True, blank=True)
    
    # Virtual network fields
    ssid_name = models.CharField(max_length=100, blank=True, null=True)
    vlan_id = models.IntegerField(blank=True, null=True)
    wifi_password = models.CharField(max_length=100, default='slice123')
    
    created_at = models.DateTimeField(auto_now_add=True)
    activated_at = models.DateTimeField(null=True, blank=True)
    expires_at = models.DateTimeField(null=True, blank=True)
    
    def __str__(self):
        return f"{self.name} ({self.get_slice_type_display()}) - {self.status}"
    
    def delete(self, *args, **kwargs):
        """Override delete to cleanup network resources before deletion"""
        try:
            from .network_actions import HomeNetworkManager
            network_mgr = HomeNetworkManager()
            network_mgr.cleanup_network_slice(self)
        except Exception as e:
            print(f"⚠️  Cleanup error during slice deletion: {e}")
        
        super().delete(*args, **kwargs)
    
    # REMOVE the custom save() method or fix it like this:
    # def save(self, *args, **kwargs):
    #     if not self.ssid_name:
    #         uuid_str = str(self.id)  # Convert to string first
    #         self.ssid_name = f"NetworkSlice_{self.slice_type}_{uuid_str[:8]}"
    #     
    #     if not self.vlan_id:
    #         uuid_str = str(self.id)  # Convert to string first
    #         self.vlan_id = 100 + (hash(uuid_str) % 100)
    #     
    #     super().save(*args, **kwargs)


class Device(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    mac_address = models.CharField(max_length=17, unique=True)
    slice = models.ForeignKey(NetworkSlice, on_delete=models.SET_NULL, null=True, blank=True, related_name='devices')
    device_type = models.CharField(max_length=50, blank=True, null=True)
    hostname = models.CharField(max_length=100, blank=True, null=True)
    ip_address = models.GenericIPAddressField(blank=True, null=True)
    last_seen = models.DateTimeField(blank=True, null=True)
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"{self.mac_address} -> {self.slice.name if self.slice else 'Unassigned'}"


class GuestCredential(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    code = models.CharField(max_length=32, unique=True)
    slice = models.ForeignKey(NetworkSlice, on_delete=models.CASCADE, related_name='guest_credentials')
    expires_at = models.DateTimeField()
    created_at = models.DateTimeField(auto_now_add=True)
    usedx = models.BooleanField(default=False)

    def is_valid(self):
        from django.utils import timezone
        return (not self.used) and timezone.now() < self.expires_at

    def __str__(self):
        return f"{self.code} ({'valid' if self.is_valid() else 'expired'})"