"""Tenant API-key extraction for the Task 4 gateway."""

from __future__ import annotations

MAX_API_KEY_LENGTH = 512


class TenantKeyError(ValueError):
    """Raised when a tenant API key cannot be safely extracted."""


def extract_tenant_api_key(
    authorization: str | None,
) -> str:
    """Extract a tenant key from an HTTP Bearer authorization header."""

    if authorization is None:
        raise TenantKeyError(
            "Bearer tenant API key required"
        )

    scheme, separator, credential = authorization.partition(" ")

    if (
        not separator
        or scheme.lower() != "bearer"
    ):
        raise TenantKeyError(
            "Bearer tenant API key required"
        )

    credential = credential.strip()

    if (
        not credential
        or any(character.isspace() for character in credential)
        or len(credential) > MAX_API_KEY_LENGTH
    ):
        raise TenantKeyError(
            "Invalid tenant API key"
        )

    return credential
