"""
JavaScript payload helper — shared utility for generating window.VAR = {...} globals.

Used by site_data.py, study_knowledge.py, and other exporters that need to write
generated JS data files for the static site.
"""

from __future__ import annotations

import json
from typing import Any


def js_global(var: str, data: Any) -> str:
    """Generate a JavaScript global assignment: window.VAR = {...};

    Args:
        var: Variable name (e.g., "GA_STUDIES", "GA_STUDY_KNOWLEDGE")
        data: Python object to serialize (lists, dicts, primitives)

    Returns:
        JavaScript source code as a string, ready to write to a .js file.

    Example:
        >>> js_global("GA_CONCEPTS", [{"key": "arm-drag", "label": "Arm Drag"}])
        '/* generated — do not edit */\\nwindow.GA_CONCEPTS = [...]\\n'
    """
    payload = json.dumps(data, ensure_ascii=False, separators=(",", ":"))
    return f"/* generated — do not edit */\nwindow.{var} = {payload};\n"
