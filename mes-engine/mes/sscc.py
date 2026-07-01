"""SSCC (Serial Shipping Container Code) builder + validator.

GS1 SSCC is 18 digits: [extension digit:1][GS1 company prefix + serial:16][check digit:1].
The check digit uses the GS1 mod-10 algorithm (a Luhn-style weighted sum where,
scanning right-to-left over the first 17 digits, positions alternate weight 3,1,3,1...).

DairyWorks uses placeholder prefix "80" (from the factory model). Format + check
digit are public GS1 standards; the prefix is a placeholder, not an assigned range.
"""

from __future__ import annotations

SSCC_LENGTH = 18


def gs1_check_digit(body: str) -> int:
    """GS1 mod-10 check digit for a numeric string (without the check digit).

    Rightmost digit of `body` gets weight 3, then alternating 1,3,1,3...
    Check digit = (10 - (weighted_sum mod 10)) mod 10.
    """
    if not body.isdigit():
        raise ValueError(f"non-numeric SSCC body: {body!r}")
    total = 0
    # Weight 3 on the rightmost body digit, alternating outward.
    for i, ch in enumerate(reversed(body)):
        weight = 3 if i % 2 == 0 else 1
        total += int(ch) * weight
    return (10 - (total % 10)) % 10


def build_sscc(prefix: str, serial, extension: str = "0") -> str:
    """Build an 18-digit SSCC with a valid GS1 check digit.

    prefix    : GS1 company prefix (placeholder "80" for DairyWorks).
    serial    : serial reference (int or str); zero-padded to fill 17 digits.
    extension : single extension digit (default "0").

    Layout: [ext:1][prefix + serial padded to 16][check:1] = 18 digits.
    """
    ext = str(extension)
    if len(ext) != 1 or not ext.isdigit():
        raise ValueError(f"extension must be a single digit, got {extension!r}")
    prefix = str(prefix)
    if not prefix.isdigit():
        raise ValueError(f"prefix must be numeric, got {prefix!r}")

    serial_s = str(serial)
    if not serial_s.isdigit():
        raise ValueError(f"serial must be numeric, got {serial!r}")

    # ext(1) + middle(16) + check(1) = 18 -> middle must be exactly 16 digits.
    middle_raw = prefix + serial_s
    if len(middle_raw) > 16:
        raise ValueError(
            f"prefix+serial too long ({len(middle_raw)} digits, max 16): {middle_raw}"
        )
    middle = middle_raw.rjust(16, "0")

    body = ext + middle  # 17 digits
    check = gs1_check_digit(body)
    return body + str(check)


def validate_sscc(sscc: str) -> bool:
    """True if sscc is 18 numeric digits with a correct GS1 check digit."""
    if not isinstance(sscc, str):
        return False
    if len(sscc) != SSCC_LENGTH or not sscc.isdigit():
        return False
    body, check = sscc[:-1], int(sscc[-1])
    return gs1_check_digit(body) == check
