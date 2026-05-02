"""Tests for IntegrityError / unique-constraint / FK violation scenarios (I2).

The framework installs a default exception handler that translates
SQLAlchemy ``IntegrityError`` into HTTP 409 Conflict (with a clean JSON
body). These tests pin that behavior end-to-end. Users can opt out via
``fr.configure(install_default_exception_handlers=False)`` — the
``test_exception_handlers.py`` suite covers that path.
"""

from sqlalchemy import ForeignKey, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

import fastapi_restly as fr

from .conftest import create_tables

# ---------------------------------------------------------------------------
# Unique-column violation on POST
# ---------------------------------------------------------------------------


def test_post_duplicate_unique_column_returns_409(client):
    """POSTing two records with the same unique value: the second triggers
    a SQLAlchemy ``IntegrityError`` which the framework's default handler
    translates to HTTP 409 with a clean JSON body."""

    class UserUnique(fr.IDBase):
        username: Mapped[str] = mapped_column(unique=True)
        email: Mapped[str]

    class UserUniqueSchema(fr.IDSchema):
        username: str
        email: str

    @fr.include_view(client.app)
    class UserUniqueView(fr.AsyncRestView):
        prefix = "/unique-users"
        model = UserUnique
        schema = UserUniqueSchema

    create_tables()

    # First insert succeeds
    response = client.post(
        "/unique-users/", json={"username": "alice", "email": "alice@example.com"}
    )
    assert response.status_code == 201

    # Second insert with same username is now translated to 409.
    response = client.post(
        "/unique-users/",
        json={"username": "alice", "email": "alice2@example.com"},
        assert_status_code=409,
    )
    assert response.status_code == 409
    body = response.json()
    assert "detail" in body
    assert "unique" in body["detail"].lower()


def test_post_duplicate_explicit_unique_constraint_returns_409(client):
    """Same as above but using an explicit table-level UniqueConstraint."""

    class Coupon(fr.IDBase):
        code: Mapped[str]
        campaign: Mapped[str]
        __table_args__ = (UniqueConstraint("code", "campaign", name="uq_coupon_code_campaign"),)

    class CouponSchema(fr.IDSchema):
        code: str
        campaign: str

    @fr.include_view(client.app)
    class CouponView(fr.AsyncRestView):
        prefix = "/coupons"
        model = Coupon
        schema = CouponSchema

    create_tables()

    response = client.post(
        "/coupons/", json={"code": "SAVE10", "campaign": "spring"}
    )
    assert response.status_code == 201

    response = client.post(
        "/coupons/",
        json={"code": "SAVE10", "campaign": "spring"},
        assert_status_code=409,
    )
    assert response.status_code == 409
    assert "unique" in response.json()["detail"].lower()


# ---------------------------------------------------------------------------
# Unique violation on PATCH/PUT (update)
# ---------------------------------------------------------------------------


def test_patch_to_duplicate_unique_value_returns_409(client):
    """Updating a record to use a username that already exists violates the
    unique constraint and is translated to 409."""

    class Account(fr.IDBase):
        username: Mapped[str] = mapped_column(unique=True)

    class AccountSchema(fr.IDSchema):
        username: str

    @fr.include_view(client.app)
    class AccountView(fr.AsyncRestView):
        prefix = "/accounts"
        model = Account
        schema = AccountSchema

    create_tables()

    a = client.post("/accounts/", json={"username": "alice"}).json()
    client.post("/accounts/", json={"username": "bob"}).json()

    response = client.patch(
        f"/accounts/{a['id']}",
        json={"username": "bob"},
        assert_status_code=409,
    )
    assert response.status_code == 409
    assert "unique" in response.json()["detail"].lower()


# ---------------------------------------------------------------------------
# FK constraint violation
# ---------------------------------------------------------------------------


def test_post_with_invalid_fk_via_plain_int_raises_integrity_error(client):
    """When the schema accepts a plain int FK (not IDSchema), the framework
    does NOT validate that the parent row exists. The DB raises an
    IntegrityError on flush.

    Note: SQLite by default does not enforce FK constraints unless
    `PRAGMA foreign_keys = ON` is set. The framework's default in-memory
    SQLite session does not enable FK enforcement, so this currently
    succeeds silently. We pin THAT behavior.
    """

    class Owner(fr.IDBase):
        name: Mapped[str]

    class Pet(fr.IDBase):
        name: Mapped[str]
        owner_id: Mapped[int] = mapped_column(ForeignKey("owner.id"))

    class PetSchema(fr.IDSchema):
        name: str
        owner_id: int

    @fr.include_view(client.app)
    class PetView(fr.AsyncRestView):
        prefix = "/pets"
        model = Pet
        schema = PetSchema

    create_tables()

    # No owner exists, but FK enforcement is OFF by default in SQLite.
    response = client.post("/pets/", json={"name": "Rex", "owner_id": 9999})
    # Pin the current behavior: this currently succeeds (no FK enforcement).
    # If the framework later turns on PRAGMA foreign_keys, this becomes an
    # IntegrityError translated to 409, and the test breaks deliberately.
    assert response.status_code == 201
    assert response.json()["owner_id"] == 9999


def test_post_with_invalid_fk_via_idschema_returns_404(client):
    """When the schema declares the FK as `IDSchema[Parent]`, the framework
    looks up the parent and raises HTTPException(404) before flush. This
    is the well-defined contract — pin it."""

    class Department(fr.IDBase):
        name: Mapped[str]

    class Employee(fr.IDBase):
        name: Mapped[str]
        department_id: Mapped[int] = mapped_column(ForeignKey("department.id"))
        department: Mapped[Department] = relationship(default=None)

    class EmployeeSchema(fr.IDSchema):
        name: str
        department_id: fr.IDSchema[Department]

    @fr.include_view(client.app)
    class EmployeeView(fr.AsyncRestView):
        prefix = "/employees"
        model = Employee
        schema = EmployeeSchema

    create_tables()

    response = client.post(
        "/employees/",
        json={"name": "Jane", "department_id": {"id": 99999}},
        assert_status_code=404,
    )
    assert response.status_code == 404


# ---------------------------------------------------------------------------
# NOT NULL constraint violation
# ---------------------------------------------------------------------------


def test_post_missing_required_field_returns_422(client):
    """Missing a required Pydantic field is caught at the validation layer
    (422), not at the DB layer. Pin this so the framework keeps short-
    circuiting before hitting the database."""

    class StrictItem(fr.IDBase):
        name: Mapped[str]
        sku: Mapped[str]

    class StrictItemSchema(fr.IDSchema):
        name: str
        sku: str

    @fr.include_view(client.app)
    class StrictItemView(fr.AsyncRestView):
        prefix = "/strict-items"
        model = StrictItem
        schema = StrictItemSchema

    create_tables()

    response = client.post("/strict-items/", json={"name": "Hat"}, assert_status_code=422)
    assert response.status_code == 422
