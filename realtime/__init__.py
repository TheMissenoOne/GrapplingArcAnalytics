"""Realtime CV backend — FastAPI service wiring the cv/ building blocks.

Exposes pose classification + stream segmentation over HTTP for the web app
(live overlay + post-hoc video review). See ``docs/realtime_cv_design.md``.
"""

from .app import create_app

__all__ = ["create_app"]
