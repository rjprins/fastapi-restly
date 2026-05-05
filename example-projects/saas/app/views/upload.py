"""Upload view — multipart import with early-flush-for-PK pattern.

This view is the canonical illustration of why ``make_new_object`` and
``save_object`` need to be public utilities (per option B' in
``rut-notes/discussion_save_object.md``). The multipart upload flow:

1. Build the parent ``Upload`` row from form fields.
2. ``await self.session.flush()`` — populates ``upload.id`` from the
   autoincrement so we can use it as a foreign key on the lines.
3. Mutate: parse the file, attach related ``UploadLine`` rows, set the
   denormalized ``line_count`` and ``completed_at`` from the parse result.
4. ``await self.save_object(upload)`` — final flush + refresh.

Why not just rely on relationship cascade? Because step (3) needs
``upload.id`` *before* we know how many lines there are or whether the
parse succeeds. We need to commit-to-an-id, then keep mutating in the
same transaction. That is the exact gap a single ``create_object``
helper can't fill (option D from the doc) and is why ``save_object``
remains a public utility.
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
    exclude_routes = ["create"]

    @fr.post("/", response_model=UploadSchema, status_code=201)
    async def upload_csv(
        self,
        file: fastapi.UploadFile = fastapi.File(...),  # noqa: B008 — fastapi style
        organization_id: int = fastapi.Form(...),  # noqa: B008
    ) -> Upload:
        """Parse a CSV file and create an Upload + UploadLine rows.

        The flow is the entire reason ``make_new_object`` /
        ``session.flush()`` / ``save_object`` are separate public steps —
        no single helper would absorb the flush-for-PK requirement.
        """
        if not file.filename:
            raise fastapi.HTTPException(422, "filename is required")
        raw = await file.read()
        try:
            reader = csv.DictReader(io.StringIO(raw.decode("utf-8")))
            rows = list(reader)
        except UnicodeDecodeError as exc:
            raise fastapi.HTTPException(422, f"file is not utf-8: {exc}") from exc

        # 1) Build parent. ``make_new_object`` is overkill here because we're
        #    not coming from a JSON body schema, so we construct directly. The
        #    framework call style (``self.make_new_object``) is also valid if
        #    you do have a schema_obj.
        upload = Upload(
            filename=file.filename,
            organization_id=organization_id,
            uploaded_by_id=self._current_user_id(),
        )
        self.session.add(upload)

        # 2) Early flush — gives upload.id its autoincrement value before we
        #    use it as a FK below.
        await self.session.flush()

        # 3) Mutate: build related rows referencing the parent's PK.
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

        # 4) Final flush + refresh — picks up server-side defaults on the
        #    UploadLine rows and the updated columns on Upload.
        upload = await self.save_object(upload)
        self._emit("upload.completed", upload, {"line_count": upload.line_count})
        return upload

    @fr.get("/{id}/lines", response_model=list[UploadLineSchema])
    async def list_lines(self, id: int) -> list[UploadLine]:
        """Return the parsed lines for an upload."""
        import sqlalchemy as sa

        result = await self.session.scalars(
            sa.select(UploadLine).where(UploadLine.upload_id == id)
        )
        return list(result.all())
