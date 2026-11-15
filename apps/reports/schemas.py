"""The shapes the AI must answer in, as Pydantic models.

Sent to OpenAI as a strict JSON schema (structured outputs), so an answer
always parses; what it *says* is still checked against our own lists before
anything is stored (`services._clean_*`). Field descriptions are part of the
schema the model reads, so they double as instructions.
"""

from typing import Literal

from pydantic import BaseModel, Field


class BriefingItem(BaseModel):
    kind: Literal[
        "opportunity", "injury_opening", "riser", "faller", "salary_watch", "schedule"
    ] = Field(
        description="opportunity: a teammate who gains from an absence (name him, and the "
        "absent player). injury_opening: an injury itself, and what it opens up. "
        "riser/faller: a lasting change in form, week over week. salary_watch: a notable "
        "predicted salary change before Sunday's update. schedule: a busy or light stretch."
    )
    player_refs: list[str] = Field(
        max_length=3, description="Refs (P001...) of the players the item is about."
    )
    text: str = Field(description="One or two sentences, with the figures behind it.")


class Briefing(BaseModel):
    headline: str = Field(description="The week in at most ten words.")
    summary: str = Field(description="Two to four sentences on what matters this week.")
    items: list[BriefingItem] = Field(max_length=8)


class TradePick(BaseModel):
    trade_id: str = Field(description="A shortlist ref, T01...")
    priority: Literal[1, 2]
    horizon: Literal["short_term", "few_weeks", "rest_of_season"] = Field(
        description="How long the gain should last: short_term (about a week, e.g. a "
        "salary bump or a busy schedule), few_weeks (e.g. while a teammate is out), "
        "rest_of_season (a lasting role or level)."
    )
    reasoning: str = Field(
        description="Two or three sentences: why, with figures. Name the players, "
        "never the trade id."
    )
    why_it_lasts: str = Field(
        description="One sentence: what should keep the gain going (role, minutes, an "
        "absence, a steady weekly rise)."
    )
    what_ends_it: str = Field(
        description="One sentence: what would end it, with a date when the data has one "
        "(e.g. a starter's expected return)."
    )
    confidence: Literal["low", "medium", "high"]


class WatchItem(BaseModel):
    player_ref: str = Field(description="A player ref, P001...")
    text: str = Field(description="One sentence: what to watch for.")


class RosterAdvice(BaseModel):
    headline: str = Field(description="The advice in at most ten words.")
    situation: str = Field(description="Two or three sentences on the squad as it stands.")
    decision: Literal["trade", "hold", "buy_trade_then_trade"]
    trades: list[TradePick] = Field(max_length=2, description="Empty when the decision is hold.")
    hold_reason: str = Field(description="Why to keep the trade; empty unless holding.")
    watch: list[WatchItem] = Field(max_length=3)
    risks: str = Field(description="One or two sentences on what could make this wrong.")
