"""Reading the official season stats file and matching its lines to our players.

The files are built line by line in the exact shape NBA.com exports them:
semicolons, a trailing semicolon on every line, CRLF line endings, UTF-8.
"""

from decimal import Decimal

import pytest
from django.core.management import call_command

from apps.nba.models import Player, Team
from apps.nba.season_stats import (
    MAX_ROWS,
    SeasonStatsFileError,
    match_rows,
    name_key,
    parse_season_stats,
)

HEADER = (
    "PLAYER;TEAM;AGE;GP;W;L;MIN;PTS;FGM;FGA;FG%;3PM;3PA;3P%;FTM;FTA;FT%;OREB;DREB;REB;"
    "AST;TOV;STL;BLK;PF;TFP;HSFP;DD2;TD3;+/-;"
)
LUKA = (
    "Luka Dončić;LAL;27;64;43;21;35.8;33.5;10.8;22.8;47.6;4.0;10.8;36.6;7.9;10.1;78.0;"
    "0.6;7.1;7.7;8.3;4.0;1.6;0.5;2.4;57.7;64.3;34.0;8.0;2.9;"
)
SGA = (
    "Shai Gilgeous-Alexander;OKC;27;68;56;12;33.2;31.1;10.8;19.4;55.3;1.7;4.4;38.6;7.9;"
    "9.0;87.9;0.6;3.7;4.3;6.6;2.2;1.4;0.8;2.0;50.4;55.1;8.0;0.0;11.6;"
)


def stats_file(*lines, header=HEADER, delimiter=";", encoding="utf-8"):
    text = "\r\n".join([header, *lines]) + "\r\n"
    if delimiter != ";":
        text = text.replace(";", delimiter)
    return text.encode(encoding)


def line(name="Bam Adebayo", team="MIA", **overrides):
    """A valid line for `name`, with any column replaced by keyword (MIN=...)."""
    values = dict(zip(HEADER.split(";")[2:-1], LUKA.split(";")[2:-1], strict=True))
    values.update({column.replace("_PCT", "%"): str(v) for column, v in overrides.items()})
    return ";".join([name, team, *values.values()]) + ";"


# --- reading the file -----------------------------------------------------------


def test_the_exported_format_is_read():
    parsed = parse_season_stats(stats_file(LUKA, SGA))

    assert parsed.problems == []
    assert [row.name for row in parsed.rows] == ["Luka Dončić", "Shai Gilgeous-Alexander"]
    luka = parsed.rows[0]
    assert luka.team == "LAL"
    assert luka.line == 2
    assert luka.values["age"] == 27
    assert luka.values["games_played"] == 64
    assert luka.values["points"] == Decimal("33.5")
    assert luka.values["field_goal_pct"] == Decimal("47.6")
    assert luka.values["fantasy_points"] == Decimal("57.7")


def test_ignored_columns_are_not_read():
    values = parse_season_stats(stats_file(LUKA)).rows[0].values

    assert len(values) == 25, "24 read from the file, plus the calculated score"
    assert not {"hsfp", "dd2", "td3", "plus_minus"} & set(values)


def test_columns_are_found_by_name_not_position():
    header = "TEAM;PLAYER;" + ";".join(HEADER.split(";")[2:])
    row = ";".join(["LAL", "Luka Dončić", *LUKA.split(";")[2:]])

    parsed = parse_season_stats(stats_file(row, header=header))

    assert parsed.rows[0].name == "Luka Dončić"
    assert parsed.rows[0].values["points"] == Decimal("33.5")


def test_a_byte_order_mark_and_blank_lines_are_tolerated():
    data = b"\xef\xbb\xbf" + b";;;;;\r\n" + stats_file(LUKA, "", ";;;;", SGA)

    assert len(parse_season_stats(data).rows) == 2


def test_problems_name_the_line_in_the_file_counting_blank_lines():
    parsed = parse_season_stats(stats_file("", LUKA, ";;;", line(PTS="x")))

    assert parsed.problems[0].startswith("Line 5 ")


def test_comma_separated_files_are_read():
    assert len(parse_season_stats(stats_file(LUKA, SGA, delimiter=",")).rows) == 2


def test_decimal_commas_are_read_in_semicolon_files():
    parsed = parse_season_stats(stats_file(line(PTS="33,5")))

    assert parsed.rows[0].values["points"] == Decimal("33.5")


def test_a_file_that_is_not_utf8_is_rejected():
    with pytest.raises(SeasonStatsFileError, match="UTF-8"):
        parse_season_stats(stats_file(LUKA, encoding="cp1250"))


def test_a_missing_column_rejects_the_file():
    header = HEADER.replace("TFP;", "").replace("3P%;", "")

    with pytest.raises(SeasonStatsFileError, match="3P%, TFP"):
        parse_season_stats(stats_file(header=header))


def test_an_empty_file_is_rejected():
    with pytest.raises(SeasonStatsFileError, match="empty"):
        parse_season_stats(b"\r\n")


def test_too_many_rows_reject_the_file():
    lines = [line(f"Player {number}") for number in range(MAX_ROWS + 1)]

    with pytest.raises(SeasonStatsFileError, match="more than"):
        parse_season_stats(stats_file(*lines))


def test_a_file_over_two_megabytes_is_rejected():
    with pytest.raises(SeasonStatsFileError, match="2 MB"):
        parse_season_stats(b"x" * (2 * 1024 * 1024 + 1))


@pytest.mark.parametrize(
    ("column", "value"),
    [
        ("PTS", "abc"),
        ("PTS", ""),
        ("FG_PCT", "100.1"),
        ("MIN", "49"),
        ("GP", "60.5"),
        ("AGE", "-1"),
    ],
)
def test_a_bad_value_leaves_out_only_that_line(column, value):
    parsed = parse_season_stats(stats_file(line("Bam Adebayo", **{column: value}), SGA))

    assert [row.name for row in parsed.rows] == ["Shai Gilgeous-Alexander"]
    assert len(parsed.problems) == 1
    assert parsed.problems[0].startswith("Line 2 (Bam Adebayo)")


def test_a_line_without_a_name_is_left_out():
    parsed = parse_season_stats(stats_file(line(""), SGA))

    assert len(parsed.rows) == 1
    assert "name is empty" in parsed.problems[0]


def test_a_player_listed_twice_is_left_out_entirely():
    parsed = parse_season_stats(stats_file(LUKA, SGA, LUKA.replace("LAL", "DAL")))

    assert [row.name for row in parsed.rows] == ["Shai Gilgeous-Alexander"]
    assert len(parsed.problems) == 2


# --- names ------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("theirs", "ours"),
    [
        ("Luka Dončić", "Luka Doncic"),
        ("Jaren Jackson Jr.", "Jaren Jackson"),
        ("Ronald Holland II", "Ronald Holland"),
        ("Shai Gilgeous-Alexander", "SHAI GILGEOUS ALEXANDER"),
        ("Nah'Shon Hyland", "Nah\u2018Shon Hyland"),
        ("Egor Dëmin", "Egor Demin"),
        ("AJ Green", "A.J. Green"),
    ],
)
def test_name_keys_ignore_accents_case_punctuation_and_suffixes(theirs, ours):
    assert name_key(theirs) == name_key(ours)


# --- matching ---------------------------------------------------------------------


@pytest.fixture
def teams(db):
    call_command("seed_teams", verbosity=0)
    return {team.abbreviation: team for team in Team.objects.all()}


def player(first, last, team=None, **fields):
    return Player.objects.create(
        first_name=first, last_name=last, position="G", team=team, **fields
    )


def rows_for(*lines):
    return parse_season_stats(stats_file(*lines)).rows


def test_a_unique_name_matches_even_after_a_move(teams):
    luka = player("Luka", "Doncic", teams["DAL"])

    result = match_rows(rows_for(LUKA))

    assert [(row.name, p) for row, p in result.matched] == [("Luka Dončić", luka)]
    assert result.unmatched == result.ambiguous == []


def test_inactive_players_and_free_agents_are_matched(teams):
    luka = player("Luka", "Dončić", is_active=False)

    assert match_rows(rows_for(LUKA)).matched[0][1] == luka


@pytest.mark.parametrize(
    ("theirs", "first", "last"),
    [
        ("GG Jackson", "Gregory", "Jackson"),
        ("Nic Claxton", "Nicolas", "Claxton"),
        ("Bones Hyland", "Nah\u2018Shon", "Hyland"),
        ("Ronald Holland II", "Ron", "Holland"),
        ("Yang Hansen", "Hansen", "Yang"),
    ],
)
def test_known_nicknames_are_matched_through_the_aliases(teams, theirs, first, last):
    ours = player(first, last)

    assert match_rows(rows_for(line(theirs))).matched[0][1] == ours


def test_the_team_breaks_a_tie_between_namesakes(teams):
    player("Jaylin", "Williams")
    thunder = player("Jaylin", "Williams", teams["OKC"])

    result = match_rows(rows_for(line("Jaylin Williams", "OKC")))

    assert result.matched[0][1] == thunder
    assert result.ambiguous == []


def test_namesakes_without_a_team_match_are_reported_not_guessed(teams):
    first = player("Jaylin", "Williams")
    second = player("Jaylin", "Williams", teams["MIA"])

    result = match_rows(rows_for(line("Jaylin Williams", "OKC")))

    assert result.matched == []
    assert result.ambiguous == [f"Jaylin Williams (OKC): {first.slug}, {second.slug}"]


def test_unknown_players_are_reported(teams):
    result = match_rows(rows_for(line("Kyle Lowry", "PHI")))

    assert result.matched == []
    assert result.unmatched == ["Kyle Lowry (PHI)"]


def test_two_lines_landing_on_one_player_are_both_reported(teams):
    player("Ron", "Holland")

    result = match_rows(rows_for(line("Ron Holland", "DET"), line("Ronald Holland II", "DET")))

    assert result.matched == []
    assert len(result.ambiguous) == 2
