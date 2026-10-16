from enum import StrEnum


class Role(StrEnum):
    MEMBER = "member"
    MANAGER = "manager"
    ADMIN = "admin"


class Classification(StrEnum):
    PUBLIC = "public"
    INTERNAL = "internal"
    CONFIDENTIAL = "confidential"
    RESTRICTED = "restricted"


_RANK = {
    Classification.PUBLIC: 0,
    Classification.INTERNAL: 1,
    Classification.CONFIDENTIAL: 2,
    Classification.RESTRICTED: 3,
}

CLEARANCE: dict[Role, Classification] = {
    Role.MEMBER: Classification.INTERNAL,
    Role.MANAGER: Classification.CONFIDENTIAL,
    Role.ADMIN: Classification.RESTRICTED,
}


def within_clearance(role: Role, classification: Classification) -> bool:
    """True if `role` may see material at `classification`."""
    return _RANK[classification] <= _RANK[CLEARANCE[role]]
