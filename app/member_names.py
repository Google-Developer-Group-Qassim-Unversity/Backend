"""Official, public, and wallet names are independent member preferences."""

import unicodedata


def initial_public_name(full_name: str) -> str:
    """Match the club's first/family-name format, including compound Abdullah names."""
    parts = full_name.split()
    if not parts:
        return "Member"
    if len(parts) <= 2:
        return " ".join(parts)
    family_name = parts[-2:] if parts[-1] == "الله" else parts[-1:]
    return " ".join([parts[0], *family_name])


def default_public_name(context) -> str:
    """SQLAlchemy insert default also covers imports and direct ORM creation."""
    return initial_public_name(context.get_current_parameters()["name"])


def validate_member_name(value: str, *, max_length: int) -> str:
    if not isinstance(value, str):
        raise ValueError("Name must be a string")
    value = value.strip()
    if not value or len(value) > max_length:
        raise ValueError(f"Name must contain between 1 and {max_length} characters")
    if any(unicodedata.category(char) in {"Cc", "Cs"} for char in value):
        raise ValueError("Name must not contain control characters")
    return value
