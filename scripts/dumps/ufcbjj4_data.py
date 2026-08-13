"""UFC BJJ 4 — refined from transcript."""
# ruff: noqa: E501
from __future__ import annotations

from typing import Any

RAW: list[dict[tuple[str, int], dict[str, Any]]] = [{('Aurelie Le Vern', 2025): {'method': 'Submission (Kimura)',
                              'start': '0:00',
                              'opponent': 'Raquel Canuto',
                              'event': 'UFC BJJ 4',
                              'weight_class': "Women's Featherweight",
                              'stage': '',
                              'winner': 'Aurelie Le Vern',
                              'win_type': 'SUBMISSION',
                              'submission': 'Kimura',
                              'events': [{'label': 'Inverted Triangle',
                                          'type': 'submission',
                                          'actor': 'Aurelie Le Vern',
                                          'ts': 227,
                                          'successful': False},
                                         {'label': 'Kimura',
                                          'type': 'submission',
                                          'actor': 'Aurelie Le Vern',
                                          'ts': 237,
                                          'successful': True}],
                              'scouting_observations': [],
                              'timing': {'end_ts': 298},
                              'adjudication': {'status': 'unknown', 'kind': 'none'},
                              'timing_basis': 'video_absolute',
                              'bout_start_s': 0,
                              'duration_s': 298}}]
