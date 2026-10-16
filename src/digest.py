"""Morning digest: lists cards waiting on a human."""


def render(cards):
    lines = [f"{len(cards)} waiting on you"]
    for card in cards:
        lines.append(f"- {card['title']}")
    return "\n".join(lines)
