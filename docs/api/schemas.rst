Schemas API
===========

``fastapi_restly.schemas`` implements the Pydantic schema layer: schema base
classes, the ``ReadOnly`` and ``WriteOnly`` field markers, relationship
reference types, and schema generation from SQLAlchemy models.

.. seealso::

   :doc:`/relationships` covers ``MustExist`` checked foreign
   keys and the JSON format of ``IDRef`` / ``IDSchema`` relationship fields.
   :doc:`/schemas` describes schema bases, field markers, and
   aliases in prose.

.. py:data:: fastapi_restly.schemas.ReadOnly

   Marks a field as excluded from generated create and update schemas.
   The field remains available in responses.

.. py:data:: fastapi_restly.schemas.WriteOnly

   Marks a field as input-only by setting Pydantic's serialization exclusion.
   See :ref:`json-document-columns` for restrictions inside JSON documents.

.. automodule:: fastapi_restly.schemas
   :members:
   :undoc-members:
   :show-inheritance:
   :exclude-members: readonly_marker, writeonly_marker, getattrs, rebase_with_model_config, set_schema_title, SQLAlchemyModel, get_read_only_fields, get_write_only_fields, create_model_with_optional_fields, create_model_without_read_only_fields, _async_resolve_ids_to_sqlalchemy_objects, convert_sqlalchemy_type_to_pydantic, get_model_fields, get_relationship_target_model, get_sqlalchemy_field_type, is_relationship_field
