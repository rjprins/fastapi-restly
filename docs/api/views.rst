Views API
=========

``fastapi_restly.views`` implements the class-based view layer: ``RestView``
and ``AsyncRestView`` generate CRUD endpoints from a model, ``View`` is the
bare primitive for hand-written endpoint groups, and ``include_view()``
registers either on a FastAPI app.

.. automodule:: fastapi_restly.views
   :members:
   :undoc-members:
   :show-inheritance:
   :exclude-members: make_new_object, update_object, save_object, delete_object

.. seealso::

   :doc:`/class_based_views` introduces the class-based view concept and
   hierarchy, :doc:`/the_handle_design` explains the three-tier override
   model, and :doc:`/howto_override_endpoints` collects task-shaped
   override recipes.
