from fastapi import Depends, FastAPI
from sqlalchemy.orm import DeclarativeBase, Mapped

import fastapi_restly as fr

app = FastAPI()


class Base(fr.ModelDefaults, DeclarativeBase):
    pass


class Tag(fr.IDMixin, Base):
    name: Mapped[str]


class TagSchema(fr.IDSchema[Tag]):
    name: str


class QuietRoutes:
    delete_endpoint = fr.route_options(status_code=200)


@fr.include_view(app)
class TagView(QuietRoutes, fr.AsyncRestView):
    prefix = "/tags"
    model = Tag
    schema = TagSchema

    get_one_endpoint = fr.route_options(responses={404: None})
    create_endpoint = fr.route_options("/new", name="create_tag")
    update_endpoint = fr.route_options(
        methods=["PUT"], dependencies=[Depends(lambda: None)]
    )


@fr.include_view(app)
class NamedIdView(fr.AsyncRestView):
    prefix = "/named"
    model = Tag
    schema = TagSchema
    id_param = "named_id:int"
