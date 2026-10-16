# DD: Morning digest deduplication

## Section 2: suppression filter

Add `suppress_seen(cards, seen)` to `src/digest.py`.

It takes the card list and a set of card ids already mentioned today, and
returns only the cards whose id is not in that set. Order is preserved.
A card with no id is always kept: dropping an unidentifiable card is worse
than mentioning it twice.

`render` is unchanged. Callers wire the filter in a later PR.

## Non-goals

Changing when the digest is sent. Persisting the seen set. Touching `render`.
