Clauses API
===========

``fastapi_restly.clauses`` implements composable, context-bound query
clauses: named fragments of a query, declared once at module level,
composed with boolean functions, and applied to plain SQLAlchemy
statements.

.. automodule:: fastapi_restly.clauses
   :members:
   :undoc-members:
   :show-inheritance:
   :special-members: __call__

.. autodata:: fastapi_restly.clauses.UNSCOPED

.. seealso::

   :doc:`/howto_current` covers request values and dependency binding.
   :doc:`/clauses` covers query fragments and composition.
