# PRD: Reclaim stale card claims

## Problem
A worker that crashes leaves its card claimed. The claim has a TTL, but until
it lapses the card looks like work in progress, so nothing else picks it up and
nobody is told. A crash at the start of the day can cost most of a day.

## Outcome
A card whose worker has died returns to the queue promptly, and the operator
learns that it happened rather than discovering it later.

## Success
A card whose worker is killed is runnable again without anyone intervening,
and the event appears in the morning digest.

## Non-goals
Preventing crashes. Changing the claim TTL. Retrying the work automatically.
