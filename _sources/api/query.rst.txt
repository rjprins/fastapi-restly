Query API
=========

``fastapi_restly.query`` implements list-endpoint filtering, sorting, and
pagination: ``derive_schema_list_params()`` derives the list params, a model
of the URL parameters, from the view's response class and its model, and
``apply_list_params()`` applies validated list params to a SQLAlchemy select.
For list params of your own, ``PagedListParams`` gives the page fields of
the view's pagination, ``FilterKey`` and ``SortKey`` find the field and the
column of one key, and ``order_by_primary_key()`` orders by the primary key
last; see :ref:`own-list-params` and :ref:`own-list-params-keys`.

The view classes call the two functions for you. Call them yourself only in
a custom endpoint that needs the same list params, and pass validated list
params, not raw ``QueryParams``. Pass the view's settings to
``derive_schema_list_params()``: ``pagination=self.pagination``, and
``include_filters``, ``exclude_filters``, ``include_sort_keys``,
``exclude_sort_keys``, ``sort_query_param`` and ``default_sort``.
``apply_list_params()`` takes ``(stmt, list_params, model, schema_response)``,
in the same order as the view method ``apply_list_params(stmt, list_params)``,
and the view's ``sort_nulls=self.sort_nulls``.
Pass the view's ``self.schema_response``, not ``self.schema``: the list params
follow the response class.

.. automodule:: fastapi_restly.query
   :members:
   :undoc-members:
   :show-inheritance:

.. seealso::

   :doc:`/filtering` documents the list params that these helpers build
   and apply: filter, sort and pagination.
