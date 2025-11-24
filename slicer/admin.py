# slicer/admin.py
from django.contrib import admin
from .models import NetworkSlice, Device, GuestCredential

@admin.register(NetworkSlice)
class NetworkSliceAdmin(admin.ModelAdmin):
    list_display = ['name', 'owner', 'slice_type', 'status', 'created_at', 'activated_at']
    list_filter = ['slice_type', 'status', 'created_at']
    search_fields = ['name', 'owner__username']
    readonly_fields = ['id', 'created_at', 'activated_at', 'expires_at']

# Or use this simpler version if you prefer:
# admin.site.register(NetworkSlice)


@admin.register(Device)
class DeviceAdmin(admin.ModelAdmin):
    list_display = ['mac_address', 'slice', 'ip_address', 'last_seen', 'created_at']
    search_fields = ['mac_address', 'hostname', 'ip_address']
    list_filter = ['slice']


@admin.register(GuestCredential)
class GuestCredentialAdmin(admin.ModelAdmin):
    list_display = ['code', 'slice', 'expires_at', 'used_flag', 'created_at']
    list_filter = ['slice']  # Boolean field caused system check issue; remove from filter
    search_fields = ['code']

    def used_flag(self, obj):
        return obj.used
    used_flag.boolean = True
    used_flag.short_description = 'Used'