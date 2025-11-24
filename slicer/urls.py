# slicer/urls.py
from django.urls import path, include
from django.contrib.auth import views as auth_views
from .views_auth import (
    RegisterView,
    UnifiedLoginView,
    UniversalLogoutView,
)
from rest_framework.routers import DefaultRouter
from . import views
from . import admin_views

router = DefaultRouter()
router.register(r'slices', views.NetworkSliceViewSet)
router.register(r'devices', views.DeviceViewSet)
router.register(r'guest-creds', views.GuestCredentialViewSet)

urlpatterns = [
    path('api/', include(router.urls)),
    path('', views.SliceDashboardView.as_view(), name='dashboard'),
    path('login/', UnifiedLoginView.as_view(), name='login'),
    path('logout/', UniversalLogoutView.as_view(), name='logout'),
    path('register/', RegisterView.as_view(), name='register'),
    
    # User password management (non-admin)
    path('password-change/', auth_views.PasswordChangeView.as_view(
        template_name='registration/password_change_form.html',
        success_url='/password-change-done/'
    ), name='password_change'),
    path('password-change-done/', auth_views.PasswordChangeDoneView.as_view(
        template_name='registration/password_change_done.html'
    ), name='password_change_done'),
    
    # QoS Controller Routes (moved off /admin to avoid conflict with Django admin site)
    path('qos/', admin_views.QoSControllerView.as_view(), name='qos_controller'),
    path('qos/adjust/<int:slice_id>/', admin_views.adjust_qos, name='adjust_qos'),
    path('qos/priority/<int:slice_id>/', admin_views.priority_control, name='priority_control'),
    path('qos/metrics/', admin_views.live_metrics, name='live_metrics'),
    path('qos/topology/', admin_views.network_topology, name='network_topology'),
]