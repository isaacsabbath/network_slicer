"""
Captive Portal Views
"""
from django.shortcuts import render, redirect
from django.contrib.auth import authenticate, login
from django.contrib.auth.decorators import login_required
from django.http import JsonResponse, HttpResponse
from django.views.decorators.csrf import csrf_exempt
from django.utils import timezone
from django.db import transaction
import logging

from slicer.core.models import DeviceSession, NetworkSlice, UserSlicePermission, CaptivePortalLog
from slicer.network.vlan_manager import VLANManager

logger = logging.getLogger(__name__)


def get_client_mac(request):
    """Extract client MAC address from request (requires ARP lookup or DHCP logs)"""
    # In production, this would query ARP table or DHCP logs
    # For now, use a header if available
    mac = request.META.get('HTTP_X_CLIENT_MAC', '')
    if not mac:
        # Fallback: query ARP table based on IP
        client_ip = request.META.get('REMOTE_ADDR')
        # This is simplified - actual implementation would parse /proc/net/arp
        mac = f"00:00:00:00:00:00"  # Placeholder
    return mac


def get_or_create_session(mac_address, ip_address=None):
    """Get existing session or create new one in quarantine"""
    session, created = DeviceSession.objects.get_or_create(
        mac_address=mac_address,
        is_active=True,
        defaults={
            'ip_address': ip_address,
            'state': 'QUARANTINE',
            'current_slice': None
        }
    )
    
    if created:
        logger.info(f"Created new session for {mac_address} in quarantine")
        CaptivePortalLog.objects.create(
            session=session,
            log_type='REDIRECT',
            message=f"New device connected, placed in quarantine VLAN",
            mac_address=mac_address,
            ip_address=ip_address
        )
    
    return session


@csrf_exempt
def captive_portal_detect(request):
    """
    Endpoint for captive portal detection
    Many devices probe specific URLs to detect captive portals
    """
    # Return 204 if authenticated, redirect if not
    mac_address = get_client_mac(request)
    ip_address = request.META.get('REMOTE_ADDR')
    
    session = get_or_create_session(mac_address, ip_address)
    
    if session.state == 'ACTIVE' and not session.is_expired():
        # Device is authenticated, return success
        return HttpResponse(status=204)
    else:
        # Redirect to captive portal
        return redirect('captive_portal_landing')


def captive_portal_landing(request):
    """Main captive portal landing page"""
    mac_address = get_client_mac(request)
    ip_address = request.META.get('REMOTE_ADDR')
    
    session = get_or_create_session(mac_address, ip_address)
    
    # Update session info
    session.ip_address = ip_address
    session.user_agent = request.META.get('HTTP_USER_AGENT', '')
    session.captive_portal_shown = True
    session.save()
    
    context = {
        'session': session,
        'mac_address': mac_address,
        'quarantine_vlan': VLANManager.QUARANTINE_VLAN
    }
    
    return render(request, 'captive_portal/landing.html', context)


def captive_portal_login(request):
    """Handle captive portal authentication"""
    if request.method == 'POST':
        username = request.POST.get('username')
        password = request.POST.get('password')
        mac_address = get_client_mac(request)
        ip_address = request.META.get('REMOTE_ADDR')
        
        user = authenticate(request, username=username, password=password)
        
        if user is not None:
            login(request, user)
            
            # Get session
            session = get_or_create_session(mac_address, ip_address)
            session.user = user
            session.state = 'AUTHENTICATING'
            session.save()
            
            # Log successful login
            CaptivePortalLog.objects.create(
                session=session,
                log_type='LOGIN_SUCCESS',
                message=f"User {username} authenticated successfully",
                mac_address=mac_address,
                ip_address=ip_address,
                user=user
            )
            
            return redirect('captive_portal_slice_select')
        else:
            # Log failed login
            CaptivePortalLog.objects.create(
                log_type='LOGIN_FAILED',
                message=f"Failed login attempt for username: {username}",
                mac_address=mac_address,
                ip_address=ip_address
            )
            
            return render(request, 'captive_portal/landing.html', {
                'error': 'Invalid username or password',
                'mac_address': mac_address
            })
    
    return redirect('captive_portal_landing')


@login_required
def captive_portal_slice_select(request):
    """Allow user to select a network slice"""
    mac_address = get_client_mac(request)
    session = DeviceSession.objects.filter(mac_address=mac_address, is_active=True).first()
    
    if not session:
        return redirect('captive_portal_landing')
    
    # Get slices user has permission to access
    user_permissions = UserSlicePermission.objects.filter(
        user=request.user,
        can_access=True
    ).select_related('slice')
    
    allowed_slices = [perm.slice for perm in user_permissions]
    
    # If no specific permissions, allow access to all active slices
    if not allowed_slices:
        allowed_slices = NetworkSlice.objects.filter(is_active=True)
    
    # Get default slice for user
    default_slice = None
    default_perm = user_permissions.filter(is_default=True).first()
    if default_perm:
        default_slice = default_perm.slice
    elif NetworkSlice.objects.filter(is_default=True, is_active=True).exists():
        default_slice = NetworkSlice.objects.filter(is_default=True, is_active=True).first()
    
    if request.method == 'POST':
        slice_id = request.POST.get('slice_id')
        
        try:
            selected_slice = NetworkSlice.objects.get(id=slice_id, is_active=True)
            
            # Check if slice is at capacity
            if selected_slice.is_at_capacity:
                return render(request, 'captive_portal/slice_select.html', {
                    'slices': allowed_slices,
                    'session': session,
                    'error': f'Slice "{selected_slice.name}" is at maximum capacity'
                })
            
            # Activate session with selected slice
            with transaction.atomic():
                session.activate_session(request.user, selected_slice, duration_hours=24)
                
                # Log slice selection
                CaptivePortalLog.objects.create(
                    session=session,
                    log_type='SLICE_SELECTED',
                    message=f"User selected slice: {selected_slice.name}",
                    mac_address=mac_address,
                    ip_address=session.ip_address,
                    user=request.user
                )
                
                # TODO: Trigger VLAN assignment via hostapd
                # For now, just log the action
                VLANManager.move_device_to_vlan(
                    mac_address=mac_address,
                    from_vlan=VLANManager.QUARANTINE_VLAN,
                    to_vlan=selected_slice.vlan_id
                )
            
            return redirect('captive_portal_success')
            
        except NetworkSlice.DoesNotExist:
            return render(request, 'captive_portal/slice_select.html', {
                'slices': allowed_slices,
                'session': session,
                'error': 'Invalid slice selected'
            })
    
    context = {
        'slices': allowed_slices,
        'default_slice': default_slice,
        'session': session
    }
    
    return render(request, 'captive_portal/slice_select.html', context)


@login_required
def captive_portal_success(request):
    """Success page after slice selection"""
    mac_address = get_client_mac(request)
    session = DeviceSession.objects.filter(mac_address=mac_address, is_active=True).first()
    
    if not session or session.state != 'ACTIVE':
        return redirect('captive_portal_landing')
    
    context = {
        'session': session,
        'slice': session.current_slice
    }
    
    return render(request, 'captive_portal/success.html', context)


@csrf_exempt
def api_session_status(request, mac_address):
    """API endpoint to check session status"""
    try:
        session = DeviceSession.objects.filter(
            mac_address=mac_address,
            is_active=True
        ).select_related('current_slice', 'user').first()
        
        if not session:
            return JsonResponse({
                'status': 'not_found',
                'message': 'No active session found'
            }, status=404)
        
        data = {
            'status': session.state,
            'mac_address': session.mac_address,
            'ip_address': session.ip_address,
            'is_active': session.is_active,
            'is_expired': session.is_expired(),
            'connected_at': session.connected_at.isoformat(),
            'last_seen': session.last_seen.isoformat()
        }
        
        if session.user:
            data['user'] = session.user.username
        
        if session.current_slice:
            data['slice'] = {
                'id': str(session.current_slice.id),
                'name': session.current_slice.name,
                'vlan_id': session.current_slice.vlan_id,
                'bandwidth_mbps': session.current_slice.bandwidth_mbps
            }
        
        if session.expires_at:
            data['expires_at'] = session.expires_at.isoformat()
        
        return JsonResponse(data)
        
    except Exception as e:
        logger.error(f"Error fetching session status: {e}")
        return JsonResponse({
            'status': 'error',
            'message': str(e)
        }, status=500)
