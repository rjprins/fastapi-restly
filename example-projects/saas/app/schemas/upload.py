"""Upload + UploadLine schemas (read-only)."""

from datetime import datetime

import fastapi_restly as fr


class UploadLineSchema(fr.TimestampsSchemaMixin, fr.IDSchema):
    """Read view of a single parsed line."""

    upload_id: int
    row_number: int
    title: str
    amount: int = 0


class UploadSchema(fr.TimestampsSchemaMixin, fr.IDSchema):
    """Read view of an upload batch.

    The matching upload endpoint is a custom multipart POST on
    ``UploadView``; the generic CRUD ``post`` route is excluded because
    the wire format (``multipart/form-data`` with a file) doesn't fit
    the framework's JSON-only auto-generated handler.
    """

    filename: str
    organization_id: int
    uploaded_by_id: int | None = None
    completed_at: datetime | None = None
    line_count: int = 0
