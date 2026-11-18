"""D. Games and rosters (FR-03, FR-04, FR-05, FR-15) and reliability-gated creation (SYS-06)."""

from __future__ import annotations

from datetime import timedelta
from decimal import Decimal

import pytest
import sqlalchemy as sa

from server.app.crud import attendance, games
from server.app.crud.errors import (
    BlockedError,
    GameFullError,
    GameNotJoinableError,
    NotFoundError,
    NotPermittedError,
    ReliabilityTooLowError,
    SkillOutOfRangeError,
)
from server.app.models import (
    Game,
    GameParticipant,
    ParticipantStatus,
    PlayerRating,
    ReliabilityKind,
    ScoringRule,
    Visibility,
)
from server.tests.db import factories as f


async def _count(db, game_id) -> int:
    return await db.scalar(sa.select(Game.participant_count).where(Game.id == game_id))


async def _status(db, game_id, user_id):
    return await db.scalar(
        sa.select(GameParticipant.status).where(GameParticipant.game_id == game_id, GameParticipant.user_id == user_id)
    )


async def test_create_game_records_every_field_and_adds_organizer(db):
    org = await f.user(db)
    court = await f.court(db)
    start = f.at(days=2)
    g = await games.create_game(
        db,
        organizer_id=org.id,
        court_id=court.id,
        starts_at=start,
        ends_at=start + timedelta(hours=1),
        player_cap=8,
        visibility=Visibility.REQUEST_TO_JOIN,
        scoring_rule=ScoringRule.TWOS_THREES,
        skill_min=3.5,
        skill_max=4.5,
        is_recorded=True,
        rules_note="2s & 3s to 21",
    )
    stored = await db.get(Game, g.id, populate_existing=True)
    assert (stored.player_cap, stored.visibility, stored.scoring_rule, stored.is_recorded) == (
        8, Visibility.REQUEST_TO_JOIN, ScoringRule.TWOS_THREES, True
    )
    assert (stored.skill_min, stored.skill_max, stored.rules_note) == (Decimal("3.5"), Decimal("4.5"), "2s & 3s to 21")
    assert stored.participant_count == 1
    assert await _status(db, g.id, org.id) == ParticipantStatus.CONFIRMED


async def test_private_game_gets_invite_token(db):
    g = await f.game(db, visibility=Visibility.PRIVATE)
    assert g.invite_token and len(g.invite_token) >= 16


async def test_join_and_full_game(db):
    g = await f.game(db, cap=2)
    p1, p2 = await f.user(db), await f.user(db)
    assert await games.join_game(db, g.id, p1.id) == ParticipantStatus.CONFIRMED
    assert await _count(db, g.id) == 2
    with pytest.raises(GameFullError):
        await games.join_game(db, g.id, p2.id)
    assert await _count(db, g.id) == 2


async def test_join_twice_is_a_no_op(db):
    g = await f.game(db)
    p = await f.user(db)
    await games.join_game(db, g.id, p.id)
    await games.join_game(db, g.id, p.id)
    assert await _count(db, g.id) == 2


async def test_leave_frees_a_spot_and_player_can_rejoin(db):
    g = await f.game(db, cap=2)
    p1, p2 = await f.user(db), await f.user(db)
    await games.join_game(db, g.id, p1.id)
    await games.leave_game(db, g.id, p1.id)
    assert await _count(db, g.id) == 1
    assert await games.join_game(db, g.id, p2.id) == ParticipantStatus.CONFIRMED
    await games.leave_game(db, g.id, p2.id)
    assert await games.join_game(db, g.id, p1.id) == ParticipantStatus.CONFIRMED
    assert await _status(db, g.id, p1.id) == ParticipantStatus.CONFIRMED


async def test_organizer_cannot_leave(db):
    org = await f.user(db)
    g = await f.game(db, organizer=org)
    with pytest.raises(NotPermittedError):
        await games.leave_game(db, g.id, org.id)


async def test_request_to_join_is_pending_and_approval_rechecks_cap(db):
    org = await f.user(db)
    g = await f.game(db, organizer=org, cap=2, visibility=Visibility.REQUEST_TO_JOIN)
    r1, r2 = await f.user(db), await f.user(db)
    assert await games.join_game(db, g.id, r1.id) == ParticipantStatus.PENDING
    assert await games.join_game(db, g.id, r2.id) == ParticipantStatus.PENDING
    assert await _count(db, g.id) == 1  # pending requests don't hold spots

    with pytest.raises(NotPermittedError):
        await games.approve_request(db, g.id, r2.id, r1.id)  # not the organizer
    await games.approve_request(db, g.id, org.id, r1.id)
    assert await _count(db, g.id) == 2
    with pytest.raises(GameFullError):
        await games.approve_request(db, g.id, org.id, r2.id)
    assert await _status(db, g.id, r2.id) == ParticipantStatus.PENDING


async def test_blocked_player_cannot_join_request_or_see(db):
    org = await f.user(db)
    pub = await f.game(db, organizer=org)
    req = await f.game(db, organizer=org, visibility=Visibility.REQUEST_TO_JOIN)
    p = await f.user(db)
    await games.block_player(db, org.id, p.id)
    with pytest.raises(BlockedError):
        await games.join_game(db, pub.id, p.id)
    with pytest.raises(BlockedError):
        await games.join_game(db, req.id, p.id)
    visible = {v.game_id for v in await games.list_visible_games(db, p.id)}
    assert pub.id not in visible and req.id not in visible


async def test_block_removes_player_from_game(db):
    org = await f.user(db)
    g = await f.game(db, organizer=org)
    p = await f.user(db)
    await games.join_game(db, g.id, p.id)
    await games.block_player(db, org.id, p.id, game_id=g.id)
    assert await _status(db, g.id, p.id) == ParticipantStatus.REMOVED
    assert await _count(db, g.id) == 1


async def test_removed_player_cannot_rejoin(db):
    org = await f.user(db)
    g = await f.game(db, organizer=org)
    p = await f.user(db)
    await games.join_game(db, g.id, p.id)
    await games.remove_player(db, g.id, org.id, p.id)
    with pytest.raises(NotPermittedError):
        await games.join_game(db, g.id, p.id)


async def test_private_game_hidden_but_joinable_by_invite(db):
    g = await f.game(db, visibility=Visibility.PRIVATE)
    p = await f.user(db)
    assert g.id not in {v.game_id for v in await games.list_visible_games(db, p.id)}
    with pytest.raises(NotPermittedError):
        await games.join_game(db, g.id, p.id)
    with pytest.raises(NotPermittedError):
        await games.join_game(db, g.id, p.id, invite_token="wrong")
    game_id, status = await games.join_by_invite(db, g.invite_token, p.id)
    assert (game_id, status) == (g.id, ParticipantStatus.CONFIRMED)
    with pytest.raises(NotFoundError):
        await games.join_by_invite(db, "not-a-token", p.id)


async def test_skill_based_visibility_tc_fr04(db):
    org = await f.user(db, rating=4.0)
    court = await f.court(db)
    ranges = {"Beginner": (2.0, 3.0), "Mid": (3.5, 4.0), "Advanced": (4.0, 5.0), "All": None}
    ids = {}
    for label, rng in ranges.items():
        ids[label] = (await f.game(db, organizer=org, court_obj=court, skill=rng)).id
    expected = {2.5: {"Beginner", "All"}, 3.8: {"Mid", "All"}, 4.6: {"Advanced", "All"}}
    for rating, labels in expected.items():
        viewer = await f.user(db, rating=rating)
        seen = {v.game_id for v in await games.list_visible_games(db, viewer.id)}
        assert seen & set(ids.values()) == {ids[label] for label in labels}, rating


async def test_join_outside_skill_range_rejected(db):
    g = await f.game(db, skill=(4.0, 5.0))
    with pytest.raises(SkillOutOfRangeError):
        await games.join_game(db, g.id, (await f.user(db, rating=2.5)).id)


async def test_provisional_player_sees_all_levels_and_provisional_range(db):
    org = await f.user(db, rating=4.0)
    court = await f.court(db)
    all_levels = await f.game(db, organizer=org, court_obj=court)
    mid = await f.game(db, organizer=org, court_obj=court, skill=(3.5, 4.0))
    adv = await f.game(db, organizer=org, court_obj=court, skill=(4.5, 5.0))
    newbie = await f.user(db, rating=3.8)
    rating = await db.get(PlayerRating, newbie.id)
    assert rating.is_provisional and rating.rated_games == 0
    seen = {v.game_id for v in await games.list_visible_games(db, newbie.id)}
    assert {all_levels.id, mid.id} <= seen and adv.id not in seen


async def test_low_reliability_organizer_cannot_create_public_game(db):
    org = await f.user(db)
    for kind in (ReliabilityKind.ATTENDED, ReliabilityKind.NO_SHOW, ReliabilityKind.NO_SHOW):
        past = await f.game(db, organizer=org, starts_at=f.at(days=-3 - len(kind.value)))
        await attendance.add_reliability_event(db, org.id, past.id, kind)
    assert await attendance.recompute_reliability(db, org.id) == Decimal("0.333")
    with pytest.raises(ReliabilityTooLowError):
        await f.game(db, organizer=org, visibility=Visibility.PUBLIC)
    private = await f.game(db, organizer=org, visibility=Visibility.PRIVATE)
    assert private.visibility == Visibility.PRIVATE


async def test_threshold_not_applied_before_enough_history(db):
    org = await f.user(db)
    past = await f.game(db, organizer=org, starts_at=f.at(days=-2))
    await attendance.add_reliability_event(db, org.id, past.id, ReliabilityKind.NO_SHOW)
    assert await attendance.recompute_reliability(db, org.id) == Decimal("0.000")
    assert (await f.game(db, organizer=org)).visibility == Visibility.PUBLIC


async def test_cancelled_game_cannot_be_joined(db):
    org = await f.user(db)
    g = await f.game(db, organizer=org)
    await games.cancel_game(db, g.id, org.id)
    with pytest.raises(GameNotJoinableError):
        await games.join_game(db, g.id, (await f.user(db)).id)
    assert g.id not in {v.game_id for v in await games.list_visible_games(db, (await f.user(db)).id)}


async def test_late_cancellations_count_against_reliability(db):
    org = await f.user(db)
    soon = await f.game(db, organizer=org, starts_at=f.at(hours=1))
    p = await f.user(db)
    await games.join_game(db, soon.id, p.id)
    await games.leave_game(db, soon.id, p.id)
    assert (await attendance.reliability_counts(db, p.id))[ReliabilityKind.LATE_CANCEL] == 1
    await games.cancel_game(db, soon.id, org.id)
    assert (await attendance.reliability_counts(db, org.id))[ReliabilityKind.ORGANIZER_CANCEL] == 1

    later = await f.game(db, organizer=org, starts_at=f.at(days=3))
    q = await f.user(db)
    await games.join_game(db, later.id, q.id)
    await games.leave_game(db, later.id, q.id)  # well ahead of time: not a late cancel
    assert sum((await attendance.reliability_counts(db, q.id)).values()) == 0


async def test_update_game_and_cap_floor(db):
    org = await f.user(db)
    g = await f.game(db, organizer=org, cap=4)
    await games.join_game(db, g.id, (await f.user(db)).id)
    with pytest.raises(NotPermittedError):
        await games.update_game(db, g.id, org.id, player_cap=1)
    with pytest.raises(NotPermittedError):
        await games.update_game(db, g.id, (await f.user(db)).id, rules_note="x")
    new_start = g.starts_at + timedelta(hours=2)
    updated = await games.update_game(db, g.id, org.id, starts_at=new_start, ends_at=new_start + timedelta(hours=1))
    assert updated.starts_at == new_start


async def test_visible_game_carries_court_sheet_fields(db):
    host = await f.user(db, rating=4.2, name="Mike R.")
    court = await f.court(db, name="Purdue CoRec - Court 2")
    g = await f.game(db, organizer=host, court_obj=court, cap=10, skill=(3.5, 4.0))
    for _ in range(6):
        await f.add_confirmed(db, g, await f.user(db))
    viewer = await f.user(db, rating=3.8)
    lat, lon = 40.4284, -86.9215
    [v] = [x for x in await games.list_visible_games(db, viewer.id, near=(lat, lon), radius_m=500) if x.game_id == g.id]
    assert (v.court_name, v.host_name, v.skill_label) == ("Purdue CoRec - Court 2", "Mike R.", "Mid")
    assert (v.player_cap, v.participant_count, v.spots_available) == (10, 7, 3)
    assert v.host_rating == 4.2 and v.host_show_up_rate == 1.0
    assert v.distance_m is not None and v.distance_m < 1
    far = await games.list_visible_games(db, viewer.id, near=(40.0, -86.0), radius_m=500)
    assert g.id not in {x.game_id for x in far}


def test_skill_labels():
    D = Decimal
    assert games.skill_label(None, None) == "All"
    assert games.skill_label(D("2.0"), D("3.0")) == "Beginner"
    assert games.skill_label(D("3.5"), D("4.0")) == "Mid"
    assert games.skill_label(D("4.0"), D("5.0")) == "Advanced"


async def test_roster_listing(db):
    g = await f.game(db)
    p = await f.user(db)
    await games.join_game(db, g.id, p.id)
    assert {r.user_id for r in await games.roster(db, g.id)} == {g.organizer_id, p.id}
