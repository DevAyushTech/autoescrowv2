"""Isolated SIWE import.

abnf 2.8.3 is required by siwe 4.4.0 but emits grammar-redefinition warnings at
import time. Suppress them only while importing the library so application logs
remain clean and machine-readable.
"""

import warnings

with warnings.catch_warnings():
    warnings.simplefilter("ignore")
    from siwe import ISO8601Datetime, SiweMessage  # noqa: E402

__all__ = ["ISO8601Datetime", "SiweMessage"]
