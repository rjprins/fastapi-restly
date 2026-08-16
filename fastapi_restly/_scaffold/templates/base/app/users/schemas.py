import fastapi_restly as fr


class UserSchema(fr.TimestampsSchemaMixin, fr.IDSchema):
    email: str
    name: str
