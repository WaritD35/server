"""``api`` — MVCS HTTP surface for the Pet-Plant web client.

Owns the ``/companion`` routes consumed by the UI.  Internal bounded contexts
(``assessment``, ``advice``, ``companion``, ``registry``, ``action``) are
accessed exclusively through their published interfaces; the ``api`` layer
never imports ORM models from other contexts.

Architecture: Controller → Service → Repository → Model
"""
