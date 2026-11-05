import uuid
from dataclasses import dataclass


@dataclass(frozen=True)
class OrganizationContext:
    """Immutable representation of the resolved tenant and actor context for an authenticated request.

    Attributes:
        user_id: The UUID of the authenticated User.
        organization_id: The UUID of the active Organization (buyer tenant).
        membership_id: The UUID of the verified Membership record linking user to organization.
        role: The verified role in this tenant ('owner', 'admin', 'reviewer', 'viewer').
        user_email: The authenticated user's email address.
        user_full_name: The authenticated user's display name.
        is_superuser: Whether the user holds system-wide superuser privileges.
        organization_name: The display name of the buyer organization.
        organization_slug: The URL-safe slug of the buyer organization.
    """

    user_id: uuid.UUID
    organization_id: uuid.UUID
    membership_id: uuid.UUID
    role: str
    user_email: str
    user_full_name: str
    is_superuser: bool = False
    organization_name: str = ""
    organization_slug: str = ""

    def has_role(self, *allowed_roles: str) -> bool:
        """Verify whether the user's role within this organization matches any allowed role."""
        return self.role in allowed_roles

    @property
    def is_owner(self) -> bool:
        return self.role == "owner"

    @property
    def is_admin(self) -> bool:
        return self.role == "admin"

    @property
    def is_reviewer(self) -> bool:
        return self.role == "reviewer"

    @property
    def is_viewer(self) -> bool:
        return self.role == "viewer"

    @property
    def is_admin_or_owner(self) -> bool:
        return self.role in ("admin", "owner")
