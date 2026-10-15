# PRD: Morning digest deduplication

## Problem
The morning digest re-lists the same blocked card every day until it is resolved.
A card blocked for a week appears seven times, so the digest stops being read.

## Outcome
A card appears in the digest at most once per day, and a card blocked for
multiple days is shown as aged rather than repeated as if new.

## Success
Running the digest twice in one day produces one mention of each blocked card.

## Non-goals
Changing when the digest is sent. Changing the per-gate alert.
