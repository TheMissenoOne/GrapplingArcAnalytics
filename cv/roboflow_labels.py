"""Map Roboflow ``bjj3`` class names to the ViCoS ``"{position}_{role}"`` form.

The bjj3 object-detection model emits classes like ``mount1`` / ``side_control2`` /
``5050_guard`` / ``standing``. Per the ViCoS-lineage paper (Hudovernik & Skočaj,
MMSports'22) the trailing ``1``/``2`` distinguishes the **top/bottom** athlete in all
positions except ``5050_guard`` and ``standing``. Converting to the ViCoS
``"{position}_{role}"`` string lets the existing :func:`cv.vocab_map.map_vicos_class`
parse position + role unchanged (it ``rsplit("_", 1)`` on the last underscore).
"""

from __future__ import annotations

import logging
import re

logger = logging.getLogger(__name__)

#: Default suffix → role map (1 = top, 2 = bottom), per the ViCoS paper.
_DEFAULT_ROLE_MAP = {1: "top", 2: "bottom"}

# A trailing 1/2 that marks a role only counts when preceded by a letter, so the
# leading digits of "5050_guard" are preserved.
_TRAILING_VARIANT = re.compile(r"^(.*[a-z])([12])$")


def roboflow_to_vicos(class_name: str, role_map: dict[int, str] | None = None) -> str:
    """Convert a Roboflow bjj3 class to a ViCoS ``"{position}_{role}"`` string.

    Parameters
    ----------
    class_name : str
        e.g. ``"mount1"``, ``"side_control2"``, ``"5050_guard"``, ``"standing"``.
    role_map : dict[int, str] or None
        Maps the trailing digit to a role. Defaults to ``{1: "top", 2: "bottom"}``.
        Pass ``{}`` (or any falsy map) to treat the suffix as a meaningless variant
        and drop it (position only, no role).

    Returns
    -------
    str
        ViCoS form: ``"mount_top"``, ``"side control_bottom"``, ``"open guard_top"``,
        ``"5050 guard"`` (no role), ``"standing"``. The position keeps no underscores
        (they become spaces), so ``_`` survives only as the position/role separator.
    """
    rmap = _DEFAULT_ROLE_MAP if role_map is None else role_map
    name = class_name.strip().lower()

    role = ""
    match = _TRAILING_VARIANT.match(name)
    if match:
        name = match.group(1)
        digit = int(match.group(2))
        role = rmap.get(digit, "")

    position = re.sub(r"\s+", " ", name.replace("_", " ")).strip()
    return f"{position}_{role}" if role else position
