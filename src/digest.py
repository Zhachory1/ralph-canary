"""Morning digest: lists cards waiting on a human."""


def suppress_seen(cards, seen):
    """Return cards whose id is not already in `seen`, preserving order.

    A card with no id is always kept: dropping an unidentifiable card is
    worse than mentioning it twice.
    """
    result = []
    for card in cards:
        card_id = card.get("id")
        if card_id is None or card_id not in seen:
            result.append(card)
    return result


def render(cards):
    lines = [f"{len(cards)} waiting on you"]
    for card in cards:
        lines.append(f"- {card['title']}")
    return "\n".join(lines)
