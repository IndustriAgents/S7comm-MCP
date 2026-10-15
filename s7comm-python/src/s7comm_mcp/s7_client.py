"""Compatibility import; implementation lives in adapters.s7_client."""

import sys

from .adapters import s7_client as _implementation

sys.modules[__name__] = _implementation
