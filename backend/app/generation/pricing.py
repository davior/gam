"""What a fal.ai generation cost.

fal reports the billed *quantity* (`x-fal-billable-units`, in the endpoint's own unit)
on the queue result; the *price* per unit comes from its pricing API, fetched with the
user's own key so a negotiated price is the one applied. Units × price is then the bill,
and recorded as exact. Each half degrades separately rather than to nothing:

| units from              | price from          | cost         | cost_estimated |
|-------------------------|---------------------|--------------|----------------|
| header                  | pricing API         | units × price| False          |
| header                  | catalogue list price| units × price| True           |
| outputs (no header)     | either              | derived × price | True        |
| —                       | no price anywhere   | None         | None           |

gecko-notes filled its price cache from fal's *usage* API, which needs a billing-scoped
key, only knows endpoints already billed, and dropped prices outside each refresh's
window; the pricing API has none of those problems. Neither the header on the queue
result nor the pricing API on an ordinary key could be verified from where this was
written — both are documented, neither was reachable — which is why both fall back.
"""

from __future__ import annotations

import logging
import math
import threading
import time
from dataclasses import dataclass
from typing import Iterable, Optional

from app.config import settings
from app.generation.fal import LABEL, auth_headers
from app.models.usage import (
    UNIT_GENERATED_SECONDS,
    UNIT_IMAGES,
    UNIT_MEGAPIXELS,
    UNIT_VIDEOS,
)
from app.providers import _upstream
from app.providers.base import ProviderError

logger = logging.getLogger(__name__)

PRICE_TTL_SECONDS = 6 * 3600
PRICING_TIMEOUT = 10.0

# fal rounds each image *up* to whole megapixels, and its megapixel is 1024², not 10⁶:
# 1024×1024 bills as one, 1024×1536 as two, 512×512 as one. Computing it any other way
# under-counts (one integrator was 14% low).
PIXELS_PER_MEGAPIXEL = 1024 * 1024

_UNIT_TYPES = {
    "image": UNIT_IMAGES,
    "megapixel": UNIT_MEGAPIXELS,
    "second": UNIT_GENERATED_SECONDS,
    "video": UNIT_VIDEOS,
}


@dataclass(frozen=True)
class UnitPrice:
    unit_price: float
    unit: str  # fal's word for it: "image", "megapixel", "second", "video", or other
    currency: str


@dataclass(frozen=True)
class OutputFacts:
    """What was probed from one downloaded output, for deriving units without a header."""

    width: Optional[int] = None
    height: Optional[int] = None
    duration_seconds: Optional[float] = None


@dataclass(frozen=True)
class Cost:
    units: int
    unit_type: str
    cost: Optional[float]
    currency: Optional[str]
    estimated: Optional[bool]


# ─── fal's pricing API, cached ───────────────────────────────────────────────
#
# Per (user, endpoint): the price is fetched with the user's key and "custom pricing or
# discounts may be applied", so one user's price is not another's. In-process only — it
# is six hours of one small number, and a restart refetching it costs one request.

_cache: dict[tuple[str, str], tuple[float, UnitPrice]] = {}
_lock = threading.Lock()


def list_price(user_id: str, endpoint_id: str, *, api_key: str) -> Optional[UnitPrice]:
    """fal's own price for this endpoint, or None if its pricing API would not say."""
    key = (user_id, endpoint_id)
    now = time.monotonic()
    with _lock:
        cached = _cache.get(key)
        if cached and now - cached[0] < PRICE_TTL_SECONDS:
            return cached[1]

    price = _fetch(endpoint_id, api_key)
    # Only a found price is cached. A failure is retried on the next generation, which
    # costs one small request, rather than pinning six hours of estimates on a blip.
    if price is not None:
        with _lock:
            _cache[key] = (now, price)
    return price


def clear_cache() -> None:
    with _lock:
        _cache.clear()


def _fetch(endpoint_id: str, api_key: str) -> Optional[UnitPrice]:
    try:
        response = _upstream.request(
            "GET",
            f"{settings.fal_api_base_url.rstrip('/')}/v1/models/pricing",
            headers=auth_headers(api_key),
            params={"endpoint_id": endpoint_id},
            timeout=PRICING_TIMEOUT,
            label=f"{LABEL} pricing",
        )
    except ProviderError as exc:
        logger.info("No price from fal.ai for %s: %s", endpoint_id, exc)
        return None
    if response.status_code >= 400:
        logger.info(
            "No price from fal.ai for %s: HTTP %d (%s)",
            endpoint_id,
            response.status_code,
            _upstream.error_detail(response),
        )
        return None

    try:
        body = response.json()
    except ValueError:
        return None
    for entry in (body.get("prices") if isinstance(body, dict) else None) or []:
        if not isinstance(entry, dict) or entry.get("endpoint_id") != endpoint_id:
            continue
        unit_price = entry.get("unit_price")
        if isinstance(unit_price, bool) or not isinstance(unit_price, (int, float)):
            continue
        return UnitPrice(
            unit_price=float(unit_price),
            unit=str(entry.get("unit") or ""),
            currency=str(entry.get("currency") or "USD"),
        )
    return None


# ─── the cost of one request ─────────────────────────────────────────────────


def compute(
    *,
    is_video: bool,
    billable_units: Optional[str],
    api_price: Optional[UnitPrice],
    catalogue_price: Optional[UnitPrice],
    fallback_unit: Optional[str],
    outputs: Iterable[OutputFacts],
) -> Cost:
    """Apply the table in the module docstring.

    `units` is an integer column, so it holds the quantity rounded up; `cost` is worked
    out from the exact quantity first. `fallback_unit` is the catalogue's `price_unit`,
    used to label units when there is no price to say what they are in.
    """
    outputs = list(outputs)
    price = api_price or catalogue_price
    unit = _unit_kind(price.unit if price else fallback_unit)
    unit_type = _UNIT_TYPES.get(unit or "", UNIT_VIDEOS if is_video else UNIT_IMAGES)

    quantity = _parse_quantity(billable_units)
    from_header = quantity is not None
    if quantity is None and unit is not None:
        quantity = _derive(unit, outputs)

    if price is None or quantity is None:
        # Not priceable. The row is still written — a total that silently left out a
        # generation would read as complete — with whatever count there is.
        units = quantity if quantity is not None else len(outputs)
        return Cost(
            units=math.ceil(units), unit_type=unit_type, cost=None, currency=None, estimated=None
        )

    return Cost(
        units=math.ceil(quantity),
        unit_type=unit_type,
        cost=round(quantity * price.unit_price, 6),
        currency=price.currency,
        # Exact only when both halves came from fal: its count and its price.
        estimated=not (from_header and api_price is not None),
    )


def _parse_quantity(raw: Optional[str]) -> Optional[float]:
    if raw is None:
        return None
    try:
        value = float(raw.strip())
    except (ValueError, AttributeError):
        return None
    return value if math.isfinite(value) and value >= 0 else None


def _unit_kind(unit: Optional[str]) -> Optional[str]:
    """fal's free-text unit as one of the four this app can derive and label.

    "compute seconds" and GPU units are not generated seconds and are left unmapped —
    a header quantity in them still prices correctly, it just cannot be derived.
    """
    text = (unit or "").strip().lower()
    if "megapixel" in text:
        return "megapixel"
    if "compute" in text or "gpu" in text:
        return None
    if "second" in text:
        return "second"
    if "video" in text:
        return "video"
    if "image" in text:
        return "image"
    return None


def _derive(unit: str, outputs: list[OutputFacts]) -> Optional[float]:
    """The billed quantity, worked out from the files themselves.

    None when it cannot be: a megapixel price with an image whose size was not probed
    is unpriceable, not free.
    """
    if not outputs:
        return None
    if unit in ("image", "video"):
        return float(len(outputs))
    if unit == "megapixel":
        if any(not o.width or not o.height for o in outputs):
            return None
        return float(
            sum(max(1, math.ceil(o.width * o.height / PIXELS_PER_MEGAPIXEL)) for o in outputs)
        )
    if unit == "second":
        if any(o.duration_seconds is None for o in outputs):
            return None
        return float(sum(o.duration_seconds for o in outputs))
    return None
