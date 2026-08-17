"""Rating Engine V2 (Glicko-2, shadow) — see docs/rating_v2/.

``glicko2.py`` is pure math (no DB/file/network import — enforced by tests). ``replay.py``
is the only module in this package that touches the database, and it is read-only.
"""
