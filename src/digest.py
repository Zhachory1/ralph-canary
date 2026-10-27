"""Morning digest: lists cards waiting on a human."""


def render(cards):
    lines = [f"{len(cards)} waiting on you"]
    for card in cards:
        lines.append(f"- {card['title']}")
    return "\n".join(lines)


def oldest_first(cards, limit):
    """The `limit` cards that have waited longest, oldest first.

    Each card has a numeric `waiting_since` timestamp; a smaller value has
    waited longer.
    """
    ordered = sorted(cards, key=lambda c: c["waiting_since"], reverse=True)
    return ordered[:limit]
