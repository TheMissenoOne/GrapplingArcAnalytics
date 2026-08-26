"""Dossier career-graph video player must seek in place for same-video clicks and only
re-embed the iframe when the target is a genuinely different YouTube id — a fresh iframe/src
swap re-triggers YouTube's pre-roll ad. Full branch-logic behaviour is exercised with a small
Node harness in scratch during development; this test guards the emitted JS stays wired that
way without adding a Node dependency to CI."""

from __future__ import annotations

from export import site_data


def test_gawatch_seeks_same_video_and_only_reembeds_on_video_change() -> None:
    js = site_data._PROFILE_JS

    # same-video branch: compares the requested id against the currently loaded one and
    # calls the YT API's seekTo instead of touching innerHTML
    assert "ref.vid===gaDsVid&&gaDsPlayer" in js
    assert "gaDsSeek(start)" in js
    assert "gaDsPlayer.seekTo(t,true)" in js

    # the branch that rebuilds the embed only runs when the above comparison falls through,
    # and the iframe it writes carries enablejsapi so future clicks can seek it via postMessage
    same_video_branch = js.index("ref.vid===gaDsVid")
    rebuild = js.index("wrap.innerHTML=")
    assert same_video_branch < rebuild, "same-video seek must be checked before any re-embed"
    assert "enablejsapi=1" in js
    assert 'id="dsFrame"' in js
