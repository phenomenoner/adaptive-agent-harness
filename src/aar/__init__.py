"""Adaptive Agent Runtime public package."""

from aar._windows_openssl import preload_windows_openssl
from aar.versions import PACKAGE_VERSION

preload_windows_openssl()

__all__ = ["PACKAGE_VERSION"]
