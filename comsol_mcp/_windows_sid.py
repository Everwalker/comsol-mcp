"""Bounded revision-1 SID text grammar, independent of identity or authority.

MS-DTYP 2.4.2.1 uses one to fifteen uint32 subauthorities. Identifier
authorities below 2**32 are decimal; larger 48-bit authorities use 12 hex
digits. Recognizing this syntax never proves a principal exists or is trusted.
"""
import re

_DECIMAL = re.compile(r"(?:0|[1-9][0-9]{0,9})\Z")
_HEX_AUTHORITY = re.compile(r"0x[0-9a-f]{12}\Z", re.IGNORECASE)


def normalize_scm_account_name(account: str) -> str:
    """Translate only SCM's documented LocalSystem labels before real lookup.

    This returns a name, never an identity or fallback for a failed Win32 call.
    Preserve other accounts exactly, including whitespace and Unicode names.
    """
    if isinstance(account, str) and account.isascii() and account.lower() in {"localsystem", ".\\localsystem"}:
        return r"NT AUTHORITY\SYSTEM"
    return account


def is_canonical_sid(value: object) -> bool:
    if not isinstance(value, str) or not 7 <= len(value) <= 183:
        return False
    parts = value.split("-")
    if not 4 <= len(parts) <= 18 or parts[0] not in {"S", "s"} or parts[1] != "1":
        return False
    authority = parts[2]
    if _DECIMAL.fullmatch(authority):
        if int(authority) >= 2**32:
            return False
    elif _HEX_AUTHORITY.fullmatch(authority):
        if int(authority, 16) < 2**32:
            return False
    else:
        return False
    return all(_DECIMAL.fullmatch(part) and int(part) < 2**32 for part in parts[3:])
