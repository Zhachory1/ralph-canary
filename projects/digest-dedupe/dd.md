# DD: Morning digest deduplication

Covers: `projects/digest-dedupe/prd.md` (commit `d15a167`). Revision v1.

## Review header

### Decisions needed from you

1. **What a "digest" is scoped to.** The PRD says a card appears "in the digest"
   at most once per day, which reads as one global digest. If the digest is instead
   per-user or per-team, a card legitimately belongs in several people's digests on
   the same day, and a global dedup key would let the first recipient's digest
   silently consume it for everyone else. This design keys on
   `(digest_scope, card_id, digest_date)`, which is correct either way, but I need
   the real identity of `digest_scope` — one global digest, per recipient, or per
   team. Everything else here is a tuning question; this one is structural.
2. **Day boundary.** "Once per day" does not say whose day. Proposed: the digest's
   configured send timezone, reset at local midnight. The alternative, a rolling 24h
   window, makes a delayed or retried run suppress the next morning's legitimate
   mention.
3. **Aged presentation, and whether ordering may change.** "Shown as aged rather
   than repeated as if new" fixes no format. Proposed: a `blocked Nd` marker, plus
   demoting aged entries below first-time ones. The demotion is what restores the
   digest as something read, but it is a change to digest ordering that the PRD does
   not explicitly license — so it needs your approval, not my inference. Marker-only
   is the smaller option and drops the ordering work from PR 2.
4. **Whether a send ledger already exists.** I cannot see the digest implementation
   (see Assumptions). This design adds a `digest_mention` ledger. If the digest
   already records what it sent, dedup becomes a read against that record and PR 1
   shrinks to a filter.

### Council dissent

Independent reviewer delegation was unavailable in this run, so the critique below
is my own, run as three passes (correctness, PRD fidelity, operations). Treat it as
a self-review, not as three independent reviewers.

- **Dedup key, scope of.** The correctness pass accepted a global
  `(card_id, digest_date)` key. The fidelity pass rejected it: with more than one
  audience it does not deduplicate, it *loses* mentions. Resolved toward the scoped
  key, since it degenerates to the global case at no cost — but the scope's identity
  is unknown to me, so it is decision 1 above rather than a settled matter.
- **Reordering aged entries.** The correctness and operations passes treated
  demotion as part of "shown as aged". The fidelity pass called it scope the PRD
  does not grant. Unresolved, and deliberately left so: it is folded into decision 3
  instead of being shipped as implied consent.
- **Re-block within the same day.** Dissent resolved without escalating: the PRD's
  success criterion is an unconditional "one mention of each blocked card", so a
  card unblocked and re-blocked the same day stays suppressed until tomorrow. This
  was a judgment call I could make from the PRD, so I made it.

### Scope and PR breakdown

**Scope: S.** Two PRs, neither touching the digest schedule or the per-gate alert.

- **PR 1 — ledger and suppression.** Ledger store, write-on-send, suppression filter
  in assembly. Two flags, not one: write-on-send ships **on** so the ledger warms
  with real data; suppression ships **off**. Digest output is unchanged in PR 1 —
  unchanged output, not an inert no-op, since rows are accumulating.
- **PR 2 — age and rendering.** Age computation, the aged marker, ordering if
  decision 3 allows it, suppression flag flipped on against an already-warm ledger.

## Non-goals

- Changing when the digest is sent, its schedule, or its recipients.
- Changing the per-gate alert in any way, including its own dedup behaviour.
- Deduplicating anything other than blocked cards in the morning digest.
- Ever dropping an aging card out of the digest. The PRD asks for "at most once per
  day", not for an eventual stop; a card blocked for 30 days still appears on day 30.
- Backfilling the ledger for cards blocked before rollout. Day one treats every
  currently blocked card as a first mention, and ages run from rollout.

## Design

### Dedup key

`(digest_scope, card_id, digest_date)`, unique. `digest_date` is the calendar date
in the digest's send timezone at assembly time. `digest_scope` identifies the digest
audience (decision 1). The uniqueness constraint is the mechanism: a second run on
the same day finds rows already present and emits nothing, without application logic
deciding anything.

Each row also carries `spell_start`, the date the card's current blocked spell was
first mentioned, copied forward from the previous day's row. Without it, age would
have to be inferred from the contiguity of ledger rows, which breaks the first time
a digest run is skipped — a skipped day would read as a new spell and reset a
week-old card to "new".

### Assembly flow

One filter stage between candidate selection and rendering:

1. Select blocked cards as today (unchanged).
2. Deduplicate within the run in memory — a set, not a ledger concern.
3. **Read** the ledger for `(digest_scope, *, digest_date)`. Drop any candidate
   already present: it was mentioned today.
4. Render the survivors, each annotated with its age.
5. **Write** ledger rows only after the digest is successfully handed to the sender.

Read-to-filter and write-after-send are deliberately separate steps. Claiming rows at
filter time would suppress a card permanently the moment a send failed, since the
claim would outlive the message that was never delivered. Writing after the send
inverts the risk: a crash between send and write costs one duplicate mention
tomorrow. A duplicate is the exact failure the digest already tolerates today; a
silent drop is a new one, and worse.

### Age

`digest_date - spell_start`, read from the row carried forward. A card with no row
for its scope is new and renders with no marker. When a card is unblocked, its rows
are left alone and `spell_start` is simply not carried forward; a later re-block
starts a new spell at that day's date.

### Rendering

First-time entries first, aged entries below with a `blocked Nd` marker — subject to
decision 3. The top of the digest is then always new information, which is the
behaviour the PRD's problem statement is actually asking for.

### Failure modes

- **Ledger unavailable.** Fail open: send undeduplicated, log loudly. This
  *violates* the PRD's success criterion for the duration of the outage, which is an
  accepted trade, not a free one — a noisy digest is the status quo, a missing
  digest is a new regression.
- **Send timezone reconfigured.** One day may be double-mentioned or skipped at the
  change. Bounded to the day of the change. Note that ordinary DST shifts are *not*
  a hazard here: a calendar-date key in a fixed local zone still yields one date per
  day.
- **Retention shorter than a block spell.** Age depends on ledger history, so
  pruning rows faster than the longest expected spell makes an old card render as
  new. Retention is a correctness constraint, not housekeeping.
- **Card deleted mid-run.** Rows outlive the card and are pruned on retention. No
  foreign key.

## Verification

The PRD's success criterion is directly testable and is PR 1's acceptance test: run
the digest twice in one day, assert each blocked card appears exactly once across the
combined output.

PR 2: a card blocked across three simulated days appears once per day with the age
marker advancing; a skipped day in the middle does not reset age; a first-time card
renders with no marker. If decision 3 permits ordering, assert no aged entry sorts
above a first-time entry.

Multi-scope: with two digest scopes and one blocked card, both scopes mention it.
This is the regression that a global key would cause, so it is worth a standing test.

Flag-off regression: with suppression off, output matches a pre-change fixture
exactly while ledger rows accumulate.

## Assumptions

Every line is an inference of mine. The PRD and the card state none of them.

- The digest has a single configured send timezone that assembly can read.
- The digest assembles then sends as a batch, so a filter stage can sit between.
- Cards have a stable identifier usable as a ledger key across days.
- The digest has durable storage available; the ledger is a schema addition, not new
  infrastructure.
- A card's unblock is observable to the digest, so a spell can end.
- Nothing downstream of the digest depends on a blocked card being re-listed daily.
- I did not inspect the digest implementation. This repo holds documents only and the
  card scopes me to it, so component names describe roles, not verified modules, and
  the PR breakdown assumes those roles live in one service.

## Revision history

- **v1** — first draft from `prd.md` at `d15a167`.
