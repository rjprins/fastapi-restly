"""Upload view with a multipart early-flush-for-PK write.

The multipart upload flow:

1. Build the parent ``Upload`` row from the file. The tenant and the
   uploader are the model's stamps from ``Current``.
2. Pass it to ``write_action`` as ``obj``: it is the new row that the action
   creates, so Restly adds it to the session.
3. ``await self.session.flush()`` inside the block: the INSERT gives
   ``upload.id``, the foreign key of the lines.
4. Add the ``UploadLine`` rows and the outbox event. When the block ends,
   Restly flushes, refreshes the upload and commits once.

The early flush gives related rows a parent id while the full transaction still
commits once.
"""

import csv
import io
from datetime import datetime, timezone

import fastapi

import fastapi_restly as fr

from ..views import AuthenticatedView
from .models import Upload, UploadLine
from .schemas import UploadLineSchema, UploadResponse, UploadSchema


class UploadView(AuthenticatedView[Upload]):
    """Read endpoints for uploads + a custom multipart POST.

    Generic create is excluded because the request format is multipart, not
    JSON. The CRUD scaffolding still covers GET / list / PATCH / DELETE,
    which are all JSON.
    """

    prefix = "/uploads"
    model = Upload
    schema = UploadSchema
    exclude_routes = [fr.ViewRoute.CREATE]

    @fr.post("/", response_model=UploadResponse, status_code=201)
    async def upload_csv(
        self,
        file: fastapi.UploadFile = fastapi.File(...),  # noqa: B008 — fastapi style
    ) -> Upload:
        """Parse a CSV file and create an Upload + UploadLine rows.

        The parent, lines, and outbox event are committed together through one
        ``write_action`` block, with the new upload as its ``obj``.
        """
        if not file.filename:
            raise fastapi.HTTPException(422, "filename is required")
        raw = await file.read()
        try:
            reader = csv.DictReader(io.StringIO(raw.decode("utf-8")))
            rows = list(reader)
        except UnicodeDecodeError as exc:
            raise fastapi.HTTPException(422, f"file is not utf-8: {exc}") from exc

        upload = Upload(
            filename=file.filename,
            line_count=len(rows),
            completed_at=datetime.now(timezone.utc),
        )
        # The new upload is the subject: the parent, the lines and the outbox
        # event are committed together.
        async with self.write_action(fr.Action.CREATE, obj=upload):
            await self.session.flush()  # upload.id, for the lines
            self.session.add_all(
                UploadLine(
                    upload_id=upload.id,
                    row_number=n,
                    title=row.get("title", ""),
                    amount=int(row.get("amount") or 0),
                )
                for n, row in enumerate(rows, start=1)
            )
            self._emit("upload.completed", upload, {"line_count": upload.line_count})
        return upload

    @fr.get("/{id}/lines", response_model=list[UploadLineSchema])
    async def list_lines(self, id: int) -> list[UploadLine]:
        """Return the parsed lines for an upload.

        ``handle_get_one`` answers 404 for an upload the caller cannot see.
        The lines carry their own tenant criterion in ``uploads.models``, so
        a read that skips this check is still filtered.
        """
        import sqlalchemy as sa

        await self.handle_get_one(id)
        result = await self.session.scalars(
            sa.select(UploadLine).where(UploadLine.upload_id == id)
        )
        return list(result.all())
