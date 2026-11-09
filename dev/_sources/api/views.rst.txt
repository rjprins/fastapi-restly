Views API
=========

``fastapi_restly.views`` implements the class-based view layer. ``RestView``
and ``AsyncRestView`` define CRUD endpoint methods for a model. ``View`` is the
bare primitive for hand-written endpoint groups. ``include_view()`` registers
either on a FastAPI app.

.. automodule:: fastapi_restly.views
   :members:
   :undoc-members:
   :show-inheritance:
   :exclude-members: IDType, ResponseSchema, ListResponseSchema, CreateSchema, UpdateSchema

Markers for the view's own types
--------------------------------

A route names the types of its view with these markers. When the view
registers, Restly puts the view's own type in place of each marker, so a route
on a base view gets the types of each subclass. A type checker reads each
marker as ``Any``. See :ref:`name-the-view-schemas`. To build another type
for each view, use :class:`~fastapi_restly.views.SchemaType`; see
:ref:`schema-type`.

.. py:data:: IDType

   The view's id type: :attr:`~fastapi_restly.views.BaseRestView.id_type`,
   else the type of the model's primary key. A path parameter with this type
   gets the view's path converter: ``{id:int}`` for ``int`` and
   ``{id:uuid}`` for ``UUID``.

.. py:data:: ResponseSchema

   The view's response schema,
   :attr:`~fastapi_restly.views.BaseRestView.response_schema`.

.. py:data:: ListResponseSchema

   The view's list response,
   :attr:`~fastapi_restly.views.BaseRestView.list_response_schema`. Unless the
   view sets another class, this is the envelope of the view's pagination, with
   items of the view's response schema.

.. py:data:: CreateSchema

   The view's create schema,
   :attr:`~fastapi_restly.views.BaseRestView.create_schema`.

.. py:data:: UpdateSchema

   The view's update schema,
   :attr:`~fastapi_restly.views.BaseRestView.update_schema`.

.. seealso::

   :doc:`/views` introduces the class-based view concept and
   hierarchy, and :doc:`/crud_routes` explains how a CRUD route runs. The
   page of each route, such as :doc:`/crud_update`, shows what you can
   change.
