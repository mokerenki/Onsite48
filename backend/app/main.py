"""FastAPI application entrypoint for Onsite48.

Implementation lives in ``app.main_impl`` so the module stays review-sized.
Re-exports ``app`` and ``lifespan`` expected by the worker and ASGI server.
"""
from app.main_impl import app, lifespan  # noqa: F401

__all__ = ["app", "lifespan"]
