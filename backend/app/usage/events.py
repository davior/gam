"""Recording what a call consumed.

Best-effort by design: a failure to write a usage row must never fail the work that
produced it. Losing one line of accounting is a far smaller problem than losing the
summary a user just paid for.
"""

from __future__ import annotations

import logging
from typing import Optional

from sqlmodel import Session, func, select

from app.models.usage import KIND_AI, UNIT_TOKENS, UsageEvent
from app.usage import pricing

logger = logging.getLogger(__name__)


def record(
    session: Session,
    *,
    user_id: str,
    kind: str,
    units: int,
    unit_type: str,
    asset_id: Optional[str] = None,
    provider: Optional[str] = None,
    model: str = "",
    cost: Optional[float] = None,
    currency: Optional[str] = None,
    cost_estimated: Optional[bool] = None,
) -> None:
    """Write one usage row. Never raises."""
    try:
        session.add(
            UsageEvent(
                user_id=user_id,
                asset_id=asset_id,
                kind=kind,
                provider=provider,
                model=model or "",
                units=int(units or 0),
                unit_type=unit_type or "",
                cost=cost,
                currency=currency,
                cost_estimated=cost_estimated,
            )
        )
        session.commit()
    except Exception:  # noqa: BLE001 - accounting must not break the work it measures
        logger.warning("Could not record usage for %s", user_id, exc_info=True)
        session.rollback()


def record_completion(session: Session, asset_id: Optional[str], user_id: str, completion) -> None:
    """Record one LLM call from the `Completion` its provider returned.

    `units` is the input and output tokens together, which is what a "how many tokens did
    this cost me" question means. The split is kept for the cost estimate, where the two
    sides are priced differently.

    Guarded in its own right, not just by `record`: everything before that call — reading
    the usage off the completion, looking up a price — is also accounting, and accounting
    must never be why a summary the user paid for fails to save.
    """
    try:
        _record_completion(session, asset_id, user_id, completion)
    except Exception:  # noqa: BLE001 - see above
        logger.warning("Could not record usage for %s", user_id, exc_info=True)


def _record_completion(session: Session, asset_id: Optional[str], user_id: str, completion) -> None:
    input_tokens = completion.usage.input_tokens
    output_tokens = completion.usage.output_tokens
    total = input_tokens + output_tokens
    if total <= 0:
        # A provider that reported nothing. A zero-token row would suggest a free call
        # rather than an unmeasured one.
        return

    estimate = pricing.cost_for(
        completion.provider_type, completion.model, input_tokens, output_tokens
    )
    record(
        session,
        user_id=user_id,
        asset_id=asset_id,
        kind=KIND_AI,
        units=total,
        unit_type=UNIT_TOKENS,
        provider=completion.provider_type,
        model=completion.model,
        cost=estimate[0] if estimate else None,
        currency=estimate[1] if estimate else None,
        # Only ever True here. An exact, provider-billed figure needs fal.ai's response
        # headers, which arrive with M8.
        cost_estimated=True if estimate else None,
    )


def totals_for(session: Session, user_id: str, asset_id: Optional[str] = None) -> dict:
    """What has been spent, overall or on one asset.

    `priced_events` and `total_events` are reported separately on purpose: when they
    differ, some calls could not be estimated, and a total that silently omitted them
    would read as complete. The UI says so rather than quietly under-reporting.
    """
    where = [UsageEvent.user_id == user_id]
    if asset_id is not None:
        where.append(UsageEvent.asset_id == asset_id)

    rows = session.exec(select(UsageEvent).where(*where)).all()

    priced = [r for r in rows if r.cost is not None]
    return {
        "total_events": len(rows),
        "priced_events": len(priced),
        "cost": round(sum(r.cost or 0.0 for r in priced), 6) if priced else 0.0,
        "currency": next((r.currency for r in priced if r.currency), "USD"),
        # True when every figure in the total came from the list-price table rather than
        # a bill. Everything does today; the flag exists so that stops being assumed.
        "estimated": all(r.cost_estimated for r in priced) if priced else True,
        "tokens": sum(r.units for r in rows if r.unit_type == UNIT_TOKENS),
        "seconds": sum(r.units for r in rows if r.unit_type == "seconds"),
    }


def by_provider(session: Session, user_id: str) -> list[dict]:
    """Spend grouped by provider, biggest first — the shape a readout wants."""
    rows = session.exec(
        select(
            UsageEvent.provider,
            func.count(UsageEvent.id),
            func.sum(UsageEvent.units),
            func.sum(UsageEvent.cost),
        )
        .where(UsageEvent.user_id == user_id)
        .group_by(UsageEvent.provider)
    ).all()

    grouped = [
        {
            "provider": provider or "unknown",
            "events": int(events or 0),
            "units": int(units or 0),
            "cost": round(float(cost), 6) if cost is not None else None,
        }
        for provider, events, units, cost in rows
    ]
    grouped.sort(key=lambda g: (g["cost"] or 0.0), reverse=True)
    return grouped
