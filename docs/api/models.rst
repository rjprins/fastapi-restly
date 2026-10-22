Models API
==========

``fastapi_restly.models`` provides ``ModelDefaults``, which adds Restly's
defaults to your own declarative base, and the model mixins ``IDMixin`` and
``TimestampsMixin``. :doc:`/models` shows how to use them.

.. automodule:: fastapi_restly.models
   :members:
   :undoc-members:
   :show-inheritance:
   :exclude-members: utc_now, underscore, metadata, TableNameMixin

.. seealso::

   :doc:`/models` shows how to add Restly's defaults to your own
   declarative base and how to change one. :ref:`existing-models`
   shows Restly views on the models you already have.
