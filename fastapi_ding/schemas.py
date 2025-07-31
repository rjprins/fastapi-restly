import types
from datetime import datetime
from typing import Annotated, Any, ClassVar, Generic, Optional, TypeVar

import pydantic
from pydantic.fields import Field, FieldInfo
from sqlalchemy import select
from sqlalchemy.exc import NoResultFound
from sqlalchemy.orm import DeclarativeBase


class BaseSchema(pydantic.BaseModel):
    # TODO: Is this still needed?
    pass


class _Marker:
    def __init__(self, name: str):
        self.name = name

    def __repr__(self):
        return f"fd._Marker('{self.name}'>"


readonly_marker = _Marker("ReadOnly")
writeonly_marker = _Marker("WriteOnly")

_T = TypeVar("_T")

ReadOnly = Annotated[_T, readonly_marker, Field(json_schema_extra={"readOnly": True})]
WriteOnly = Annotated[
    _T, writeonly_marker, Field(json_schema_extra={"writeOnly": True})
]


class TimestampsSchemaMixin(pydantic.BaseModel):
    created_at: ReadOnly[datetime]
    updated_at: ReadOnly[datetime]


SQLAlchemyModel = TypeVar("SQLAlchemyModel", bound=DeclarativeBase)


class IDSchema(BaseSchema, Generic[SQLAlchemyModel]):
    """Generic schema useful for serializing only the id of objects.
    Can be used as IDSchema[MyModel].
    """

    id: ReadOnly[int]

    def get_sql_model_annotation(self) -> SQLAlchemyModel | None:
        """
        Return the annotation on IDSchema when used as:

        foo: IDSchema[Foo]

        This property will return "Foo".
        """
        try:
            return self.__pydantic_generic_metadata__["args"][0]
        except Exception:
            return None


class IDStampsSchema(TimestampsSchemaMixin, IDSchema[SQLAlchemyModel]):
    pass


async def async_resolve_ids_to_sqlalchemy_objects(
    schema_obj: BaseSchema, session: Any
) -> None:
    """
    Go over the Pydantic fields and turn any IDSchema objects into SQLAlchemy instances.
    A database request is made for each IDSchema to look up the related row in the database.
    If an id is not found in the database `sqlalchemy.orm.exc.NoResultFound` is raised.
    """
    # Go over all Pydantic fields and check if any of them are an IDSchema object or
    # a list of IDSchema objects.
    for field in schema_obj.model_fields_set:
        value = getattr(schema_obj, field, None)

        if isinstance(value, IDSchema):
            sql_model = value.get_sql_model_annotation()
            if not sql_model:
                continue

            # Replace the IDSchema object with a SQLAlchemy instance from the database
            sql_model_obj = await session.get_one(sql_model, value.id)
            setattr(schema_obj, field, sql_model_obj)

        elif isinstance(value, list) and any(isinstance(i, IDSchema) for i in value):
            # Assume all IdSchemas are for the same model
            sql_model = value[0].get_sql_model_annotation()
            if not sql_model:
                continue

            # Replace all IDSchema objects with SQLAlchemy instances
            ids = [obj.id for obj in value]
            query = select(sql_model).where(sql_model.id.in_(ids))
            sql_model_objs = list(await session.scalars(query))

            if len(ids) != len(sql_model_objs):
                missing_ids = set(ids).difference(o.id for o in sql_model_objs)
                raise NoResultFound(f"Id not found for {field}: {missing_ids}")

            setattr(schema_obj, field, sql_model_objs)


def resolve_ids_to_sqlalchemy_objects(schema_obj: BaseSchema, session: Any) -> None:
    """
    Go over the Pydantic fields and turn any IDSchema objects into SQLAlchemy instances.
    A database request is made for each IDSchema to look up the related row in the database.
    If an id is not found in the database `sqlalchemy.orm.exc.NoResultFound` is raised.
    """
    # Go over all Pydantic fields and check if any of them are an IDSchema object or
    # a list of IDSchema objects.
    for field in schema_obj.model_fields_set:
        value = getattr(schema_obj, field, None)

        if isinstance(value, IDSchema):
            sql_model = value.get_sql_model_annotation()
            if not sql_model:
                continue

            # Replace the IDSchema object with a SQLAlchemy instance from the database
            sql_model_obj = session.get_one(sql_model, value.id)
            setattr(schema_obj, field, sql_model_obj)

        elif isinstance(value, list) and any(isinstance(i, IDSchema) for i in value):
            # Assume all IdSchemas are for the same model
            sql_model = value[0].get_sql_model_annotation()
            if not sql_model:
                continue

            # Replace all IDSchema objects with SQLAlchemy instances
            ids = [obj.id for obj in value]
            query = select(sql_model).where(sql_model.id.in_(ids))
            sql_model_objs = list(session.scalars(query))

            if len(ids) != len(sql_model_objs):
                missing_ids = set(ids).difference(o.id for o in sql_model_objs)
                raise NoResultFound(f"Id not found for {field}: {missing_ids}")

            setattr(schema_obj, field, sql_model_objs)


def get_read_only_fields(model_cls: type[pydantic.BaseModel]) -> list[str]:
    """Get all fields from a model annotated as ReadOnly[]"""
    read_only_fields: list[str] = []
    # Get read-only fields from Annotated metadata
    for field_name, field_info in model_cls.model_fields.items():
        metadata = getattr(field_info, "metadata", None)
        if metadata and readonly_marker in metadata:
            read_only_fields.append(field_name)
    return read_only_fields


def is_field_readonly(model_cls: type[pydantic.BaseModel], field_name: str) -> bool:
    """Check if a specific field is marked as readonly."""
    field_info = model_cls.model_fields.get(field_name)
    if field_info is None:
        return False
    metadata = getattr(field_info, "metadata", None)
    if not metadata:
        return False
    return readonly_marker in metadata


def get_write_only_fields(model_cls: type[pydantic.BaseModel]) -> list[str]:
    """Get all fields from a model annotated as WriteOnly[]"""
    write_only_fields: list[str] = []
    # Get write-only fields from Annotated metadata
    for field_name, field_info in model_cls.model_fields.items():
        metadata = getattr(field_info, "metadata", None)
        if metadata and writeonly_marker in metadata:
            write_only_fields.append(field_name)
    return write_only_fields


def is_field_writeonly(model_cls: type[pydantic.BaseModel], field_name: str) -> bool:
    """Check if a specific field is marked as writeonly."""
    field_info = model_cls.model_fields.get(field_name)
    if field_info is None:
        return False
    metadata = getattr(field_info, "metadata", None)
    if not metadata:
        return False
    return writeonly_marker in metadata


def create_model_without_read_only_fields(
    model_cls: type[pydantic.BaseModel],
) -> type[pydantic.BaseModel]:
    """
    Copy the given pydantic model class, but with fields with names in
    'read_only_fields' removed.
    """
    read_only_fields = get_read_only_fields(model_cls)
    if not read_only_fields:
        return model_cls

    # Get the base classes, handling both generic and non-generic classes
    orig_bases: tuple[type, ...]
    if hasattr(model_cls, "__orig_bases__") and model_cls.__orig_bases__:
        orig_bases = model_cls.__orig_bases__
    else:
        orig_bases = model_cls.__bases__

    bases: list[type[pydantic.BaseModel]] = []
    for base_cls in orig_bases:
        if issubclass(base_cls, pydantic.BaseModel):
            base_cls = create_model_without_read_only_fields(base_cls)
            bases.append(base_cls)
        else:
            bases.append(base_cls)
    base = tuple(bases)

    new_model_name = "Create" + model_cls.__name__
    doc = model_cls.__doc__ or "" + "\nRead-only have been fields removed."
    writable_fields = _get_writable_field_definitions(model_cls)

    if model_cls.model_config:
        base = rebase_with_model_config(base, model_cls)

    # Ignore mypy because Pydantic typing on __base__ is too strict
    new_model_cls = pydantic.create_model(  # type: ignore
        new_model_name,
        __doc__=doc,
        __base__=base,
        __module__=model_cls.__module__,
        __validators__=getattr(model_cls, "__validators__", None),
        __cls_kwargs__=getattr(model_cls, "__cls_kwargs__", None),
        **writable_fields,
    )
    set_schema_title(new_model_cls)
    return new_model_cls


def rebase_with_model_config(
    base: tuple[type, ...], model_cls: type[pydantic.BaseModel]
) -> type[pydantic.BaseModel]:
    def class_body(ns: dict[str, Any]) -> None:
        ns["model_config"] = model_cls.model_config.copy()

    return types.new_class(
        f"{model_cls.__name__}ModelConfig", base, exec_body=class_body
    )


# NOT_SET is used as a sentinel value for validating incoming data. It is the default
# value for update_schemas and allows us to see the difference between 'None' and
# 'not submitted'. A string is used so it renders nicely in /docs and /redoc.
NOT_SET = "Partial PUT does not support default values"


def create_model_with_optional_fields(
    model_cls: type[pydantic.BaseModel],
) -> type[pydantic.BaseModel]:
    """
    Copy the given pydantic model class, but with fields with names in
    'read_only_fields' removed and all writable fields made optional with NOT_SET defaults.
    """
    # Get the base classes, handling both generic and non-generic classes
    orig_bases: tuple[type, ...]
    if hasattr(model_cls, "__orig_bases__") and model_cls.__orig_bases__:
        orig_bases = model_cls.__orig_bases__
    else:
        orig_bases = model_cls.__bases__

    bases: list[type[pydantic.BaseModel]] = []
    for base_cls in orig_bases:
        if issubclass(base_cls, pydantic.BaseModel):
            # Check if the base class has any ReadOnly fields
            has_readonly_fields = any(
                getattr(field_info, "metadata", None)
                and readonly_marker in field_info.metadata
                for field_info in base_cls.model_fields.values()
            )
            if has_readonly_fields:
                new_base_cls = create_model_with_optional_fields(base_cls)
                bases.append(new_base_cls)
            else:
                bases.append(base_cls)
        else:
            bases.append(base_cls)
    base = tuple(bases)

    new_model_name = "Update" + model_cls.__name__
    doc = (
        model_cls.__doc__
        or "" + "\nRead-only fields have been removed and all fields are optional."
    )

    # Get writable fields and make them optional with NOT_SET defaults
    writable_fields = _get_writable_field_definitions(model_cls)
    optional_fields = {}
    for field_name, (field_annotation, field_info) in writable_fields.items():
        field_with_default = FieldInfo.merge_field_infos(field_info, default=NOT_SET)
        optional_fields[field_name] = (Optional[field_annotation], field_with_default)

    if model_cls.model_config:
        base = rebase_with_model_config(base, model_cls)

    # Ignore mypy because Pydantic typing on __base__ is too strict
    new_model_cls = pydantic.create_model(  # type: ignore
        new_model_name,
        __doc__=doc,
        __base__=base,
        __module__=model_cls.__module__,
        __validators__=getattr(model_cls, "__validators__", None),
        __cls_kwargs__=getattr(model_cls, "__cls_kwargs__", None),
        **optional_fields,
    )
    set_schema_title(new_model_cls)
    return new_model_cls


def _get_writable_field_definitions(
    model_cls: type[pydantic.BaseModel],
) -> dict[str, tuple[Any, Any]]:
    """
    Return fields from a Pydantic model that are not mentioned in `read_only_fields`.
    Field definitions are returned in the form suitable for `pydantic.create_model()`.
    See https://docs.pydantic.dev/latest/api/base_model/#pydantic.create_model
    """
    read_only_fields = get_read_only_fields(model_cls)
    writable_fields: dict[str, tuple[Any, Any]] = {}
    for field_name, field_info in model_cls.model_fields.items():
        if field_name not in read_only_fields:
            writable_fields[field_name] = (field_info.annotation, field_info)
    return writable_fields


def getattrs(obj: Any, *attrs: str, default: Any = None) -> Any:
    """
    Try access a chain of attributes and return the default if any of the attrs is not defined.
    """
    for attr in attrs:
        if not hasattr(obj, attr):
            return default
        obj = getattr(obj, attr)
    return obj


def set_schema_title(schema_cls: type[pydantic.BaseModel]) -> None:
    """
    Ensure that a schema class maintains its name in OpenAPI generation.
    This uses Pydantic's built-in model_config to control schema naming.

    Args:
        schema_cls: The original schema class
    """
    # Get or create model_config
    config = getattr(schema_cls, "model_config", pydantic.ConfigDict())

    # Ensure the title is set to the class name
    if not config.get("title"):
        config["title"] = schema_cls.__name__
        schema_cls.model_config = config


def make_response_schema(
    schema_cls: type[pydantic.BaseModel],
) -> type[pydantic.BaseModel]:
    """
    Create a response model from a schema that includes populate_by_name=True.
    This allows GET responses to work with aliases while keeping POST/PUT operations
    safe by requiring field names.

    Args:
        schema_cls: The original schema class

    Returns:
        A new schema class with populate_by_name=True enabled
    """
    config = getattr(schema_cls, "model_config", pydantic.ConfigDict())
    if config.get("populate_by_name") and config.get("from_attributes"):
        set_schema_title(schema_cls)
        return schema_cls

    new_config = config.copy()
    new_config.update(populate_by_name=True, from_attributes=True)

    # Create a new schema class using Pydantic's built-in capabilities
    new_schema = type(
        schema_cls.__name__,  # Keep the original name
        (schema_cls,),
        {
            "model_config": new_config,
            "__module__": schema_cls.__module__,  # Preserve module info
        },
    )

    set_schema_title(new_schema)
    return new_schema
