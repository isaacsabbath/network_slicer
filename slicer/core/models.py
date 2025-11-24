# slicer/core/models.py
"""
Core models for the Network Slicing Captive Portal system
"""
from django.db import models
from django.contrib.auth.models import User
from django.utils import timezone
from datetime import timedelta
import uuid


class NetworkSlice(models.Model):
    """Network slice with VLAN and bandwidth configuration"""
    
    SLICE_TYPES = [
        ('CORP', 'Corporate'),
        ('GUEST', 'Guest'),
        ('IOT', 'IoT Devices'),
        ('GAMING', 'Gaming'),
        ('STREAMING', 'Streaming'),
    ]
    
    PRIORITY_LEVELS = [
        ('LOW', 'Low'),
        ('MEDIUM', 'Medium'),
        ('HIGH', 'High'),
        ('CRITICAL', 'Critical'),
    ]
    
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    name = models.CharField(max_length=100, unique=True)
    description = models.TextField(blank=True)
    slice_type = models.CharField(max_length=20, choices=SLICE_TYPES)
    
    # VLAN Configuration
    vlan_id = models.IntegerField(unique=True, help_text="VLAN ID (1-4094)")
    bridge_interface = models.CharField(max_length=50, help_text="Bridge interface name (e.g., br-vlan10)")
    
    # Bandwidth Configuration
    bandwidth_mbps = models.IntegerField(help_text="Maximum bandwidth in Mbps")
    latency_ms = models.IntegerField(default=50, help_text="Target latency in milliseconds")
    priority = models.CharField(max_length=20, choices=PRIORITY_LEVELS, default='MEDIUM')
    
    # Network Settings
    subnet = models.CharField(max_length=18, help_text="Subnet CIDR (e.g., 10.0.10.0/24)")
    gateway = models.GenericIPAddressField(help_text="Gateway IP address")
    dns_servers = models.CharField(max_length=255, default="8.8.8.8,8.8.4.4", help_text="Comma-separated DNS servers")
    
    # Capacity
    max_devices = models.IntegerField(default=50, help_text="Maximum number of devices allowed")
    
    # Status
    is_active = models.BooleanField(default=True)
    is_default = models.BooleanField(default=False, help_text="Default slice for authenticated users")
    
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    
    class Meta:
        ordering = ['vlan_id']
        verbose_name = "Network Slice"
        verbose_name_plural = "Network Slices"
    
    def __str__(self):
        return f"{self.name} (VLAN {self.vlan_id})"
    
    @property
    def current_device_count(self):
        """Get current number of devices in this slice"""
        return self.active_sessions.filter(is_active=True).count()
    
    @property
    def is_at_capacity(self):
        """Check if slice is at maximum capacity"""
        return self.current_device_count >= self.max_devices
    
    def get_dhcp_range(self):
        """Calculate DHCP range from subnet"""
        # Simple implementation - assumes /24 subnet
        base = self.subnet.split('/')[0].rsplit('.', 1)[0]
        return f"{base}.100", f"{base}.200"


class UserSlicePermission(models.Model):
    """User permissions for network slices"""
    
    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name='slice_permissions')
    slice = models.ForeignKey(NetworkSlice, on_delete=models.CASCADE, related_name='user_permissions')
    
    can_access = models.BooleanField(default=True)
    is_default = models.BooleanField(default=False, help_text="Default slice for this user")
    
    created_at = models.DateTimeField(auto_now_add=True)
    
    class Meta:
        unique_together = ['user', 'slice']
        verbose_name = "User Slice Permission"
        verbose_name_plural = "User Slice Permissions"
    
    def __str__(self):
        return f"{self.user.username} → {self.slice.name}"


class DeviceSession(models.Model):
    """Active device session tracking"""
    
    SESSION_STATES = [
        ('QUARANTINE', 'Quarantined'),
        ('AUTHENTICATING', 'Authenticating'),
        ('ACTIVE', 'Active'),
        ('EXPIRED', 'Expired'),
        ('TERMINATED', 'Terminated'),
    ]
    
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    
    # Device Information
    mac_address = models.CharField(max_length=17, db_index=True, help_text="Device MAC address")
    ip_address = models.GenericIPAddressField(null=True, blank=True)
    hostname = models.CharField(max_length=255, blank=True)
    user_agent = models.TextField(blank=True)
    
    # User Association
    user = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, blank=True, related_name='device_sessions')
    
    # Slice Assignment
    current_slice = models.ForeignKey(NetworkSlice, on_delete=models.SET_NULL, null=True, related_name='active_sessions')
    previous_slice = models.ForeignKey(NetworkSlice, on_delete=models.SET_NULL, null=True, blank=True, related_name='previous_sessions')
    
    # Session State
    state = models.CharField(max_length=20, choices=SESSION_STATES, default='QUARANTINE')
    is_active = models.BooleanField(default=True)
    
    # Timestamps
    connected_at = models.DateTimeField(auto_now_add=True)
    authenticated_at = models.DateTimeField(null=True, blank=True)
    last_seen = models.DateTimeField(auto_now=True)
    expires_at = models.DateTimeField(null=True, blank=True)
    terminated_at = models.DateTimeField(null=True, blank=True)
    
    # Session Data
    session_key = models.CharField(max_length=255, blank=True)
    captive_portal_shown = models.BooleanField(default=False)
    
    # Statistics
    bytes_uploaded = models.BigIntegerField(default=0)
    bytes_downloaded = models.BigIntegerField(default=0)
    
    class Meta:
        ordering = ['-connected_at']
        indexes = [
            models.Index(fields=['mac_address', 'is_active']),
            models.Index(fields=['state']),
        ]
        verbose_name = "Device Session"
        verbose_name_plural = "Device Sessions"
    
    def __str__(self):
        return f"{self.mac_address} ({self.get_state_display()})"
    
    def activate_session(self, user, slice, duration_hours=24):
        """Activate session for authenticated user"""
        self.user = user
        self.current_slice = slice
        self.state = 'ACTIVE'
        self.authenticated_at = timezone.now()
        self.expires_at = timezone.now() + timedelta(hours=duration_hours)
        self.save()
    
    def move_to_slice(self, new_slice):
        """Move device to a different slice"""
        self.previous_slice = self.current_slice
        self.current_slice = new_slice
        self.save()
    
    def is_expired(self):
        """Check if session is expired"""
        if self.expires_at:
            return timezone.now() > self.expires_at
        return False
    
    def terminate(self):
        """Terminate the session"""
        self.is_active = False
        self.state = 'TERMINATED'
        self.terminated_at = timezone.now()
        self.save()


class VLANAssignment(models.Model):
    """Track VLAN assignments for audit purposes"""
    
    session = models.ForeignKey(DeviceSession, on_delete=models.CASCADE, related_name='vlan_assignments')
    from_vlan = models.IntegerField(help_text="Previous VLAN ID")
    to_vlan = models.IntegerField(help_text="New VLAN ID")
    
    reason = models.CharField(max_length=255, help_text="Reason for VLAN change")
    assigned_by = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, blank=True, related_name='vlan_assignments_made')
    
    timestamp = models.DateTimeField(auto_now_add=True)
    
    class Meta:
        ordering = ['-timestamp']
        verbose_name = "VLAN Assignment"
        verbose_name_plural = "VLAN Assignments"
    
    def __str__(self):
        return f"{self.session.mac_address}: VLAN {self.from_vlan} → {self.to_vlan}"


class CaptivePortalLog(models.Model):
    """Log captive portal events"""
    
    LOG_TYPES = [
        ('REDIRECT', 'Portal Redirect'),
        ('LOGIN_SUCCESS', 'Login Success'),
        ('LOGIN_FAILED', 'Login Failed'),
        ('SLICE_SELECTED', 'Slice Selected'),
        ('SESSION_EXPIRED', 'Session Expired'),
        ('VLAN_CHANGED', 'VLAN Changed'),
    ]
    
    session = models.ForeignKey(DeviceSession, on_delete=models.CASCADE, null=True, blank=True, related_name='portal_logs')
    log_type = models.CharField(max_length=20, choices=LOG_TYPES)
    message = models.TextField()
    
    mac_address = models.CharField(max_length=17, db_index=True)
    ip_address = models.GenericIPAddressField(null=True, blank=True)
    user = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, blank=True)
    
    timestamp = models.DateTimeField(auto_now_add=True, db_index=True)
    
    class Meta:
        ordering = ['-timestamp']
        verbose_name = "Captive Portal Log"
        verbose_name_plural = "Captive Portal Logs"
    
    def __str__(self):
        return f"{self.get_log_type_display()} - {self.mac_address} @ {self.timestamp}"
