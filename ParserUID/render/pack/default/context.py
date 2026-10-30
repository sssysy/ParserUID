"""Prepare the default skin's view model for Jinja templates."""

from __future__ import annotations

from collections.abc import Mapping

from ...models import PreviewCard
from .pipeline import prepare_template_card


async def prepare_context(card: PreviewCard, *, proxy: str | None = None) -> Mapping[str, object]:
    """Build template data without producing HTML in Python."""
    template_card = await prepare_template_card(card, dark=card.is_video, proxy=proxy)
    return {"card": template_card}
