#!/usr/bin/env python3
"""
IMS 2.0 - API Integration Testing
==================================

This script tests real API endpoints with actual HTTP requests.

Usage:
    python api_integration_test.py http://localhost:8000

Requirements:
    - Backend running at specified URL
    - curl or requests library
"""

import httpx
import json
import sys
from datetime import datetime, timedelta
import asyncio


class APIIntegrationTester:
    """Tests API endpoints end-to-end"""

    def __init__(self, base_url: str):
        self.base_url = base_url.rstrip("/")
        self.client = httpx.AsyncClient(timeout=10.0)
        self.auth_token = None
        self.test_results = []

    async def test_api_endpoint(self, method: str, endpoint: str, data: dict = None, expect_status: int = 200, name: str = ""):
        """Test a single API endpoint"""
        url = f"{self.base_url}{endpoint}"
        headers = {}

        if self.auth_token:
            headers["Authorization"] = f"Bearer {self.auth_token}"

        try:
            if method.upper() == "GET":
                response = await self.client.get(url, headers=headers)
            elif method.upper() == "POST":
                response = await self.client.post(url, json=data, headers=headers)
            elif method.upper() == "PUT":
                response = await self.client.put(url, json=data, headers=headers)
            elif method.upper() == "DELETE":
                response = await self.client.delete(url, headers=headers)
            else:
                raise ValueError(f"Unsupported method: {method}")

            success = response.status_code == expect_status
            status = "✅ PASS" if success else "❌ FAIL"

            result = {
                "name": name or f"{method} {endpoint}",
                "method": method,
                "endpoint": endpoint,
                "status_code": response.status_code,
                "expected_status": expect_status,
                "success": success,
                "timestamp": datetime.now().isoformat()
            }

            print(f"{status} | {method:6} {endpoint:40} | Status: {response.status_code}/{expect_status}")

            self.test_results.append(result)

            try:
                return response.json()
            except:
                return response.text

        except httpx.ConnectError:
            print(f"❌ CONNECTION ERROR | {method:6} {endpoint:40} | Cannot connect to {self.base_url}")
            self.test_results.append({
                "name": name,
                "status": "CONNECTION_ERROR",
                "url": url
            })
            return None
        except Exception as e:
            print(f"❌ ERROR | {method:6} {endpoint:40} | {str(e)}")
            self.test_results.append({
                "name": name,
                "status": "ERROR",
                "error": str(e)
            })
            return None

    async def run_full_test_suite(self):
        """Run complete API test suite"""
        print("\n" + "=" * 90)
        print("IMS 2.0 - API INTEGRATION TEST SUITE")
        print("=" * 90)
        print(f"Target: {self.base_url}\n")

        # ====================================================================
        # 1. HEALTH CHECK
        # ====================================================================
        print("\n📋 1. HEALTH CHECKS")
        print("-" * 90)

        await self.test_api_endpoint("GET", "/health", expect_status=200, name="Health Check")
        await self.test_api_endpoint("GET", "/docs", expect_status=200, name="Swagger UI")

        # ====================================================================
        # 2. AUTHENTICATION
        # ====================================================================
        print("\n📋 2. AUTHENTICATION FLOW")
        print("-" * 90)

        login_response = await self.test_api_endpoint(
            "POST", "/api/v1/auth/login",
            data={"username": "admin", "password": "admin123"},
            expect_status=200,
            name="Login"
        )

        if login_response and isinstance(login_response, dict) and "access_token" in login_response:
            self.auth_token = login_response["access_token"]
            print(f"   → Token obtained: {self.auth_token[:20]}...")
        else:
            print("   ⚠️  Failed to obtain auth token. Continuing with public endpoints...")

        # ====================================================================
        # 3. STORE OPERATIONS
        # ====================================================================
        print("\n📋 3. STORE MANAGEMENT")
        print("-" * 90)

        stores = await self.test_api_endpoint(
            "GET", "/api/v1/stores/",
            expect_status=200,
            name="List Stores"
        )

        if stores and isinstance(stores, dict):
            num_stores = len(stores.get("data", []))
            print(f"   → Found {num_stores} stores")

        # ====================================================================
        # 4. CUSTOMER OPERATIONS
        # ====================================================================
        print("\n📋 4. CUSTOMER MANAGEMENT")
        print("-" * 90)

        customers = await self.test_api_endpoint(
            "GET", "/api/v1/customers/",
            expect_status=200,
            name="List Customers"
        )

        if customers:
            print(f"   → Sample customer data retrieved")

        # Create new customer
        new_customer = {
            "name": f"Test Customer {datetime.now().timestamp()}",
            "email": f"test{int(datetime.now().timestamp())}@example.com",
            "phone": "9876543210",
            "store_id": "BV-DEL"
        }

        customer_response = await self.test_api_endpoint(
            "POST", "/api/v1/customers/",
            data=new_customer,
            expect_status=200,
            name="Create Customer"
        )

        # ====================================================================
        # 5. PRODUCT OPERATIONS
        # ====================================================================
        print("\n📋 5. PRODUCT MANAGEMENT")
        print("-" * 90)

        products = await self.test_api_endpoint(
            "GET", "/api/v1/products/",
            expect_status=200,
            name="List Products"
        )

        if products:
            print(f"   → Product catalog retrieved")

        # ====================================================================
        # 6. INVENTORY OPERATIONS
        # ====================================================================
        print("\n📋 6. INVENTORY MANAGEMENT")
        print("-" * 90)

        inventory = await self.test_api_endpoint(
            "GET", "/api/v1/inventory/",
            expect_status=200,
            name="List Inventory"
        )

        if inventory:
            print(f"   → Inventory levels retrieved")

        # ====================================================================
        # 7. ORDER OPERATIONS
        # ====================================================================
        print("\n📋 7. ORDER MANAGEMENT")
        print("-" * 90)

        orders = await self.test_api_endpoint(
            "GET", "/api/v1/orders/",
            expect_status=200,
            name="List Orders"
        )

        if orders:
            print(f"   → Orders retrieved from database")

        # Create new order
        new_order = {
            "customer_id": "CUST-0001",
            "store_id": "BV-DEL",
            "items": [
                {"product_id": "FRAME-01-01", "quantity": 1, "price": 5000}
            ],
            "subtotal": 5000,
            "tax": 900,
            "total": 5900,
            "payment_method": "CARD"
        }

        order_response = await self.test_api_endpoint(
            "POST", "/api/v1/orders/",
            data=new_order,
            expect_status=200,
            name="Create Order"
        )

        # ====================================================================
        # 8. PRESCRIPTION OPERATIONS
        # ====================================================================
        print("\n📋 8. PRESCRIPTION MANAGEMENT")
        print("-" * 90)

        prescriptions = await self.test_api_endpoint(
            "GET", "/api/v1/prescriptions/",
            expect_status=200,
            name="List Prescriptions"
        )

        if prescriptions:
            print(f"   → Prescriptions retrieved")

        # ====================================================================
        # 9. REPORTS & ANALYTICS
        # ====================================================================
        print("\n📋 9. REPORTS & ANALYTICS")
        print("-" * 90)

        dashboard = await self.test_api_endpoint(
            "GET", "/api/v1/reports/dashboard",
            expect_status=200,
            name="Dashboard KPIs"
        )

        if dashboard:
            print(f"   → Dashboard metrics calculated")

        sales_report = await self.test_api_endpoint(
            "GET", "/api/v1/reports/sales",
            expect_status=200,
            name="Sales Report"
        )

        inventory_report = await self.test_api_endpoint(
            "GET", "/api/v1/reports/inventory",
            expect_status=200,
            name="Inventory Report"
        )

        # ====================================================================
        # PRINT RESULTS SUMMARY
        # ====================================================================
        self.print_summary()

    def print_summary(self):
        """Print test results summary"""
        print("\n" + "=" * 90)
        print("TEST RESULTS SUMMARY")
        print("=" * 90)

        passed = sum(1 for r in self.test_results if r.get("success") == True)
        failed = sum(1 for r in self.test_results if r.get("success") == False)
        errors = sum(1 for r in self.test_results if r.get("status") == "ERROR")
        total = len([r for r in self.test_results if "success" in r])

        print(f"\nTotal Tests: {total}")
        print(f"Passed: {passed} ✅")
        print(f"Failed: {failed} ❌")
        print(f"Errors: {errors} ⚠️")

        if total > 0:
            pass_rate = (passed / total * 100)
            print(f"Pass Rate: {pass_rate:.1f}%")

        print("\n" + "=" * 90)
        print("ENDPOINTS TESTED (BY CATEGORY)")
        print("=" * 90)

        categories = {}
        for result in self.test_results:
            if "endpoint" in result:
                cat = result["endpoint"].split("/")[3] if len(result["endpoint"].split("/")) > 3 else "misc"
                if cat not in categories:
                    categories[cat] = {"passed": 0, "failed": 0}
                if result.get("success"):
                    categories[cat]["passed"] += 1
                else:
                    categories[cat]["failed"] += 1

        for cat, stats in sorted(categories.items()):
            total_cat = stats["passed"] + stats["failed"]
            status = "✅" if stats["failed"] == 0 else "⚠️"
            print(f"{status} {cat.upper():20} | Passed: {stats['passed']:2}/{total_cat}")

        print("\n" + "=" * 90)

        if passed == total:
            print("🎉 ALL TESTS PASSED! API is fully functional!")
        else:
            print(f"⚠️  {failed} test(s) failed. Review logs above.")

        print("=" * 90 + "\n")

    async def close(self):
        """Close HTTP client"""
        await self.client.aclose()


async def main():
    """Main entry point"""
    if len(sys.argv) < 2:
        print("Usage: python api_integration_test.py <base_url>")
        print("Example: python api_integration_test.py http://localhost:8000")
        sys.exit(1)

    base_url = sys.argv[1]

    tester = APIIntegrationTester(base_url)

    try:
        await tester.run_full_test_suite()
    finally:
        await tester.close()


if __name__ == "__main__":
    asyncio.run(main())
