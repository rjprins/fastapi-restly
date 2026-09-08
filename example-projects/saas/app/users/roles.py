"""User roles within an organization."""

from enum import Enum


class UserRole(str, Enum):
    """User roles within an organization.

    ``MEMBER`` is the restricted role: a member sees the tasks assigned to
    them, every other role sees the organization's tasks. ``OWNER`` and
    ``HR`` see salaries.
    """

    OWNER = "owner"
    ADMIN = "admin"
    MEMBER = "member"
    HR = "hr"
