"""ASGI entrypoint — serve the realtime CV backend with the Roboflow bjj3 backend.

Run::

    # put ROBOFLOW_API_KEY in .env (gitignored)
    uv run --extra realtime uvicorn realtime.server:app --port 8000

When ``ROBOFLOW_API_KEY`` is set, ``/classify`` calls the Roboflow detection model
(``$BJJ_MODEL_ID``, default ``bjj3/1``) over the hosted serverless HTTP API (no heavy
SDK). Without a key it falls back to the pose+sklearn backend. An optional Qdrant store
is attached when reachable, enabling the similar-athlete prior blend; if it can't be
built, priors degrade to own-history.
"""

from __future__ import annotations

import logging
import os
from typing import TYPE_CHECKING

from dotenv import load_dotenv
from fastapi import FastAPI

from cv.roboflow_classifier import RoboflowClassifier
from realtime.app import create_app

if TYPE_CHECKING:
    from analysis.vector_store import AthleteVectorStore

logger = logging.getLogger(__name__)

load_dotenv()

MODEL_ID = os.environ.get("BJJ_MODEL_ID", "bjj3/1")


def _maybe_store() -> AthleteVectorStore | None:
    """Build a Qdrant store if possible; None (graceful) otherwise."""
    try:
        from analysis.vector_store import AthleteVectorStore, build_label_vocab
        from cv.vocab_map import load_app_nodes

        vocab = build_label_vocab(load_app_nodes())
        if not vocab:
            return None
        return AthleteVectorStore(vocab, path=os.environ.get("QDRANT_PATH") or None)
    except Exception as exc:  # noqa: BLE001 — optional dependency / runtime, degrade gracefully
        logger.warning("Qdrant store unavailable, priors use own-history only: %s", exc)
        return None


def build_app() -> FastAPI:
    api_key = os.environ.get("ROBOFLOW_API_KEY")
    roboflow = RoboflowClassifier(MODEL_ID, api_key=api_key) if api_key else None
    if roboflow is None:
        logger.warning("ROBOFLOW_API_KEY not set — /classify falls back to pose+sklearn.")
    return create_app(roboflow=roboflow, store=_maybe_store())


app = build_app()
