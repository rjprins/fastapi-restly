Query API
=========

``fastapi_restly.query`` implements list-endpoint filtering, sorting, and
pagination: ``derive_schema_list_params()`` derives the list params, a model
of the URL parameters, from the view's response class and its model, and
``apply_list_params()`` applies validated list params to a SQLAlchemy select.

.. automodule:: fastapi_restly.query
   :members:
   :undoc-members:
   :show-inheritance:

.. seealso::

   :doc:`/howto_query_modifiers` documents the URL filter, sort, and
   pagination grammar that these helpers implement.
