"""
SmartPUC -- Unit tests for DTC reading functions in integrations/obd_adapter.py.

Tests:
  - decode_dtc_bytes() with known byte sequences
  - classify_dtcs() with emission-related and non-emission codes
  - dtc_to_degradation_type() mapping
"""

from __future__ import annotations

import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from integrations.obd_adapter import (  # noqa: E402
    decode_dtc_bytes,
    classify_dtcs,
    dtc_to_degradation_type,
    DTC_EMISSION_CODES,
)


class TestDecodeDtcBytes:
    """Tests for decode_dtc_bytes()."""

    def test_p0420_catalyst(self):
        # P0420: type=P (00), digit2=0, digit3=4, digit4=2, digit5=0
        # Byte 1: 00|00|0100 = 0x04
        # Byte 2: 0010|0000 = 0x20
        result = decode_dtc_bytes([0x04, 0x20])
        assert result == ["P0420"]

    def test_p0171_fuel_lean(self):
        # P0171: type=P (00), digit2=0, digit3=1, digit4=7, digit5=1
        # Byte 1: 00|00|0001 = 0x01
        # Byte 2: 0111|0001 = 0x71
        result = decode_dtc_bytes([0x01, 0x71])
        assert result == ["P0171"]

    def test_p0300_misfire(self):
        # P0300: type=P (00), digit2=0, digit3=3, digit4=0, digit5=0
        # Byte 1: 00|00|0011 = 0x03
        # Byte 2: 0000|0000 = 0x00
        result = decode_dtc_bytes([0x03, 0x00])
        assert result == ["P0300"]

    def test_multiple_dtcs(self):
        # P0420 + P0171
        result = decode_dtc_bytes([0x04, 0x20, 0x01, 0x71])
        assert len(result) == 2
        assert "P0420" in result
        assert "P0171" in result

    def test_null_dtc_skipped(self):
        result = decode_dtc_bytes([0x00, 0x00])
        assert result == []

    def test_null_in_middle_skipped(self):
        result = decode_dtc_bytes([0x04, 0x20, 0x00, 0x00, 0x01, 0x71])
        assert len(result) == 2

    def test_empty_input(self):
        result = decode_dtc_bytes([])
        assert result == []

    def test_single_byte_input(self):
        result = decode_dtc_bytes([0x04])
        assert result == []

    def test_c_type_dtc(self):
        # C-type DTC: type=C (01), bits 7-6 = 01
        # Byte 1: 01|00|0001 = 0x41
        # Byte 2: 0010|0011 = 0x23
        result = decode_dtc_bytes([0x41, 0x23])
        assert len(result) == 1
        assert result[0].startswith("C")

    def test_b_type_dtc(self):
        # B-type DTC: type=B (10), bits 7-6 = 10
        # Byte 1: 10|00|0001 = 0x81
        # Byte 2: 0010|0011 = 0x23
        result = decode_dtc_bytes([0x81, 0x23])
        assert len(result) == 1
        assert result[0].startswith("B")

    def test_u_type_dtc(self):
        # U-type DTC: type=U (11), bits 7-6 = 11
        # Byte 1: 11|00|0001 = 0xC1
        # Byte 2: 0010|0011 = 0x23
        result = decode_dtc_bytes([0xC1, 0x23])
        assert len(result) == 1
        assert result[0].startswith("U")


class TestClassifyDtcs:
    """Tests for classify_dtcs()."""

    def test_emission_related_code(self):
        result = classify_dtcs(["P0420"])
        assert len(result["emission_related"]) == 1
        assert result["emission_related"][0]["code"] == "P0420"
        assert result["emission_related"][0]["system"] == "catalyst"
        assert result["emission_related"][0]["severity"] == "high"
        assert result["highest_severity"] == "high"
        assert result["degradation_signal"] is True
        assert len(result["other"]) == 0

    def test_non_emission_code(self):
        result = classify_dtcs(["P0442"])
        assert len(result["emission_related"]) == 0
        assert "P0442" in result["other"]
        assert result["highest_severity"] == "none"
        assert result["degradation_signal"] is False

    def test_mixed_codes(self):
        result = classify_dtcs(["P0420", "P0442", "P0171"])
        assert len(result["emission_related"]) == 2
        assert len(result["other"]) == 1
        assert result["highest_severity"] == "high"
        assert result["degradation_signal"] is True

    def test_medium_severity_no_degradation_signal(self):
        result = classify_dtcs(["P0171"])
        assert result["highest_severity"] == "medium"
        assert result["degradation_signal"] is False

    def test_empty_codes(self):
        result = classify_dtcs([])
        assert result["emission_related"] == []
        assert result["other"] == []
        assert result["highest_severity"] == "none"
        assert result["degradation_signal"] is False

    def test_all_systems_covered(self):
        """Verify all known emission DTC codes are classifiable."""
        all_codes = list(DTC_EMISSION_CODES.keys())
        result = classify_dtcs(all_codes)
        assert len(result["emission_related"]) == len(all_codes)
        assert len(result["other"]) == 0


class TestDtcToDegradationType:
    """Tests for dtc_to_degradation_type()."""

    def test_catalyst_codes(self):
        assert dtc_to_degradation_type(["P0420"]) == "catalyst_aging"
        assert dtc_to_degradation_type(["P0421"]) == "catalyst_aging"
        assert dtc_to_degradation_type(["P0430"]) == "catalyst_aging"

    def test_egr_codes(self):
        assert dtc_to_degradation_type(["P0401"]) == "egr_failure"
        assert dtc_to_degradation_type(["P0402"]) == "egr_failure"

    def test_o2_sensor_codes(self):
        assert dtc_to_degradation_type(["P0130"]) == "o2_sensor_drift"
        assert dtc_to_degradation_type(["P0131"]) == "o2_sensor_drift"

    def test_ignition_codes(self):
        assert dtc_to_degradation_type(["P0300"]) == "injector_fouling"
        assert dtc_to_degradation_type(["P0301"]) == "injector_fouling"

    def test_dpf_codes(self):
        assert dtc_to_degradation_type(["P2463"]) == "dpf_removal_diesel"
        assert dtc_to_degradation_type(["P244A"]) == "dpf_removal_diesel"

    def test_no_match(self):
        assert dtc_to_degradation_type(["P0442"]) is None
        assert dtc_to_degradation_type([]) is None

    def test_first_match_wins(self):
        # When multiple DTCs present, returns the first matching type
        result = dtc_to_degradation_type(["P0420", "P0401"])
        assert result == "catalyst_aging"
