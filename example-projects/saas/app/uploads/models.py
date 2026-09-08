"""Upload model for the early-flush-for-PK pattern.

The multipart upload route needs ``Upload.id`` before it can insert
``UploadLine`` rows. The flow is::

    upload = Upload(filename=...)
    await self.session.flush()        # populate upload.id
    upload.lines = [
        UploadLine(upload_id=upload.id, ...) for row in parsed_rows
    ]
    upload = await self.save_object(upload)   # final flush + refresh

The flow needs two flush points with mutation in between.
"""

from datetime import datetime

from sqlalchemy import ForeignKey, orm

import fastapi_restly as fr

from ..context import Current, TenantClauses
from ..models import TenantOwned


class Upload(TenantOwned, fr.TimestampsMixin, fr.IDBase):
    """Parent row for a batch of imported lines."""

    filename: orm.Mapped[str]
    uploaded_by_id: orm.Mapped[int | None] = orm.mapped_column(
        ForeignKey("user.id"), init=False, insert_default=lambda: Current.user_id()
    )
    completed_at: orm.Mapped[datetime | None] = orm.mapped_column(default=None)
    line_count: orm.Mapped[int] = orm.mapped_column(default=0)

    lines: orm.Mapped[list["UploadLine"]] = orm.relationship(
        back_populates="upload", default_factory=list, cascade="all, delete-orphan"
    )


class UploadClauses(TenantClauses):
    """Upload visibility: the tenant floor alone."""

    model = Upload


class UploadLine(fr.TimestampsMixin, fr.IDBase):
    """One parsed row from the uploaded file."""

    upload_id: orm.Mapped[int] = orm.mapped_column(ForeignKey("upload.id"))
    row_number: orm.Mapped[int]
    title: orm.Mapped[str]
    amount: orm.Mapped[int] = orm.mapped_column(default=0)

    upload: orm.Mapped["Upload"] = orm.relationship(back_populates="lines", init=False)
