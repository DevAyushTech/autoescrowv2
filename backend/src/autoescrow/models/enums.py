from enum import StrEnum


class UserStatus(StrEnum):
    ACTIVE = "active"
    SUSPENDED = "suspended"
    DEACTIVATED = "deactivated"


class AuthNoncePurpose(StrEnum):
    LOGIN = "login"
    WALLET_LINK = "wallet_link"


class RoleCode(StrEnum):
    USER = "USER"
    AGENT_OWNER = "AGENT_OWNER"
    ARBITRATOR = "ARBITRATOR"
    ADMIN = "ADMIN"


class RefreshRevocationReason(StrEnum):
    ROTATED = "rotated"
    LOGOUT = "logout"
    USER_REVOKED = "user_revoked"
    REUSE_DETECTED = "reuse_detected"
