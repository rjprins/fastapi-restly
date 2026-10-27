Exceptions API
==============

``fastapi_restly.exc`` defines the public exception hierarchy: configuration
errors raised when the framework is misused at setup, and request-time HTTP
errors that subclass ``fastapi.HTTPException``.

.. automodule:: fastapi_restly.exc
   :members:
   :undoc-members:
   :show-inheritance:

.. seealso::

   :doc:`/errors` explains which exception to raise where
   and how to change the error envelope app-wide.
