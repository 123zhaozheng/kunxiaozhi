"""Cryptographic utilities for OA SSO and other integrations.

Currently provides SM2 encryption compatible with Java BouncyCastle (C1C2C3 format)
for enterprise OA portal single sign-on.
"""

from .sm2_utils import SM2Utils

__all__ = ["SM2Utils"]
