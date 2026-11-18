"""H. Box scores, team scores, self-tagging, event review and opt-out (FR-09, FR-10, SYS-14)."""

from __future__ import annotations

import pytest

from server.app.crud import stats
from server.app.crud.errors import InvalidReviewError, InvalidTagError, NotFoundError, NotPermittedError
from server.app.models import ScoringRule
from server.tests.db import factories as f
from server.tests.db.factories import Shot

UNTAGGED = 9
FLAGGED_SHOT = 6  # index into shot_ids


async def _fixture(db, scoring=ScoringRule.ONES_TWOS):
    a1, a2, b1, b2 = [await f.user(db) for _ in range(4)]
    shots = [
        Shot(a1, True),                     # 0 inside make
        Shot(a1, True, beyond_arc=True),    # 1 three/two-pointer make
        Shot(a1, False),                    # 2 inside miss
        Shot(a2, True, confidence=0.8),     # 3
        Shot(b1, True, beyond_arc=True),    # 4
        Shot(b1, False, beyond_arc=True),   # 5
        Shot(b1, True, confidence=0.3),     # 6 flagged (below 0.6): not counted yet
        Shot(UNTAGGED, True),               # 7 untagged track, team B
    ]
    rg = await f.recorded_game(
        db,
        [(a1, "A"), (a2, "A"), (b1, "B")],
        shots,
        scoring=scoring,
        extra_tracks=[(UNTAGGED, "B")],
        roster_only=[b2],
        finalize=False,
    )
    return rg, a1, a2, b1, b2


def _lines(rows):
    return {r.user_id: (r.fga, r.fgm, r.two_pa, r.two_pm, r.three_pa, r.three_pm, r.points) for r in rows}


@pytest.mark.parametrize(
    "scoring, a1_pts, a2_pts, b1_pts, team_a, team_b",
    [(ScoringRule.ONES_TWOS, 3, 1, 2, 4, 3), (ScoringRule.TWOS_THREES, 5, 2, 3, 7, 5)],
    ids=["ones_twos", "twos_threes"],
)
async def test_box_scores_and_team_scores(db, scoring, a1_pts, a2_pts, b1_pts, team_a, team_b):
    rg, a1, a2, b1, b2 = await _fixture(db, scoring)
    lines = _lines(await stats.box_scores(db, rg.game.id))
    assert lines == {
        a1.id: (3, 2, 2, 1, 1, 1, a1_pts),
        a2.id: (1, 1, 1, 1, 0, 0, a2_pts),
        b1.id: (2, 1, 0, 0, 2, 1, b1_pts),  # the flagged make isn't counted
    }
    # Team score is the game score: it includes the untagged track's basket.
    assert await stats.team_scores(db, rg.game.id) == {"A": team_a, "B": team_b}


async def test_untagged_tracks_are_excluded(db):
    rg, a1, a2, b1, b2 = await _fixture(db)
    assert b2.id not in _lines(await stats.box_scores(db, rg.game.id))
    assert len(await stats.box_scores(db, rg.game.id)) == 3


async def test_retag_moves_stats_immediately(db):
    rg, a1, a2, b1, b2 = await _fixture(db)
    await stats.tag_track(db, rg.tracks[UNTAGGED], b2.id)
    assert _lines(await stats.box_scores(db, rg.game.id))[b2.id] == (1, 1, 1, 1, 0, 0, 1)
    # b1 realizes the untagged track is actually them? It's claimed now.
    with pytest.raises(InvalidTagError, match="claimed"):
        await stats.tag_track(db, rg.tracks[UNTAGGED], b1.id)
    # a2 moves their tag to a different track: stats follow the tag.
    await stats.untag_track(db, rg.tracks[UNTAGGED], b2.id)
    await stats.tag_track(db, rg.tracks[UNTAGGED], a2.id)
    lines = _lines(await stats.box_scores(db, rg.game.id))
    assert lines[a2.id] == (1, 1, 1, 1, 0, 0, 1)
    assert b2.id not in lines
    assert len(lines) == 3  # a2's old track is untagged now


async def test_untag_removes_player_from_stats(db):
    rg, a1, a2, b1, b2 = await _fixture(db)
    await stats.untag_track(db, rg.tracks[a1.id], a1.id)
    assert a1.id not in _lines(await stats.box_scores(db, rg.game.id))
    with pytest.raises(InvalidTagError):
        await stats.untag_track(db, rg.tracks[a2.id], a1.id)  # not their tag


async def test_opted_out_player_excluded_from_stats_and_clips(db):
    rg, a1, a2, b1, b2 = await _fixture(db)
    for idx in (0, 4, 7):
        await stats.add_highlight_clip(db, rg.shot_ids[idx], f"clips/{idx}.mp4", 4000)
    clip_owners = [uid for uid, _clip in await stats.highlight_clips(db, rg.game.id)]
    assert clip_owners == [a1.id, b1.id]  # the untagged track's clip belongs to nobody

    await stats.set_tracking_opt_out(db, rg.game.id, a1.id, True)
    assert a1.id not in _lines(await stats.box_scores(db, rg.game.id))
    assert [uid for uid, _clip in await stats.highlight_clips(db, rg.game.id)] == [b1.id]
    with pytest.raises(InvalidTagError, match="opted out"):
        await stats.tag_track(db, rg.tracks[UNTAGGED], a1.id)

    await stats.set_tracking_opt_out(db, rg.game.id, a1.id, False)
    assert a1.id in _lines(await stats.box_scores(db, rg.game.id))


async def test_flagged_event_counts_only_after_confirmation(db):
    rg, a1, a2, b1, b2 = await _fixture(db)
    flagged = rg.shot_ids[FLAGGED_SHOT]
    await stats.review_event(db, flagged, b1.id, confirm=True)
    assert _lines(await stats.box_scores(db, rg.game.id))[b1.id] == (3, 2, 1, 1, 2, 1, 3)
    await stats.review_event(db, flagged, a1.id, confirm=False)
    assert _lines(await stats.box_scores(db, rg.game.id))[b1.id] == (2, 1, 0, 0, 2, 1, 2)


async def test_review_rules(db):
    rg, a1, a2, b1, b2 = await _fixture(db)
    with pytest.raises(InvalidReviewError, match="flagged"):
        await stats.review_event(db, rg.shot_ids[0], a1.id, confirm=False)  # auto events need no review
    stranger = await f.user(db)
    with pytest.raises(InvalidReviewError, match="participants"):
        await stats.review_event(db, rg.shot_ids[FLAGGED_SHOT], stranger.id, confirm=True)


async def test_only_confirmed_participants_can_tag(db):
    rg, a1, a2, b1, b2 = await _fixture(db)
    stranger = await f.user(db)
    with pytest.raises(InvalidTagError, match="participants"):
        await stats.tag_track(db, rg.tracks[UNTAGGED], stranger.id)


async def test_cv_writes(db):
    rg, a1, a2, b1, b2 = await _fixture(db)
    with pytest.raises(NotFoundError):
        await stats.write_shot_events(db, rg.recording.id, [stats.ShotIn(42, 0, 0, 0, False, True, 0.9)])
    with pytest.raises(NotPermittedError):
        await stats.add_highlight_clip(db, rg.shot_ids[2], "miss.mp4", 3000)  # missed shot
    # Reprocessing replaces the previous run's tracks and shots.
    labels = await stats.write_tracks(db, rg.recording.id, [stats.TrackIn(0, "A")])
    assert list(labels) == [0]
    assert await stats.box_scores(db, rg.game.id) == []

    await stats.write_shot_events(db, rg.recording.id, [stats.ShotIn(0, 0, 0, 0, False, True, 0.9)])
    await stats.tag_track(db, labels[0], a1.id)
    await stats.finalize_game_stats(db, rg.game.id)
    with pytest.raises(NotPermittedError, match="finalized"):
        await stats.write_tracks(db, rg.recording.id, [])
