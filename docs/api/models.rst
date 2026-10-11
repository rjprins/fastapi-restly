Models API
==========

``fastapi_restly.models`` provides ``ModelDefaults``, which adds Restly's
defaults to your own declarative base, and the model mixins ``IDMixin`` and
``TimestampsMixin``. :doc:`/howto_models` shows how to use them.

.. automodule:: fastapi_restly.models
   :members:
   :undoc-members:
   :show-inheritance:
   :exclude-members: utc_now, underscore, metadata, CASCADE_ALL_ASYNC, CASCADE_ALL_DELETE_ORPHAN_ASYNC, TableNameMixin

``fastapi_restly.models`` also provides two cascade strings for
``relationship(cascade=...)`` on async sessions. ``CASCADE_ALL_ASYNC`` is
``"save-update, merge, delete, expunge"``, and
``CASCADE_ALL_DELETE_ORPHAN_ASYNC`` adds ``"delete-orphan"``. SQLAlchemy's
``"all"`` also includes ``"refresh-expire"``: then a plain
``session.refresh(obj)`` expires the related objects, and they lazy-load on the
next access, which fails on an async session. Restly's own ``save_object``
refreshes by attribute name and does not cascade, so ``"all"`` is safe on
Restly's write paths. Use these constants where your own code refreshes an
object on an async session.

.. seealso::

   :doc:`/howto_models` shows how to add Restly's defaults to your own
   declarative base and how to change one. :doc:`/howto_existing_project`
   shows Restly views on the models you already have.
