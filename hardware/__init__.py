"""
Smart PUC — hardware abstraction layer
======================================

This package contains the hardware abstraction interfaces that the
Smart PUC OBD client uses when signing emission readings. It is the
hook point for future integration with real cryptographic hardware
(e.g. Microchip ATECC608A secure elements) while keeping the v3.2
software demonstration fully functional with a pure-Python stub.

Contents:
    - atecc608a_interface.py   Abstract base class + software stub
                                implementation of the ECC signing API
                                we need for the Smart PUC OBD device.

The stub is wire-compatible with the real ATECC608A driver in the
Microchip CryptoAuthLib, so code written against ``Atecc608AInterface``
today will work unchanged once real hardware is attached.
"""

from .atecc608a_interface import (
    Atecc608AInterface,
    SoftwareStubAtecc608A,
    get_default_secure_element,
)

__all__ = [
    "Atecc608AInterface",
    "SoftwareStubAtecc608A",
    "get_default_secure_element",
]
