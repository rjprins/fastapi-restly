import fastapi_restly as fr
from sqlalchemy import orm


class Base(fr.ModelDefaults, orm.DeclarativeBase):
    """The base of every model in the app.

    It is your class: set metadata or type_annotation_map here to change a
    default for all models.
    """
