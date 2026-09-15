"""List-price estimates for LLM token costs, in USD per 1,000,000 tokens.

These are published list prices, used only to *estimate* what an enrichment run cost.
They are **not** billing figures: providers change prices and offer cache and volume
discounts this table does not model. Every cost derived here is stored with
`cost_estimated=True`, and nothing in the app may show a figure without saying so.

Ported from gecko-notes' `backend/app/pricing.py`, with Ollama free and `custom`
unpriced for the same reasons it gives.
"""

from __future__ import annotations

from typing import List, Optional, Tuple

# provider_type -> [(model-id substring, input $/1M, output $/1M), ...].
#
# Checked in order and the first substring *contained in* the model id wins, so the more
# specific ids come before their shorter prefixes — "claude-3-5-haiku" has to be tested
# before "claude-haiku" or it would never match. Each family ends in a catch-all, so a
# model released after this table was written still gets a figure of the right order of
# magnitude rather than none at all.
_PRICES: dict[str, List[Tuple[str, float, float]]] = {
    "anthropic": [
        ("claude-opus-4-1", 15.0, 75.0),
        ("claude-opus", 15.0, 75.0),
        ("claude-3-5-haiku", 0.80, 4.0),
        ("claude-3-haiku", 0.25, 1.25),
        ("claude-haiku", 1.0, 5.0),
        ("claude-3-7-sonnet", 3.0, 15.0),
        ("claude-3-5-sonnet", 3.0, 15.0),
        ("claude-sonnet", 3.0, 15.0),
        ("claude", 3.0, 15.0),  # unknown Claude model — assume Sonnet-tier
    ],
    "openai": [
        ("gpt-4o-mini", 0.15, 0.60),
        ("gpt-4o", 2.50, 10.0),
        ("gpt-4.1-nano", 0.10, 0.40),
        ("gpt-4.1-mini", 0.40, 1.60),
        ("gpt-4.1", 2.0, 8.0),
        ("gpt-4-turbo", 10.0, 30.0),
        ("gpt-4", 30.0, 60.0),
        ("gpt-3.5", 0.50, 1.50),
        ("o4-mini", 1.10, 4.40),
        ("o3-mini", 1.10, 4.40),
        ("o3", 2.0, 8.0),
        ("o1-mini", 1.10, 4.40),
        ("o1", 15.0, 60.0),
        ("gpt", 2.50, 10.0),  # unknown GPT model — assume 4o-tier
    ],
    # DeepSeek's standard (cache-miss) list prices. Its cache-hit discount is real and
    # substantial and is not modelled here, which is one more reason these are estimates.
    "deepseek": [
        ("deepseek-reasoner", 0.55, 2.19),
        ("deepseek-chat", 0.27, 1.10),
        ("deepseek", 0.27, 1.10),  # unknown DeepSeek model — assume chat-tier
    ],
}


def cost_for(
    provider_type: Optional[str],
    model: str,
    input_tokens: int,
    output_tokens: int,
) -> Optional[Tuple[float, str]]:
    """Estimate (cost, currency) for one LLM call, or None when no estimate is possible.

    None rather than zero for an unknown family or a `custom` endpoint: a stored 0.0
    reads as "this was free", which is a different and wrong claim. Ollama genuinely is
    free — it runs on the user's own machine — so it returns an honest zero.
    """
    kind = (provider_type or "").lower()
    if kind == "ollama":
        return (0.0, "USD")

    table = _PRICES.get(kind)
    if not table:
        return None

    needle_in = (model or "").lower()
    for needle, input_price, output_price in table:
        if needle in needle_in:
            cost = (input_tokens / 1_000_000.0) * input_price + (
                output_tokens / 1_000_000.0
            ) * output_price
            return (round(cost, 6), "USD")
    return None
