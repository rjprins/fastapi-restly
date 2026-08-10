Testing API
===========

``fastapi_restly.testing`` provides ``configure_tests()``, which adds schema and
isolation behavior to an application already configured for its test database,
and the synchronous and asynchronous status-asserting test clients,
``RestlyTestClient`` and ``AsyncRestlyTestClient``.

Install the optional testing dependencies before importing this module::

    pip install "fastapi-restly[testing]"

The testing extra installs a pytest plugin entry point, so pytest auto-loads the
fixtures. If your project disables plugin autoloading, add the following line to
your ``conftest.py``::

    pytest_plugins = ["fastapi_restly.pytest_fixtures"]

This imports the namespaced fixtures (``restly_app``, ``restly_client``,
``restly_async_client``, ``restly_session``, ``restly_async_session``, etc.)
into your test session without needing to import them individually.

.. automodule:: fastapi_restly.testing
   :members:
   :undoc-members:
   :show-inheritance:

.. seealso::

   :doc:`/howto_testing` covers the setup, the cleanup modes, the fixture
   reference, and how the rollback works.
