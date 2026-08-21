Clauses API
===========

``fastapi_restly.clauses`` implements composable, context-bound query
clauses: named fragments of a query, declared once at module level,
composed with boolean functions, and applied to plain SQLAlchemy
statements. Every symbol here is also importable from the top-level
``fastapi_restly`` namespace.

.. automodule:: fastapi_restly.clauses
   :members:
   :undoc-members:
   :show-inheritance:
   :special-members: __call__

.. seealso::

   :doc:`/clauses` explains the full interface with worked examples.
