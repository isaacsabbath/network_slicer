"""
Captive Portal URL Configuration
"""
from django.urls import path
from . import views

urlpatterns = [
    # Captive portal detection endpoints
    path('detect/', views.captive_portal_detect, name='captive_portal_detect'),
    path('generate_204', views.captive_portal_detect, name='generate_204'),  # Android
    path('hotspot-detect.html', views.captive_portal_detect, name='hotspot_detect'),  # iOS
    
    # Portal pages
    path('', views.captive_portal_landing, name='captive_portal_landing'),
    path('login/', views.captive_portal_login, name='captive_portal_login'),
    path('select/', views.captive_portal_slice_select, name='captive_portal_slice_select'),
    path('success/', views.captive_portal_success, name='captive_portal_success'),
    
    # API
    path('api/session/<str:mac_address>/', views.api_session_status, name='api_session_status'),
]
