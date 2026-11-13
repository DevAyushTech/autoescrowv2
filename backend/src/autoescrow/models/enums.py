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


class AgentStatus(StrEnum):
    DRAFT = "draft"
    ACTIVE = "active"
    INACTIVE = "inactive"


class AgentAvailability(StrEnum):
    AVAILABLE = "available"
    LIMITED = "limited"
    UNAVAILABLE = "unavailable"


class PricingModel(StrEnum):
    FIXED = "fixed"
    HOURLY = "hourly"
    NEGOTIABLE = "negotiable"


class TaskCategory(StrEnum):
    DEVELOPMENT = "development"
    DESIGN = "design"
    WRITING = "writing"
    DATA = "data"
    MARKETING = "marketing"
    RESEARCH = "research"
    OPERATIONS = "operations"
    OTHER = "other"


class JobStatus(StrEnum):
    DRAFT = "draft"
    OPEN = "open"
    IN_PROGRESS = "in_progress"
    CANCELLED = "cancelled"
    COMPLETED = "completed"


class JobVisibility(StrEnum):
    PRIVATE = "private"
    PUBLIC = "public"


class AgreementStatus(StrEnum):
    PENDING_ACCEPTANCE = "pending_acceptance"
    ACTIVE = "active"
    CANCELLED = "cancelled"


class AgreementParty(StrEnum):
    OWNER = "owner"
    AGENT_OWNER = "agent_owner"
