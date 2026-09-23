Query API
=========

``fastapi_restly.query`` implements list-endpoint filtering, sorting, and
pagination: ``create_list_params_schema()`` derives the URL parameter schema
from a response schema and its model, and ``apply_list_params()`` applies
validated parameters to a SQLAlchemy select.

.. automodule:: fastapi_restly.query
   :members:
   :undoc-members:
   :show-inheritance:

.. seealso::

   :doc:`/howto_query_modifiers` documents the URL filter, sort, and
   pagination grammar that these helpers implement.
