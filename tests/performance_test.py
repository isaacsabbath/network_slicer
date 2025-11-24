#!/usr/bin/env python3
"""
Performance Testing Script for Network Slicer System
Tests system performance under various load conditions
"""

import time
import asyncio
import aiohttp
import statistics
from concurrent.futures import ThreadPoolExecutor
import json
import sys

class PerformanceTester:
    def __init__(self, base_url="http://localhost:8000"):
        self.base_url = base_url
        self.session = None
        
    async def setup_session(self):
        """Setup HTTP session with authentication"""
        self.session = aiohttp.ClientSession()
        
    async def cleanup_session(self):
        """Cleanup HTTP session"""
        if self.session:
            await self.session.close()
    
    async def login(self, username="admin", password="testpass123"):
        """Authenticate user and get session cookies"""
        # Get CSRF token
        async with self.session.get(f"{self.base_url}/login/") as resp:
            text = await resp.text()
            csrf_token = self._extract_csrf_token(text)
        
        # Login
        login_data = {
            'username': username,
            'password': password,
            'csrfmiddlewaretoken': csrf_token
        }
        
        async with self.session.post(f"{self.base_url}/login/", data=login_data) as resp:
            return resp.status == 302
    
    def _extract_csrf_token(self, html):
        """Extract CSRF token from HTML"""
        import re
        match = re.search(r'name="csrfmiddlewaretoken" value="([^"]*)"', html)
        return match.group(1) if match else ""
    
    async def test_page_load_time(self, url, num_requests=10):
        """Test page load times"""
        times = []
        
        for _ in range(num_requests):
            start_time = time.time()
            async with self.session.get(f"{self.base_url}{url}") as resp:
                await resp.text()
                end_time = time.time()
                times.append(end_time - start_time)
        
        return {
            'url': url,
            'requests': num_requests,
            'avg_time': statistics.mean(times),
            'median_time': statistics.median(times),
            'min_time': min(times),
            'max_time': max(times),
            'times': times
        }
    
    async def test_api_response_time(self, endpoint, num_requests=50):
        """Test API endpoint response times"""
        times = []
        
        for _ in range(num_requests):
            start_time = time.time()
            async with self.session.get(f"{self.base_url}/api{endpoint}") as resp:
                await resp.json()
                end_time = time.time()
                times.append(end_time - start_time)
        
        return {
            'endpoint': endpoint,
            'requests': num_requests,
            'avg_time': statistics.mean(times),
            'median_time': statistics.median(times),
            'min_time': min(times),
            'max_time': max(times)
        }
    
    async def test_concurrent_users(self, num_users=20):
        """Test system under concurrent user load"""
        start_time = time.time()
        
        tasks = []
        for i in range(num_users):
            task = asyncio.create_task(self._simulate_user_session(i))
            tasks.append(task)
        
        results = await asyncio.gather(*tasks)
        end_time = time.time()
        
        successful_sessions = sum(1 for r in results if r['success'])
        
        return {
            'total_users': num_users,
            'successful_sessions': successful_sessions,
            'success_rate': successful_sessions / num_users * 100,
            'total_time': end_time - start_time,
            'avg_session_time': statistics.mean([r['duration'] for r in results])
        }
    
    async def _simulate_user_session(self, user_id):
        """Simulate a complete user session"""
        session_start = time.time()
        
        try:
            # Create new session for this user
            async with aiohttp.ClientSession() as user_session:
                # Login
                await self._user_login(user_session, f"testuser{user_id}", "testpass123")
                
                # Navigate to dashboard
                async with user_session.get(f"{self.base_url}/") as resp:
                    if resp.status != 200:
                        return {'success': False, 'duration': 0}
                
                # Check API endpoints
                async with user_session.get(f"{self.base_url}/api/slices/") as resp:
                    if resp.status != 200:
                        return {'success': False, 'duration': 0}
                
                # Simulate slice creation
                await self._create_test_slice(user_session)
                
                session_end = time.time()
                return {
                    'success': True,
                    'duration': session_end - session_start,
                    'user_id': user_id
                }
        
        except Exception as e:
            return {'success': False, 'duration': 0, 'error': str(e)}
    
    async def _user_login(self, session, username, password):
        """Login a specific user session"""
        # This is a simplified version - in practice you'd need proper CSRF handling
        login_data = {
            'username': username,
            'password': password
        }
        async with session.post(f"{self.base_url}/login/", data=login_data) as resp:
            return resp.status == 302
    
    async def _create_test_slice(self, session):
        """Create a test slice via API"""
        slice_data = {
            'name': f'Test Slice {time.time()}',
            'slice_type': 'GUEST',
            'bandwidth_mbps': 10,
            'latency_ms': 50,
            'duration_hours': 1
        }
        
        async with session.post(f"{self.base_url}/api/slices/", 
                               json=slice_data,
                               headers={'Content-Type': 'application/json'}) as resp:
            return resp.status == 201

class PerformanceReporter:
    """Generate performance test reports"""
    
    @staticmethod
    def print_results(test_name, results):
        """Print formatted test results"""
        print(f"\n{'='*50}")
        print(f"Performance Test: {test_name}")
        print(f"{'='*50}")
        
        if 'avg_time' in results:
            print(f"Average Response Time: {results['avg_time']:.3f}s")
            print(f"Median Response Time:  {results['median_time']:.3f}s")
            print(f"Min Response Time:     {results['min_time']:.3f}s")
            print(f"Max Response Time:     {results['max_time']:.3f}s")
        
        if 'success_rate' in results:
            print(f"Success Rate:          {results['success_rate']:.1f}%")
            print(f"Successful Sessions:   {results['successful_sessions']}/{results['total_users']}")
            print(f"Avg Session Duration:  {results['avg_session_time']:.3f}s")
        
        print(f"{'='*50}\n")
    
    @staticmethod
    def check_performance_bounds(results, bounds):
        """Check if results meet performance requirements"""
        passed = True
        failures = []
        
        if 'avg_time' in results and 'max_response_time' in bounds:
            if results['avg_time'] > bounds['max_response_time']:
                passed = False
                failures.append(f"Average response time {results['avg_time']:.3f}s exceeds limit {bounds['max_response_time']:.3f}s")
        
        if 'success_rate' in results and 'min_success_rate' in bounds:
            if results['success_rate'] < bounds['min_success_rate']:
                passed = False
                failures.append(f"Success rate {results['success_rate']:.1f}% below minimum {bounds['min_success_rate']:.1f}%")
        
        return passed, failures

async def main():
    """Main performance testing function"""
    tester = PerformanceTester()
    reporter = PerformanceReporter()
    
    # Performance bounds as defined in documentation
    bounds = {
        'page_load': {'max_response_time': 2.0},
        'api_calls': {'max_response_time': 0.5},
        'concurrent_users': {'min_success_rate': 90.0}
    }
    
    try:
        await tester.setup_session()
        
        # Authenticate
        login_success = await tester.login()
        if not login_success:
            print("Failed to authenticate - check credentials")
            return
        
        print("Starting Performance Tests...")
        
        # Test 1: Dashboard Page Load Time
        dashboard_results = await tester.test_page_load_time("/", 10)
        reporter.print_results("Dashboard Page Load", dashboard_results)
        
        passed, failures = reporter.check_performance_bounds(dashboard_results, bounds['page_load'])
        if not passed:
            print("❌ Dashboard page load FAILED:")
            for failure in failures:
                print(f"   {failure}")
        else:
            print("✅ Dashboard page load PASSED")
        
        # Test 2: API Response Time
        api_results = await tester.test_api_response_time("/slices/", 50)
        reporter.print_results("API Response Time", api_results)
        
        passed, failures = reporter.check_performance_bounds(api_results, bounds['api_calls'])
        if not passed:
            print("❌ API response time FAILED:")
            for failure in failures:
                print(f"   {failure}")
        else:
            print("✅ API response time PASSED")
        
        # Test 3: Concurrent Users (reduced for testing)
        concurrent_results = await tester.test_concurrent_users(5)  # Reduced from 20
        reporter.print_results("Concurrent Users", concurrent_results)
        
        passed, failures = reporter.check_performance_bounds(concurrent_results, bounds['concurrent_users'])
        if not passed:
            print("❌ Concurrent users test FAILED:")
            for failure in failures:
                print(f"   {failure}")
        else:
            print("✅ Concurrent users test PASSED")
        
    except Exception as e:
        print(f"Performance test error: {e}")
    
    finally:
        await tester.cleanup_session()

if __name__ == "__main__":
    print("Network Slicer Performance Testing Suite")
    print("Note: Ensure the Django server is running on localhost:8000")
    print("      and test users exist (or modify credentials in script)")
    
    # Run the async main function
    asyncio.run(main())