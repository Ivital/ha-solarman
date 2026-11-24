"""Enforce a read-only invariant independently from Home Assistant."""
from __future__ import annotations

ALLOWED_READ_FUNCTIONS = frozenset((0x03, 0x04))

def require_read_function(function_code: int) -> None:
    """Block all Modbus functions except 03 (holding) and 04 (input)."""
    if (isinstance(function_code, bool)
            or not isinstance(function_code, int)
            or function_code not in ALLOWED_READ_FUNCTIONS):
        raise PermissionError(f"Blocked Modbus function {function_code!r}: monitoring is read-only")
