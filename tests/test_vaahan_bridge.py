"""
Tests for the VAHAN 4.0 Vehicle Registration Database Bridge.

Covers MockVaahanService lookups, VaahanBridge verification with valid and
invalid formats, and emission-test eligibility checks across different
BS norms, registration statuses, and edge cases.
"""

import sys
import os
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "integrations"))

from integrations.vaahan_bridge import MockVaahanService, VaahanBridge


class TestMockVaahanService(unittest.TestCase):
    """Tests for the MockVaahanService lookup."""

    def setUp(self):
        """Create a MockVaahanService instance."""
        self.service = MockVaahanService()

    def test_lookup_existing_vehicle(self):
        """Lookup for MH12AB1234 should return a dict with correct owner and details."""
        result = self.service.lookup("MH12AB1234")
        self.assertIsNotNone(result)
        self.assertEqual(result["owner_name"], "Rajesh Kumar")
        self.assertEqual(result["fuel_type"], "Petrol")
        self.assertEqual(result["bs_norm"], "BS-VI")
        self.assertEqual(result["manufacturer"], "Maruti Suzuki")
        self.assertEqual(result["model"], "Swift")

    def test_lookup_nonexistent_vehicle(self):
        """Lookup for a vehicle not in the catalogue should return None."""
        result = self.service.lookup("XX99ZZ9999")
        self.assertIsNone(result)

    def test_lookup_case_insensitive(self):
        """Lookup should be case-insensitive (normalizes to uppercase)."""
        result = self.service.lookup("mh12ab1234")
        self.assertIsNotNone(result)
        self.assertEqual(result["owner_name"], "Rajesh Kumar")

    def test_lookup_with_spaces(self):
        """Lookup should strip spaces from the registration number."""
        result = self.service.lookup("MH 12 AB 1234")
        self.assertIsNotNone(result)
        self.assertEqual(result["owner_name"], "Rajesh Kumar")


class TestVaahanBridgeVerifyVehicle(unittest.TestCase):
    """Tests for VaahanBridge.verify_vehicle() using mock mode."""

    def setUp(self):
        """Create a VaahanBridge in mock mode."""
        self.bridge = VaahanBridge(use_mock=True)

    def test_valid_registration(self):
        """verify_vehicle() for a known vehicle should return valid=True with details."""
        result = self.bridge.verify_vehicle("MH12AB1234")
        self.assertTrue(result["valid"])
        self.assertEqual(result["registration_number"], "MH12AB1234")
        self.assertEqual(result["fuel_type"], "Petrol")
        self.assertEqual(result["bs_norm"], "BS-VI")
        self.assertIsNone(result["error"])

    def test_invalid_format(self):
        """verify_vehicle() with an invalid format should return valid=False with error."""
        result = self.bridge.verify_vehicle("123INVALID")
        self.assertFalse(result["valid"])
        self.assertIn("Invalid registration number format", result["error"])

    def test_vehicle_not_found(self):
        """verify_vehicle() for an unknown but validly formatted registration should return not found."""
        result = self.bridge.verify_vehicle("MH99ZZ9999")
        self.assertFalse(result["valid"])
        self.assertIn("not found", result["error"])

    def test_chassis_and_engine_masked(self):
        """Chassis and engine numbers should be masked in the result."""
        result = self.bridge.verify_vehicle("MH12AB1234")
        self.assertTrue(result["valid"])
        # Masked values should contain asterisks
        self.assertIn("*", result["chassis_number"])
        self.assertIn("*", result["engine_number"])


class TestVaahanBridgeValidateForEmissionTest(unittest.TestCase):
    """Tests for VaahanBridge.validate_for_emission_test() eligibility logic."""

    def setUp(self):
        """Create a VaahanBridge in mock mode."""
        self.bridge = VaahanBridge(use_mock=True)

    def test_eligible_bsvi_active(self):
        """BS-VI vehicle with Active status should be eligible."""
        result = self.bridge.validate_for_emission_test("MH12AB1234")
        self.assertTrue(result["eligible"])
        self.assertIn("eligible", result["reason"])
        self.assertIn("BS-VI", result["reason"])
        self.assertTrue(result["vehicle_info"]["valid"])

    def test_eligible_bsiv_active(self):
        """BS-IV vehicle with Active status should also be eligible (minimum norm)."""
        result = self.bridge.validate_for_emission_test("KA01EF9012")
        self.assertTrue(result["eligible"])
        self.assertIn("eligible", result["reason"])

    def test_ineligible_bsiii(self):
        """BS-III vehicle should be ineligible (below minimum BS-IV)."""
        result = self.bridge.validate_for_emission_test("TN01GH3456")
        self.assertFalse(result["eligible"])
        self.assertIn("BS-III", result["reason"])
        self.assertIn("below", result["reason"].lower())

    def test_ineligible_bsii(self):
        """BS-II vehicle should be ineligible."""
        result = self.bridge.validate_for_emission_test("KA05QR3344")
        self.assertFalse(result["eligible"])
        self.assertIn("BS-II", result["reason"])

    def test_expired_registration(self):
        """Vehicle with Expired registration status should be ineligible."""
        result = self.bridge.validate_for_emission_test("UP16MN6789")
        self.assertFalse(result["eligible"])
        self.assertIn("Expired", result["reason"])

    def test_suspended_registration(self):
        """Vehicle with Suspended registration status should be ineligible."""
        result = self.bridge.validate_for_emission_test("MH01OP1122")
        self.assertFalse(result["eligible"])
        self.assertIn("Suspended", result["reason"])

    def test_nonexistent_vehicle(self):
        """Non-existent vehicle should be ineligible with appropriate reason."""
        result = self.bridge.validate_for_emission_test("MH99ZZ9999")
        self.assertFalse(result["eligible"])
        self.assertIn("not found", result["reason"])
        self.assertFalse(result["vehicle_info"]["valid"])

    def test_invalid_format_ineligible(self):
        """Invalid registration format should result in ineligible."""
        result = self.bridge.validate_for_emission_test("BADFORMAT")
        self.assertFalse(result["eligible"])
        self.assertIn("Invalid", result["reason"])


if __name__ == "__main__":
    unittest.main()
