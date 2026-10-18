Views API
=========

``fastapi_restly.views`` implements the class-based view layer. ``RestView``
and ``AsyncRestView`` define CRUD endpoint methods for a model. ``View`` is the
bare primitive for hand-written endpoint groups. ``include_view()`` registers
either on a FastAPI app.

.. automodule:: fastapi_restly.views
   :members:
   :undoc-members:
   :show-inheritance:

Markers for the view's own types
--------------------------------

A route names the classes of its view with these markers. When the view
registers, Restly puts the view's class in place of each marker, so a route
on a base view gets the classes of each subclass. A type checker reads each
marker as ``Any``. See :ref:`name-the-response-classes`.

.. py:data:: ViewID

   The view's id type: :attr:`~fastapi_restly.views.BaseRestView.id_type`,
   else the type of the model's primary key. A path parameter with this type
   gets the view's path converter: ``{id:int}`` for ``int`` and
   ``{id:uuid}`` for ``UUID``.

.. py:data:: ViewResponse

   The view's response class,
   :attr:`~fastapi_restly.views.BaseRestView.schema_response`.

.. py:data:: ViewListResponse

   The view's list response: the envelope of the view's pagination, with
   items of the view's response class.

.. py:data:: ViewCreate

   The view's create body,
   :attr:`~fastapi_restly.views.BaseRestView.schema_create`.

.. py:data:: ViewUpdate

   The view's update body,
   :attr:`~fastapi_restly.views.BaseRestView.schema_update`.

.. seealso::

   :doc:`/views` introduces the class-based view concept and
   hierarchy, and :doc:`/lifecycle` explains how a view handles a request
   and collects the task-shaped override recipes.
