"""Numbers -> inline SVG strings for the category-trend report. **PURO** — no dependency
beyond the stdlib, no state, one function per shape in
``docs/superpowers/specs/2026-08-16-relatorio-categoria-tendencias-design.md``'s table.

Print-first, not screen-first: no hover, no JS, no animation. Colors inherit
``analysis.scouting_report.REPORT_CSS``'s two-hue vocabulary (warm ``ACCENT`` = the category's
own signal, cool ``REFERENCE`` = the baseline / a reference line) so a reader never needs a
third color to know which side of a comparison they're looking at. Every generator returns a
string containing ``<svg`` and never ``NaN`` or ``Infinity`` — ``_safe`` clamps any non-finite
input to 0 before it reaches a coordinate.
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from html import escape

INK = "oklch(0.24 0.018 52)"
MUTED = "oklch(0.55 0.02 60)"
RULE = "oklch(0.82 0.02 74)"
PAPER = "oklch(0.965 0.008 86)"
ACCENT = "oklch(0.42 0.09 34)"          # warm — the category's own signal
REFERENCE = "oklch(0.5 0.1 250)"        # cool — baseline / reference line
FONT = "Avenir Next, Liberation Sans, sans-serif"


def _safe(value: float) -> float:
    return value if math.isfinite(value) else 0.0


def _clamp01(value: float) -> float:
    return max(0.0, min(1.0, _safe(value)))


def _svg(width: float, height: float, body: str) -> str:
    return (
        f'<svg viewBox="0 0 {width:.1f} {height:.1f}" width="{width:.0f}" height="{height:.0f}" '
        f'xmlns="http://www.w3.org/2000/svg" role="img" font-family="{FONT}">{body}</svg>'
    )


def _text(x: float, y: float, value: str, *, size: float = 11, fill: str = INK,
          anchor: str = "start", weight: str = "normal") -> str:
    return (f'<text x="{x:.1f}" y="{y:.1f}" font-size="{size}" fill="{fill}" '
            f'text-anchor="{anchor}" font-weight="{weight}">{escape(value)}</text>')


# ── concentration: single stacked bar ────────────────────────────────────────────────────

def stacked_bar_single(
    top1_share: float, top1_label: str, *, width: int = 560, height: int = 46,
) -> str:
    """One horizontal bar split top-1 athlete vs. the rest of the category — dominance
    readable in one glance, before any other section."""
    share = _clamp01(top1_share)
    bar_y, bar_h = 18, 18
    top1_w = share * width
    parts = [
        f'<rect x="0" y="{bar_y}" width="{width}" height="{bar_h}" fill="{PAPER}" '
        f'stroke="{RULE}"/>',
        f'<rect x="0" y="{bar_y}" width="{top1_w:.1f}" height="{bar_h}" fill="{ACCENT}"/>',
        _text(0, bar_y - 6, f"{escape(top1_label)}: {share:.0%} dos eventos próprios",
              size=12, weight="600"),
        _text(width, bar_y + bar_h + 14, "resto da categoria", size=10, fill=MUTED, anchor="end"),
    ]
    return _svg(width, height, "".join(parts))


# ── profile: 3-reading grouped bars + baseline ticks ─────────────────────────────────────

def profile_bars(
    rows: Sequence[str],
    category: dict[str, dict[str, float]],
    baseline: dict[str, dict[str, float]] | None = None, *,
    width: int = 620, row_h: int = 46, label_w: int = 150,
) -> str:
    """One row per ``key`` (event type / label) in ``rows``, three thin bars — peso_igual
    (PRIMARY), ponderado_por_evento (SECONDARY), leave_one_out (robustness, no baseline tick) —
    plus a baseline tick over the PRIMARY and SECONDARY bars from a same-shaped ``baseline``
    profile (``None`` when there is no baseline, e.g. ``--sem-banco``)."""
    plot_w = max(1, width - label_w - 10)
    series = ("peso_igual", "ponderado_por_evento", "leave_one_out")
    series_label = {"peso_igual": "peso igual", "ponderado_por_evento": "ponderado por evento",
                    "leave_one_out": "leave-one-out"}
    bar_h = 10
    parts: list[str] = []
    y = 8
    for key in rows:
        parts.append(_text(0, y + 9, key, size=11))
        for i, series_key in enumerate(series):
            value = _clamp01(category.get(series_key, {}).get(key, 0.0))
            by = y + 2 + i * (bar_h + 2)
            opacity = 1.0 if series_key != "leave_one_out" else 0.55
            parts.append(
                f'<rect x="{label_w}" y="{by:.1f}" width="{(value * plot_w):.1f}" '
                f'height="{bar_h}" fill="{ACCENT}" fill-opacity="{opacity}"/>'
            )
            if baseline is not None and series_key in baseline:
                base_value = _clamp01(baseline[series_key].get(key, 0.0))
                bx = label_w + base_value * plot_w
                parts.append(
                    f'<line x1="{bx:.1f}" y1="{by - 1:.1f}" x2="{bx:.1f}" '
                    f'y2="{by + bar_h + 1:.1f}" stroke="{REFERENCE}" stroke-width="2"/>'
                )
        y += row_h
    legend_y = y + 4
    parts.append(_text(label_w, legend_y, " / ".join(series_label.values()) + " · traço = baseline",
                       size=9, fill=MUTED))
    return _svg(width, legend_y + 12, "".join(parts))


# ── distribution: ranked horizontal bars ──────────────────────────────────────────────────

def ranked_bars(
    rows: Sequence[tuple[str, int]], *, width: int = 620, row_h: int = 22, label_w: int = 220,
) -> str:
    """Descending horizontal bars, ``n`` printed at the tip of each — a ranking, not a share."""
    if not rows:
        return _svg(width, 20, _text(0, 14, "sem dados", fill=MUTED))
    max_n = max(n for _, n in rows) or 1
    plot_w = max(1, width - label_w - 50)
    parts: list[str] = []
    y = 6
    for label, n in rows:
        bar_w = _clamp01(n / max_n) * plot_w
        parts.append(_text(0, y + row_h * 0.65, label, size=11))
        parts.append(f'<rect x="{label_w}" y="{y + 3}" width="{bar_w:.1f}" '
                     f'height="{row_h - 8}" fill="{ACCENT}"/>')
        parts.append(_text(label_w + bar_w + 6, y + row_h * 0.65, str(n), size=10, fill=MUTED))
        y += row_h
    return _svg(width, y, "".join(parts))


# ── desvio/metagame: divergent lollipop centered on 0 ────────────────────────────────────

def divergent_lollipop(
    rows: Sequence[tuple[str, float, str | None]], *,
    width: int = 620, row_h: int = 22, label_w: int = 190, max_abs: float | None = None,
) -> str:
    """One stem per label from the zero line to its ``log2_lift`` — sign is the message, so the
    axis is centered, not zero-based. Filled circle when ``estabilidade`` is "alta", hollow when
    "baixa" or unknown — magnitude alone never outranks stability in the reading."""
    if not rows:
        return _svg(width, 20, _text(0, 14, "sem dados", fill=MUTED))
    bound = max_abs if max_abs is not None else max((abs(lift) for _, lift, _ in rows), default=1.0)
    bound = bound or 1.0
    plot_w = max(1, width - label_w - 10)
    center_x = label_w + plot_w / 2
    scale = (plot_w / 2) / bound
    parts = [f'<line x1="{center_x:.1f}" y1="0" x2="{center_x:.1f}" y2="{len(rows) * row_h}" '
             f'stroke="{RULE}" stroke-width="1"/>']
    y = 6
    for label, lift, estabilidade in rows:
        lift = _safe(lift)
        x = center_x + lift * scale
        color = ACCENT if lift >= 0 else REFERENCE
        filled = estabilidade == "alta"
        parts.append(_text(0, y + row_h * 0.55, label, size=11))
        parts.append(f'<line x1="{center_x:.1f}" y1="{y + row_h / 2:.1f}" x2="{x:.1f}" '
                     f'y2="{y + row_h / 2:.1f}" stroke="{color}" stroke-width="2"/>')
        fill = color if filled else PAPER
        parts.append(f'<circle cx="{x:.1f}" cy="{y + row_h / 2:.1f}" r="4.5" fill="{fill}" '
                     f'stroke="{color}" stroke-width="1.5"/>')
        y += row_h
    return _svg(width, y, "".join(parts))


# ── reward-risk scatter with baseline crosshair ───────────────────────────────────────────

def scatter_reward_risk(
    points: Sequence[tuple[str, float, float, int]],
    baseline_point: tuple[float, float] | None = None, *,
    width: int = 480, height: int = 420, pad: int = 44,
) -> str:
    """Category positions as (risk, reward) points, sized by occurrence — a dashed crosshair at
    the baseline's own (risk, reward) is the "linha do baseline" reference, not a regression
    line (there is no relationship being fit here, just two references)."""
    plot_w, plot_h = width - pad * 2, height - pad * 2
    parts = [
        f'<rect x="{pad}" y="{pad}" width="{plot_w}" height="{plot_h}" fill="none" '
        f'stroke="{RULE}"/>',
        _text(pad, height - 8, "risco →", size=10, fill=MUTED),
        _text(10, pad, "recompensa", size=10, fill=MUTED),
    ]
    if baseline_point is not None:
        bx = pad + _clamp01(baseline_point[0]) * plot_w
        by = pad + plot_h - _clamp01(baseline_point[1]) * plot_h
        parts.append(f'<line x1="{bx:.1f}" y1="{pad}" x2="{bx:.1f}" y2="{pad + plot_h}" '
                     f'stroke="{REFERENCE}" stroke-width="1.5" stroke-dasharray="4 3"/>')
        parts.append(f'<line x1="{pad}" y1="{by:.1f}" x2="{pad + plot_w}" y2="{by:.1f}" '
                     f'stroke="{REFERENCE}" stroke-width="1.5" stroke-dasharray="4 3"/>')
    max_occ = max((occ for _, _, _, occ in points), default=1) or 1
    for label, risk, reward, occ in points:
        x = pad + _clamp01(risk) * plot_w
        y = pad + plot_h - _clamp01(reward) * plot_h
        r = 3 + 6 * _clamp01(occ / max_occ)
        parts.append(f'<circle cx="{x:.1f}" cy="{y:.1f}" r="{r:.1f}" fill="{ACCENT}" '
                    f'fill-opacity="0.75"/>')
        parts.append(_text(x + r + 3, y + 3, label, size=9))
    return _svg(width, height, "".join(parts))


# ── inline bar (table-cell primitive: transitions, coverage) ─────────────────────────────

def inline_bar(value: float, max_value: float, *, width: int = 80, height: int = 10) -> str:
    """A single small bar for embedding inside an HTML table cell (section 6's "tabela com
    barra embutida" and section 9's coverage table) — 12 legible table rows beat a hairball."""
    share = _clamp01(value / max_value if max_value else 0.0)
    bar_w = share * width
    return _svg(
        width, height,
        f'<rect x="0" y="0" width="{width}" height="{height}" fill="{PAPER}" stroke="{RULE}"/>'
        f'<rect x="0" y="0" width="{bar_w:.1f}" height="{height}" fill="{ACCENT}"/>',
    )
