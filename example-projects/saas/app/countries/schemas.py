"""Country lookup schema."""

import fastapi_restly as fr


class CountrySchema(fr.TimestampsSchemaMixin, fr.IDSchema):
    """ISO country code + display name."""

    code: str
    name: str
