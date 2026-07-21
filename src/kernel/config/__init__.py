"""Configuration management using pydantic-settings.

This module provides centralized configuration management for the application.
"""

from .base import (
    HarnessMode,
    Settings,
    get_active_harness_mode,
    get_settings,
    normalize_harness_mode,
    settings,
)
from .constants import (
    JWT_SECRET_KEY_MIN_LENGTH,
    RESTART_REQUIRED_SETTINGS,
    SENSITIVE_SETTINGS,
)
from .definitions import SETTING_DEFINITIONS
from .service import initialize_settings, refresh_settings
from .utils import get_default_from_settings

# Alias for backward compatibility
_get_default_from_settings = get_default_from_settings

__all__ = [
    # Settings class and instance
    "Settings",
    "get_settings",
    "settings",
    "HarnessMode",
    "get_active_harness_mode",
    "normalize_harness_mode",
    # Definitions
    "SETTING_DEFINITIONS",
    # Constants
    "JWT_SECRET_KEY_MIN_LENGTH",
    "RESTART_REQUIRED_SETTINGS",
    "SENSITIVE_SETTINGS",
    # Service functions
    "initialize_settings",
    "refresh_settings",
    # Utility functions
    "get_default_from_settings",
    "_get_default_from_settings",
]
