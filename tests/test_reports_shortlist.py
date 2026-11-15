"""The legal trades a roster could make: `apps.reports.shortlist`. No database."""

from decimal import Decimal

from apps.reports.shortlist import Candidate, build

M = Decimal(1_000_000)
_ids = iter(range(1, 10_000))


def player(position="G", salary="3", projected=500, change="0"):
    serial = next(_ids)
    return Candidate(
        player_id=serial,
        ref=f"P{serial:03d}",
        name=f"Player {serial}",
        position=position,
        salary=Decimal(salary) * M,
        projected_fp=projected,
        salary_change=None if change is None else Decimal(change) * M,
    )


def squad(guards=5, forwards=5, centers=2, **kwargs):
    return (
        [player("G", **kwargs) for _ in range(guards)]
        + [player("F", **kwargs) for _ in range(forwards)]
        + [player("C", **kwargs) for _ in range(centers)]
    )


def pairs(trades):
    return [(trade.out.ref, trade.into.ref) for trade in trades]


def test_only_trades_the_cash_covers():
    team = squad(guards=5, forwards=5, centers=2, salary="3", projected=500)
    affordable = player("G", salary="4", projected=900)
    too_dear = player("G", salary="5.01", projected=2000)
    trades = build(team, [affordable, too_dear], cash=2 * M)
    assert {trade.into.ref for trade in trades} == {affordable.ref}
    assert trades[0].cash_after == 1 * M


def test_never_breaks_a_position_minimum():
    team = squad(guards=5, forwards=6, centers=2)
    guard_in = player("G", projected=900)
    forward_in = player("F", projected=900)
    trades = build(team, [guard_in, forward_in], cash=5 * M, per_player=100, limit=100)
    # A guard may only go for a guard (5 is the minimum); the spare forward may
    # go for either.
    for trade in trades:
        if trade.out.position == "G":
            assert trade.into.position == "G"
    assert any(t.out.position == "F" and t.into.position == "G" for t in trades)


def test_a_roster_already_short_may_still_swap_like_for_like():
    team = squad(guards=4, forwards=6, centers=2)
    guard_in = player("G", projected=900)
    forward_in = player("F", projected=900)
    trades = build(team, [guard_in, forward_in], cash=5 * M, per_player=100, limit=100)
    assert any(t.out.position == "G" and t.into.position == "G" for t in trades)
    # Filling the gap is fine; widening it is not.
    assert any(t.out.position == "F" and t.into.position == "G" for t in trades)
    assert not any(t.out.position == "G" and t.into.position == "F" for t in trades)


def test_only_trades_that_gain_points_and_never_unknown_newcomers():
    team = squad(projected=500)
    worse = player("G", projected=400)
    level = player("G", projected=500)
    unknown = player("G", projected=None)
    assert build(team, [worse, level, unknown], cash=5 * M) == []


def test_a_player_without_a_projection_can_still_go_out():
    team = squad(projected=500)
    team[0] = player("G", projected=None)
    newcomer = player("G", projected=100)
    trades = build(team, [newcomer], cash=5 * M)
    assert pairs(trades) == [(team[0].ref, newcomer.ref)]
    assert trades[0].fp_gain == 100


def test_ranked_by_points_then_by_salary_gain():
    team = [player("G", projected=100, change="-1"), *squad(guards=5, projected=1000)]
    rising = player("G", projected=600, change="2")
    flat = player("G", projected=600, change="0")
    better = player("G", projected=700, change="-3")
    trades = build(team, [flat, rising, better], cash=5 * M)
    assert [trade.into.ref for trade in trades] == [better.ref, rising.ref, flat.ref]
    assert [trade.ref for trade in trades] == ["T01", "T02", "T03"]
    assert trades[1].salary_gain == 3 * M


def test_variety_caps_per_player_and_in_all():
    team = squad(guards=10, forwards=5, centers=2, projected=100)
    market = [player("G", projected=1000 - n) for n in range(10)]
    trades = build(team, market, cash=5 * M, per_player=2, limit=7)
    assert len(trades) == 7
    outs = [trade.out.ref for trade in trades]
    ins = [trade.into.ref for trade in trades]
    assert max(outs.count(ref) for ref in outs) <= 2
    assert max(ins.count(ref) for ref in ins) <= 2


def test_squad_players_are_not_offered_back():
    team = squad(projected=100)
    star = team[0]
    trades = build(team, [star, *team], cash=5 * M)
    assert trades == []


def test_as_data_is_plain_json():
    team = squad(projected=100)
    newcomer = player("G", salary="2.5", projected=600, change=None)
    data = build(team, [newcomer], cash=1 * M)[0].as_data()
    assert data["ref"] == "T01"
    assert data["in"] == str(newcomer.player_id)
    assert Decimal(data["salary_in"]) == Decimal("2.5") * M
    assert Decimal(data["cash_after"]) == Decimal("1.5") * M
    assert data["fp_gain"] == 500
    assert data["change_in"] is None
    assert Decimal(data["change_out"]) == 0
