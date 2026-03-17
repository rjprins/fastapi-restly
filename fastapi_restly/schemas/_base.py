import types
from datetime import datetime
from typing import Annotated, Any, Generic, Optional, TypeVar, get_args, get_origin

from fastapi import HTTPException
import pydantic
from pydantic.fields import Field, FieldInfo
from sqlalchemy import select
from sqlalchemy.exc import NoResultFound
from sqlalchemy.ext.asyncio.session import AsyncSession as SA_AsyncSession
from sqlalchemy.orm import DeclarativeBase
from sqlalchemy.orm.session import Session as SA_Session


class BaseSchema(pydantic.BaseModel):
    # Allow validating SQLAlchemy model instances directly in request/response flows.
    # This keeps aliased fields working when FastAPI validates ORM objects.
    model_config = pydantic.ConfigDict(from_attributes=True)


class _Marker:
    def __init__(self, name: str):
        self.name = name

    def __repr__(self):
        return f"fr.{self.name}"


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

    # Keep this broad so relation-id payloads can target non-int primary keys.
    id: ReadOnly[Any]

    @classmethod
    def _get_sql_model_annotation(cls) -> type[DeclarativeBase] | None:
        try:
            sql_model = cls.__pydantic_generic_metadata__["args"][0]
        except Exception:
            return None
        return sql_model if isinstance(sql_model, type) else None

    @classmethod
    def _get_sql_model_id_type(cls) -> Any:
        sql_model = cls._get_sql_model_annotation()
        if sql_model is None:
            return None

        for model_cls in sql_model.mro():
            annotation = getattr(model_cls, "__annotations__", {}).get("id")
            if annotation is None:
                continue
            origin = get_origin(annotation)
            if origin is not None:
                args = get_args(annotation)
                if args:
                    return args[0]
            return annotation

        try:
            return sql_model.__mapper__.primary_key[0].type.python_type
        except Exception:
            return None

    @pydantic.field_validator("id", mode="before", check_fields=False)
    @classmethod
    def _coerce_id_to_model_primary_key_type(cls, value: Any) -> Any:
        id_type = cls._get_sql_model_id_type()
        if id_type in (None, Any):
            return value
        return pydantic.TypeAdapter(id_type).validate_python(value)

    def get_sql_model_annotation(self) -> SQLAlchemyModel | None:
        """
        Return the annotation on IDSchema when used as:

        foo: IDSchema[Foo]

        This property will return "Foo".
        """
        return self._get_sql_model_annotation()


class IDStampsSchema(TimestampsSchemaMixin, IDSchema):
    pass


async def async_resolve_ids_to_sqlalchemy_objects(
    session: SA_AsyncSession, schema_obj: BaseSchema
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
            try:
                sql_model_obj = await session.get_one(sql_model, value.id)
            except NoResultFound as e:
                raise HTTPException(
                    status_code=404, detail=f"Id not found for {field}: {value.id}"
                ) from e
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
                raise HTTPException(
                    status_code=404, detail=f"Id not found for {field}: {missing_ids}"
                )

            setattr(schema_obj, field, sql_model_objs)


def resolve_ids_to_sqlalchemy_objects(
    session: SA_Session, schema_obj: BaseSchema
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
            try:
                sql_model_obj = session.get_one(sql_model, value.id)
            except NoResultFound as e:
                raise HTTPException(
                    status_code=404, detail=f"Id not found for {field}: {value.id}"
                ) from e
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
                raise HTTPException(
                    status_code=404, detail=f"Id not found for {field}: {missing_ids}"
                )

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


def is_readonly_field(
    model: pydantic.BaseModel | type[pydantic.BaseModel], field_name: str
) -> bool:
    """Check if a specific field is marked as readonly."""
    if isinstance(model, pydantic.BaseModel):
        model = model.__class__
    field_info = model.model_fields.get(field_name)
    return _is_readonly(field_info)


def _is_readonly(field_info: FieldInfo | None) -> bool:
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
    Create a subclass of the given pydantic model class with a new name.
    """
    new_model_name = "Create" + model_cls.__name__
    new_doc = (model_cls.__doc__ or "") + "\nRead-only fields have been removed."

    # Create a subclass that mixes in OmitReadOnlyMixin
    new_model_cls = type(
        new_model_name,
        (OmitReadOnlyMixin, model_cls),
        {"__module__": model_cls.__module__, "__doc__": new_doc},
    )

    return new_model_cls


class OmitReadOnlyMixin(pydantic.BaseModel):
    """
    Mixin for pydantic models that removes all fields marked as ReadOnly.
    """

    @classmethod
    def __pydantic_init_subclass__(cls, **kwargs: Any) -> None:
        super().__pydantic_init_subclass__(**kwargs)

        # Collect readonly fields to delete first
        readonly_fields = []
        for name, field_info in cls.model_fields.items():
            if _is_readonly(field_info):
                readonly_fields.append(name)

        # Delete readonly fields after iteration is complete
        for name in readonly_fields:
            del cls.model_fields[name]

        cls.model_rebuild(force=True)


def rebase_with_model_config(
    base: tuple[type, ...], model_cls: type[pydantic.BaseModel]
) -> type[pydantic.BaseModel]:
    def class_body(ns: dict[str, Any]) -> None:
        ns["model_config"] = model_cls.model_config.copy()

    return types.new_class(
        f"{model_cls.__name__}ModelConfig", base, exec_body=class_body
    )


def create_model_with_optional_fields(
    model_cls: type[pydantic.BaseModel],
) -> type[pydantic.BaseModel]:
    """
    Create a subclass of the given pydantic model class with a new name.
    Read-only fields are removed and all writable fields are made optional with None as default.
    """
    new_model_name = "Update" + model_cls.__name__
    new_doc = (
        model_cls.__doc__
        or "" + "\nRead-only fields have been removed and all fields are optional."
    )

    # Create a subclass that mixes in both OmitReadOnlyMixin and PatchMixin
    new_model_cls = type(
        new_model_name,
        (PatchMixin, OmitReadOnlyMixin, model_cls),
        {"__module__": model_cls.__module__, "__doc__": new_doc},
    )

    return new_model_cls


class PatchMixin(pydantic.BaseModel):
    """
    A mixin for pydantic classes that makes all fields optional and replaces defaults
    with None.
    """

    @classmethod
    def __pydantic_init_subclass__(cls, **kwargs: Any) -> None:
        super().__pydantic_init_subclass__(**kwargs)

        for field in cls.model_fields.values():
            field.default = None
            field.annotation = Optional[field.annotation]

        cls.model_rebuild(force=True)


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
    """Set the title of a schema class to its name.
    This is used to make the schema title match the model name in the OpenAPI schema.
    """
    schema_cls.model_config["title"] = schema_cls.__name__


def get_writable_inputs(
    schema_obj: BaseSchema, schema_cls: type[pydantic.BaseModel] | None = None
) -> dict[str, Any]:
    """
    Return a dictionary of field_name: value pairs for writable input fields.

    Filters out:
    - ReadOnly fields
    - fields not provided with input (using Pydantic model_fields_set)

    Args:
        schema_obj: The schema object to extract writable fields from
        schema_cls: The schema class to check for readonly fields. If None, uses schema_obj.__class__

    Returns:
        Dictionary mapping field names to their values for writable input fields only
    """
    if schema_cls is None:
        schema_cls = schema_obj.__class__

    updated_fields: dict[str, Any] = {}
    for field_name, value in schema_obj:
        if field_name not in schema_obj.model_fields_set:
            continue
        # Skip readonly fields
        if is_readonly_field(schema_cls, field_name):
            continue
        updated_fields[field_name] = value

    return updated_fields
