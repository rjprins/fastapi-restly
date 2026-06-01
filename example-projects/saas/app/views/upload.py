"""Upload view with a multipart early-flush-for-PK write.

The multipart upload flow:

1. Build the parent ``Upload`` row from form fields.
2. ``await self.session.flush()`` — populates ``upload.id`` from the
   autoincrement so we can use it as a foreign key on the lines.
3. Mutate: parse the file, attach related ``UploadLine`` rows, set the
   denormalized ``line_count`` and ``completed_at`` from the parse result.
4. ``await self.save_object(upload)`` — final flush + refresh.

The early flush gives related rows a parent id while the full transaction still
commits once.
"""

import csv
import io

import fastapi

import fastapi_restly as fr

from ..models import Upload, UploadLine
from ..schemas import UploadLineSchema, UploadSchema
from ._base import TenantBase


class UploadView(TenantBase):
    """Read endpoints for uploads + a custom multipart POST.

    Generic create is excluded because the wire format is multipart, not
    JSON. The CRUD scaffolding still covers GET / list / PATCH / DELETE,
    which are all JSON.
    """

    prefix = "/uploads"
    model = Upload
    schema = UploadSchema
    exclude_routes = [fr.ViewRoute.CREATE]

    @fr.post("/", response_model=UploadSchema, status_code=201)
    async def upload_csv(
        self,
        file: fastapi.UploadFile = fastapi.File(...),  # noqa: B008 — fastapi style
        organization_id: int = fastapi.Form(...),  # noqa: B008
    ) -> Upload:
        """Parse a CSV file and create an Upload + UploadLine rows.

        The parent, lines, and outbox event are committed together through one
        ``write_action("create")`` block.
        """
        if not file.filename:
            raise fastapi.HTTPException(422, "filename is required")
        raw = await file.read()
        try:
            reader = csv.DictReader(io.StringIO(raw.decode("utf-8")))
            rows = list(reader)
        except UnicodeDecodeError as exc:
            raise fastapi.HTTPException(422, f"file is not utf-8: {exc}") from exc

        # Commit the parent, lines, and outbox event together.
        async with self.write_action("create") as w:
            # 1) Build the parent directly from multipart form fields.
            upload = Upload(
                filename=file.filename,
                organization_id=organization_id,
                uploaded_by_id=self._current_user_id(),
            )
            self.session.add(upload)

            # 2) Early flush: get upload.id before creating child rows.
            await self.session.flush()

            # 3) Build related rows referencing the parent's PK.
            for n, row in enumerate(rows, start=1):
                line = UploadLine(
                    upload_id=upload.id,
                    row_number=n,
                    title=row.get("title", ""),
                    amount=int(row.get("amount") or 0),
                )
                self.session.add(line)

            upload.line_count = len(rows)
            from datetime import datetime, timezone

            upload.completed_at = datetime.now(timezone.utc)

            # 4) Final flush + refresh for generated columns/defaults.
            saved = await self.save_object(upload)
            self._emit("upload.completed", saved, {"line_count": saved.line_count})
            w.obj = saved
        return w.obj

    @fr.get("/{id}/lines", response_model=list[UploadLineSchema])
    async def list_lines(self, id: int) -> list[UploadLine]:
        """Return the parsed lines for an upload."""
        import sqlalchemy as sa

        result = await self.session.scalars(
            sa.select(UploadLine).where(UploadLine.upload_id == id)
        )
        return list(result.all())
