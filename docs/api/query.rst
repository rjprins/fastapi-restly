Query API
=========

``fastapi_restly.query`` implements list-endpoint filtering, sorting, and
pagination: ``derive_schema_list_params()`` derives the list params, a model
of the URL parameters, from the view's response class and its model, and
``apply_list_params()`` applies validated list params to a SQLAlchemy select.

The view classes call these for you. Call them yourself only in a custom
endpoint that needs the same list grammar, and pass validated list params, not
raw ``QueryParams``. Pass the view's settings to
``derive_schema_list_params()``: ``pagination=self.pagination``, and
``include_filters``, ``exclude_filters``, ``include_sort_keys`` and
``exclude_sort_keys``.
``apply_list_params()`` takes ``(query, list_params, model, schema_response)``,
in the same order as the view method ``apply_list_params(query, list_params)``.
Pass the view's ``self.schema_response``, not ``self.schema``: the list params
follow the response class.

.. automodule:: fastapi_restly.query
   :members:
   :undoc-members:
   :show-inheritance:

.. seealso::

   :doc:`/filtering` documents the URL filter, sort, and
   pagination grammar that these helpers implement.
