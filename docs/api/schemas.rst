Schemas API
===========

``fastapi_restly.schemas`` implements the Pydantic schema layer: schema base
classes, the ``ReadOnly`` and ``WriteOnly`` field markers, relationship
reference types, and schema generation from SQLAlchemy models.

.. seealso::

   :doc:`/howto_relationship_idschema` covers ``MustExist`` checked foreign
   keys and the ``IDRef`` / ``IDSchema`` relationship wire contract.
   :doc:`/howto_custom_schema` describes schema bases, field markers, and
   aliases in prose.

.. automodule:: fastapi_restly.schemas
   :members:
   :undoc-members:
   :show-inheritance:
   :exclude-members: readonly_marker, writeonly_marker, getattrs, rebase_with_model_config, set_schema_title, SQLAlchemyModel, get_read_only_fields, get_write_only_fields, create_model_with_optional_fields, create_model_without_read_only_fields, _async_resolve_ids_to_sqlalchemy_objects, convert_sqlalchemy_type_to_pydantic, get_model_fields, get_relationship_target_model, get_sqlalchemy_field_type, is_relationship_field
