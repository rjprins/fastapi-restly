"""Read-only lookup tables (countries, currencies, etc).

Exists to demonstrate the read-only-resource pattern: the data is seeded
out-of-band (migration / fixture / external sync) and the API surface
exposes only GET routes — never POST/PATCH/DELETE.
"""

from sqlalchemy import orm

import fastapi_restly as fr


class Country(fr.TimestampsMixin, fr.IDBase):
    """ISO country lookup. Seeded; never mutated via the API."""

    code: orm.Mapped[str] = orm.mapped_column(unique=True)  # ISO 3166-1 alpha-2
    name: orm.Mapped[str]
