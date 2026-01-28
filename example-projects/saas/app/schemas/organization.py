"""Organization schema."""

import re

from pydantic import BaseModel, field_validator

import fastapi_restly as fr

from ..models import Organization


class OrganizationSchema(fr.TimestampsSchemaMixin, fr.IDSchema[Organization]):
    """Schema for Organization model."""

    name: str
    slug: str


class OrganizationCreateSchema(BaseModel):
    """Custom creation schema with stricter validation.

    This demonstrates using a different schema for POST operations.
    - Slug must be lowercase alphanumeric with hyphens only
    - Name has minimum length requirement
    """

    name: str
    slug: str

    @field_validator("slug")
    @classmethod
    def validate_slug(cls, v: str) -> str:
        """Validate slug is lowercase alphanumeric with hyphens."""
        if not re.match(r"^[a-z0-9]+(?:-[a-z0-9]+)*$", v):
            raise ValueError(
                "Slug must be lowercase alphanumeric with hyphens (e.g., 'my-org-name')"
            )
        return v

    @field_validator("name")
    @classmethod
    def validate_name(cls, v: str) -> str:
        """Validate name has minimum length."""
        if len(v) < 2:
            raise ValueError("Name must be at least 2 characters")
        return v


class OrganizationUpdateSchema(BaseModel):
    """Custom update schema - only name can be updated, not slug.

    This demonstrates using a different schema for PATCH operations.
    """

    name: str | None = None
