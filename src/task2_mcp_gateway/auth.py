"""Bearer-token authentication for the Task 2 assessment gateway."""

from __future__ import annotations

import hmac
import os
from collections.abc import Mapping
from enum import StrEnum


class Role(StrEnum):
    """Roles understood by the assessment gateway."""

    ADMIN = "admin"
    VIEWER = "viewer"


class AuthenticationError(ValueError):
    """Raised when an Authorization header cannot be authenticated."""


DEMO_ADMIN_TOKEN = "assessment-admin-token"
DEMO_VIEWER_TOKEN = "assessment-viewer-token"


def token_roles_from_environment() -> dict[str, Role]:
    """Load assessment tokens, using explicit non-secret demo defaults.

    The defaults make the assessment runnable without external identity
    infrastructure. They are intentionally documented demo credentials,
    not production secrets.
    """

    admin_token = os.getenv("TASK2_ADMIN_TOKEN", DEMO_ADMIN_TOKEN)
    viewer_token = os.getenv("TASK2_VIEWER_TOKEN", DEMO_VIEWER_TOKEN)

    if not admin_token or not viewer_token:
        raise RuntimeError("Task 2 bearer tokens must not be empty")

    if hmac.compare_digest(admin_token, viewer_token):
        raise RuntimeError("Admin and viewer tokens must be different")

    return {
        admin_token: Role.ADMIN,
        viewer_token: Role.VIEWER,
    }


class TokenAuthenticator:
    """Resolve trusted opaque bearer tokens to assessment roles."""

    def __init__(self, token_roles: Mapping[str, Role]) -> None:
        if not token_roles:
            raise ValueError("At least one bearer token must be configured")

        self._token_roles = tuple(token_roles.items())

    def authenticate(self, authorization: str | None) -> Role:
        """Authenticate a Bearer token and return its trusted role."""

        if authorization is None:
            raise AuthenticationError("Missing bearer token")

        parts = authorization.strip().split()

        if len(parts) != 2 or parts[0].lower() != "bearer":
            raise AuthenticationError("Invalid authorization scheme")

        candidate = parts[1]

        matched_role: Role | None = None

        # Compare against every configured token rather than trusting any role
        # value supplied inside the request itself.
        for known_token, role in self._token_roles:
            if hmac.compare_digest(candidate, known_token):
                matched_role = role

        if matched_role is None:
            raise AuthenticationError("Invalid bearer token")

        return matched_role
