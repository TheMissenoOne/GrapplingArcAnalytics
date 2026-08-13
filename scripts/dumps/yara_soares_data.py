"""Yara Soares 2021 singles (IBJJF Worlds semifinal, IBJJF Pans absolute final).

Own-bout compilation across two cards — no single event tag. Both bouts come from
their own standalone full-match upload, so ``start`` is ``0:00`` and ``ts`` is the
timestamp of that video.

The Pans opponent is announced as "Anna Carolina Vieira" in the broadcast; she is
recorded here under the canonical spelling ``Ana Carolina Vieira`` so the bout folds
into the existing athlete instead of creating a name variant.
"""
# ruff: noqa: E501
from __future__ import annotations

from typing import Any

RAW: list[dict[tuple[str, int], dict[str, Any]]] = [
    {('Yara Soares', 2021): {'method': 'Submission (Choke)',
                             'start': '0:00',
                             'opponent': 'Gabi Garcia',
                             'event': 'IBJJF Worlds 2021',
                             'weight_class': '',
                             'stage': 'Semifinal',
                             'winner': 'Yara Soares',
                             'win_type': 'SUBMISSION',
                             'submission': 'Choke',
                             'events': [{'label': 'Weave Pass',
                                         'type': 'pass',
                                         'actor': 'Yara Soares',
                                         'ts': 319,
                                         'successful': False},
                                        {'label': 'Choke',
                                         'type': 'submission',
                                         'actor': 'Yara Soares',
                                         'ts': 479,
                                         'successful': True}]}},
    {('Yara Soares', 2021): {'method': 'Decision',
                             'start': '0:00',
                             'opponent': 'Ana Carolina Vieira',
                             'event': 'IBJJF Pans 2021',
                             'weight_class': 'Absolute',
                             'stage': 'Final',
                             'winner': 'Yara Soares',
                             'win_type': 'DECISION',
                             'submission': None,
                             'events': [{'label': 'Guard Pass',
                                         'type': 'pass',
                                         'actor': 'Ana Carolina Vieira',
                                         'ts': 39,
                                         'successful': False},
                                        {'label': 'Lasso Guard',
                                         'type': 'guard',
                                         'actor': 'Yara Soares',
                                         'ts': 39},
                                        {'label': 'De la Riva Guard',
                                         'type': 'guard',
                                         'actor': 'Yara Soares',
                                         'ts': 376},
                                        {'label': 'Sweep',
                                         'type': 'sweep',
                                         'actor': 'Yara Soares',
                                         'ts': 453,
                                         'successful': True},
                                        {'label': 'Smash Pass',
                                         'type': 'pass',
                                         'actor': 'Yara Soares',
                                         'ts': 468,
                                         'successful': False},
                                        {'label': 'Turtle Control',
                                         'type': 'control',
                                         'actor': 'Yara Soares',
                                         'ts': 477},
                                        {'label': 'Choke',
                                         'type': 'submission',
                                         'actor': 'Yara Soares',
                                         'ts': 616,
                                         'successful': False}]}},
]
