# video_engine

Turns a match breakdown plus a human editorial recording into a validated video DSL, then a
deterministic storyboard. **The agent chooses what story to tell, never how pixels get drawn.**

## Status

| layer | state |
|---|---|
| `contracts.py` — the DSL: beats, claims, visual specs | built, tested |
| `facts.py` — FACT / DERIVED / ANALYST resolution | built, tested |
| `validate.py` — the gate before any renderer | built, tested |
| `storyboard.py` — beats → timed cues + cache keys | built, tested |
| `style/tokens.py` — one theme for all renderers | built |
| `render/manim/*`, `render/vizzu/*`, `render/moviepy_compositor.py` | **not built** — see below |

The renderers are unbuilt for a stated reason, not an oversight: this machine has 1.3 GB of
free disk and ManimGL, Chromium (Playwright) and MoviePy do not fit. The `video` extra in
`pyproject.toml` declares them; installing it is the next step and needs disk first.

The half that is built is the half the architecture rests on. An agent can already be handed
a fact package and produce a `video_brief.json`, and `validate()` will refuse it if any
objective sentence has nothing behind it — which is the failure that makes generated video
untrustworthy.

## The rule

Three claim classes, and deliberately no fourth:

    FACT     present in match_breakdown.json at `path`
    DERIVED  a deterministic formula over facts, evaluated by facts.evaluate()
    ANALYST  stated in the human's editorial recording, cited by segment id

"I think she was dominating the wrestling" is an ANALYST claim if the analyst said it, and is
nothing at all otherwise. It can never become "she won 80% of the wrestling exchanges".

`facts.evaluate` accepts fact paths, `+ - * /`, parentheses and numbers. Nothing else — no
names, no calls, no attribute access. An agent-authored string cannot execute anything.

## One data truth

`build_match_breakdown()` already computes the ordered sequence, per-fighter stats, momentum,
the transition graph and decision space for the site page. This engine reads that. It does not
recompute any of it: a second implementation drifts, and nobody notices until the page and the
video disagree on screen.

## Determinism

`compile_storyboard` gives every scene a content hash over renderer version, kind, inputs and
theme version. Change the outro and only the outro re-renders. That is what makes a
code-assisted loop usable rather than a five-minute wait per edit.

## Next

1. Disk, then `uv sync --extra video`.
2. `render/moviepy_compositor.py` plus three Manim scenes (hook, sequence, conclusion) —
   enough for one real 30–45s reel from an existing breakdown, no agent involved.
3. Vizzu behind `VizzuRenderer.render(scene) -> Path`, so nothing else learns Chromium exists.
4. Only then the agent authoring step, which outputs a `VideoBrief` and nothing else.
