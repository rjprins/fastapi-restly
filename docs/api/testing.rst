Testing API
===========

``fastapi_restly.testing`` provides ``RestlyTestClient`` and the
savepoint-only mode switches for writing isolated tests against a Restly
application.

Install the optional testing dependencies before importing this module::

    pip install "fastapi-restly[testing]"

The testing extra installs a pytest plugin entry point, so pytest auto-loads the
fixtures. If your project disables plugin autoloading, add the following line to
your ``conftest.py``::

    pytest_plugins = ["fastapi_restly.pytest_fixtures"]

This imports the namespaced fixtures (``restly_app``, ``restly_client``,
``restly_session``, ``restly_async_session``, etc.) into your test session
without needing to import them individually.

.. automodule:: fastapi_restly.testing
   :members:
   :undoc-members:
   :show-inheritance:

.. seealso::

   :doc:`/howto_testing` provides the conftest recipe, the fixture
   reference, and the savepoint isolation model.
