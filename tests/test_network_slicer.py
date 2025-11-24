"""
Network Slicer System - Test Cases Implementation
Test suite for the SecureSlice Network Management System
"""

import unittest
from django.test import TestCase, Client
from django.contrib.auth import get_user_model
from django.urls import reverse
from django.db import IntegrityError
from slicer.models import NetworkSlice, Device, GuestCredential
from slicer.views import NetworkSliceViewSet
from rest_framework.test import APITestCase
from rest_framework import status
import json

User = get_user_model()

class AuthenticationTestCase(TestCase):
    """Test cases for user authentication and authorization"""
    
    def setUp(self):
        self.client = Client()
        self.superuser = User.objects.create_superuser(
            username='admin', 
            email='admin@test.com', 
            password='testpass123'
        )
        self.staff_user = User.objects.create_user(
            username='staff', 
            email='staff@test.com', 
            password='testpass123',
            is_staff=True
        )
        self.guest_user = User.objects.create_user(
            username='guest', 
            email='guest@test.com', 
            password='testpass123'
        )
    
    def test_valid_login_redirect(self):
        """Test that valid login redirects appropriately based on user role"""
        # Test guest user login
        response = self.client.post('/login/', {
            'username': 'guest',
            'password': 'testpass123'
        })
        self.assertEqual(response.status_code, 302)
        self.assertRedirects(response, '/')
        
        # Test staff user login  
        self.client.logout()
        response = self.client.post('/login/', {
            'username': 'staff',
            'password': 'testpass123'
        })
        self.assertEqual(response.status_code, 302)
        self.assertRedirects(response, '/admin/')
    
    def test_invalid_login_remains_on_page(self):
        """Test that invalid credentials keep user on login page"""
        response = self.client.post('/login/', {
            'username': 'invalid',
            'password': 'wrongpass'
        })
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Please enter a correct')
    
    def test_logout_redirect(self):
        """Test that logout redirects to login page"""
        self.client.login(username='guest', password='testpass123')
        response = self.client.get('/logout/')
        self.assertEqual(response.status_code, 200)  # Our custom logout view
        
    def test_unauthorized_dashboard_access(self):
        """Test that unauthenticated users cannot access dashboard"""
        response = self.client.get('/')
        self.assertEqual(response.status_code, 302)
        self.assertIn('/login/', response.url)


class NetworkSliceModelTestCase(TestCase):
    """Test cases for NetworkSlice model functionality"""
    
    def setUp(self):
        self.user = User.objects.create_user(
            username='testuser',
            email='test@test.com', 
            password='testpass123'
        )
    
    def test_slice_creation(self):
        """Test that network slices can be created with valid parameters"""
        slice_obj = NetworkSlice.objects.create(
            name='Test Slice',
            slice_type='CORP',
            bandwidth_mbps=50,
            latency_ms=20,
            duration_hours=2,
            owner=self.user
        )
        self.assertEqual(slice_obj.name, 'Test Slice')
        self.assertEqual(slice_obj.status, 'REQUESTED')
        self.assertEqual(slice_obj.owner, self.user)
    
    def test_slice_lifecycle_status(self):
        """Test slice status transitions"""
        slice_obj = NetworkSlice.objects.create(
            name='Lifecycle Test',
            slice_type='GUEST',
            bandwidth_mbps=10,
            latency_ms=50,
            duration_hours=1,
            owner=self.user
        )
        
        # Test initial status
        self.assertEqual(slice_obj.status, 'REQUESTED')
        
        # Test status change to provisioning
        slice_obj.status = 'PROVISIONING'
        slice_obj.save()
        self.assertEqual(slice_obj.status, 'PROVISIONING')
        
        # Test status change to active
        slice_obj.status = 'ACTIVE'
        slice_obj.save()
        self.assertEqual(slice_obj.status, 'ACTIVE')
    
    def test_slice_owner_assignment(self):
        """Test that slices are properly associated with owners"""
        slice1 = NetworkSlice.objects.create(
            name='User1 Slice',
            slice_type='IOT',
            bandwidth_mbps=5,
            latency_ms=100,
            duration_hours=1,
            owner=self.user
        )
        
        user2 = User.objects.create_user(
            username='testuser2',
            password='testpass123'
        )
        slice2 = NetworkSlice.objects.create(
            name='User2 Slice',
            slice_type='GAMING',
            bandwidth_mbps=100,
            latency_ms=10,
            duration_hours=3,
            owner=user2
        )
        
        # Test ownership
        self.assertEqual(slice1.owner, self.user)
        self.assertEqual(slice2.owner, user2)
        
        # Test related manager
        self.assertEqual(self.user.slices.count(), 1)
        self.assertEqual(user2.slices.count(), 1)


class APIAuthenticationTestCase(APITestCase):
    """Test cases for REST API authentication and authorization"""
    
    def setUp(self):
        self.superuser = User.objects.create_superuser(
            username='admin',
            password='testpass123'
        )
        self.guest_user = User.objects.create_user(
            username='guest',
            password='testpass123'
        )
        
        # Create slices for each user
        self.admin_slice = NetworkSlice.objects.create(
            name='Admin Slice',
            slice_type='CORP',
            bandwidth_mbps=100,
            latency_ms=5,
            duration_hours=24,
            owner=self.superuser
        )
        
        self.guest_slice = NetworkSlice.objects.create(
            name='Guest Slice',
            slice_type='GUEST',
            bandwidth_mbps=10,
            latency_ms=50,
            duration_hours=1,
            owner=self.guest_user
        )
    
    def test_unauthenticated_api_access(self):
        """Test that unauthenticated users cannot access API"""
        response = self.client.get('/api/slices/')
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)
    
    def test_guest_user_slice_filtering(self):
        """Test that guest users only see their own slices"""
        self.client.force_authenticate(user=self.guest_user)
        response = self.client.get('/api/slices/')
        
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        data = response.json()
        self.assertEqual(data['count'], 1)
        self.assertEqual(data['results'][0]['id'], str(self.guest_slice.id))
    
    def test_admin_user_sees_all_slices(self):
        """Test that admin users can see all slices"""
        self.client.force_authenticate(user=self.superuser)
        response = self.client.get('/api/slices/')
        
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        data = response.json()
        self.assertEqual(data['count'], 2)  # Both admin and guest slices
    
    def test_slice_creation_owner_assignment(self):
        """Test that created slices are assigned to the authenticated user"""
        self.client.force_authenticate(user=self.guest_user)
        slice_data = {
            'name': 'New Guest Slice',
            'slice_type': 'IOT',
            'bandwidth_mbps': 20,
            'latency_ms': 30,
            'duration_hours': 2
        }
        
        response = self.client.post('/api/slices/', slice_data)
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        
        # Verify owner assignment
        created_slice = NetworkSlice.objects.get(name='New Guest Slice')
        self.assertEqual(created_slice.owner, self.guest_user)


class DashboardAccessTestCase(TestCase):
    """Test cases for dashboard access control and content filtering"""
    
    def setUp(self):
        self.client = Client()
        self.admin = User.objects.create_user(
            username='admin',
            password='testpass123',
            is_staff=True
        )
        self.guest = User.objects.create_user(
            username='guest',
            password='testpass123'
        )
        
        # Create slices
        self.admin_slice = NetworkSlice.objects.create(
            name='Admin Slice',
            slice_type='CORP',
            bandwidth_mbps=100,
            latency_ms=5,
            duration_hours=8,
            owner=self.admin
        )
        
        self.guest_slice = NetworkSlice.objects.create(
            name='Guest Slice',
            slice_type='GUEST',
            bandwidth_mbps=25,
            latency_ms=40,
            duration_hours=2,
            owner=self.guest
        )
    
    def test_guest_dashboard_content(self):
        """Test that guest users only see their own slices and no stats cards"""
        self.client.login(username='guest', password='testpass123')
        response = self.client.get('/')
        
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Guest Slice')
        self.assertNotContains(response, 'Admin Slice')
        
        # Check that stats cards are not visible (admin only)
        self.assertNotContains(response, 'Total Slices')
        self.assertNotContains(response, 'Active Slices')
    
    def test_admin_dashboard_content(self):
        """Test that admin users see all slices and stats cards"""
        self.client.login(username='admin', password='testpass123')
        response = self.client.get('/')
        
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Admin Slice')
        self.assertContains(response, 'Guest Slice')
        
        # Check that stats cards are visible (admin only)
        self.assertContains(response, 'Total Slices')
        self.assertContains(response, 'Active Slices')


class PasswordChangeTestCase(TestCase):
    """Test cases for password change functionality"""
    
    def setUp(self):
        self.client = Client()
        self.user = User.objects.create_user(
            username='testuser',
            password='oldpass123'
        )
    
    def test_password_change_requires_login(self):
        """Test that password change requires authentication"""
        response = self.client.get('/password-change/')
        self.assertEqual(response.status_code, 302)
        self.assertIn('/login/', response.url)
    
    def test_password_change_form_display(self):
        """Test that password change form is displayed correctly"""
        self.client.login(username='testuser', password='oldpass123')
        response = self.client.get('/password-change/')
        
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Change Password')
        self.assertContains(response, 'Old password')
        self.assertContains(response, 'New password')


class NetworkSliceValidationTestCase(TestCase):
    """Test cases for network slice parameter validation"""
    
    def setUp(self):
        self.user = User.objects.create_user(
            username='testuser',
            password='testpass123'
        )
    
    def test_valid_slice_parameters(self):
        """Test that valid slice parameters are accepted"""
        valid_slice = NetworkSlice.objects.create(
            name='Valid Slice',
            slice_type='GAMING',
            bandwidth_mbps=50,
            latency_ms=15,
            duration_hours=4,
            owner=self.user
        )
        
        self.assertIsNotNone(valid_slice.id)
        self.assertEqual(valid_slice.slice_type, 'GAMING')
    
    def test_invalid_slice_type(self):
        """Test that invalid slice types are rejected"""
        with self.assertRaises(ValueError):
            NetworkSlice.objects.create(
                name='Invalid Slice',
                slice_type='INVALID',
                bandwidth_mbps=50,
                latency_ms=15,
                duration_hours=4,
                owner=self.user
            )
    
    def test_slice_name_uniqueness(self):
        """Test slice name requirements"""
        # Create first slice
        NetworkSlice.objects.create(
            name='Test Slice',
            slice_type='CORP',
            bandwidth_mbps=50,
            latency_ms=20,
            duration_hours=2,
            owner=self.user
        )
        
        # Should allow duplicate names for different users
        user2 = User.objects.create_user(
            username='user2',
            password='testpass123'
        )
        
        duplicate_name_slice = NetworkSlice.objects.create(
            name='Test Slice',  # Same name, different user
            slice_type='GUEST',
            bandwidth_mbps=25,
            latency_ms=30,
            duration_hours=1,
            owner=user2
        )
        
        self.assertIsNotNone(duplicate_name_slice.id)


if __name__ == '__main__':
    unittest.main()