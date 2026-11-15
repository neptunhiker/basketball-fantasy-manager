"""The shapes the AI must answer in, as Pydantic models.

Sent to OpenAI as a strict JSON schema (structured outputs), so an answer
always parses; what it *says* is still checked against our own lists before
anything is stored (`services._clean_*`). Field descriptions are part of the
schema the model reads, so they double as instructions.
"""

from typing import Literal

from pydantic import BaseModel, Field


class BriefingItem(BaseModel):
    kind: Literal["injury_opening", "riser", "faller", "salary_watch", "schedule"] = Field(
        description="injury_opening: an injury that hands minutes to someone else. "
        "riser/faller: clearly improving or declining form. salary_watch: a notable "
        "predicted salary change before Sunday's update. schedule: a busy or light week."
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
    reasoning: str = Field(description="Two or three sentences: why, with figures.")
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
