# DD: Morning digest deduplication

Covers: `projects/digest-dedupe/prd.md` (commit `d15a167`). Revision v3.

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
   (see Assumptions). This design adds a `digest_mention` ledger and a `digest_run`
   record. If the digest already records what it sent and when it ran, dedup becomes
   a read against that record and PR 1 shrinks to a filter.

### Council dissent

Independent reviewer delegation was unavailable in this run, so the critique below
is my own, run as three passes (correctness, PRD fidelity, operations). Treat it as
a self-review, not as three independent reviewers. The v2 entries additionally
incorporate a review round against v1, recorded on the card.

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
  was a judgment call I could make from the PRD, so I made it. v3 extends the same
  reasoning to the card's *age*, which is the complementary case: if an earlier run
  today saw it unblocked, it still renders with its prior age rather than as new.
- **Whether spell ends are inferable at all (v2).** Review of v1 found that a
  skipped digest run and a card being unblocked are the same shape in the mention
  ledger — a gap — and argued this might be unresolvable and belong to you as a
  fifth decision. I resolved it instead, by recording runs as well as mentions: a
  gap on a date that *has* a run row means the card was not blocked, a gap on a date
  with *no* run row means the digest never looked. The residual case, a card
  unblocked and re-blocked entirely inside a stretch of skipped runs, stays
  unresolvable and is recorded as a bounded limitation under Failure modes rather
  than escalated — it overstates one card's age, it does not duplicate or drop it.
- **Whether a run earlier today should end a spell (v3).** Review of v2 showed that
  `R` silently excludes runs earlier the same day, so a card observed unblocked by
  the morning run and re-blocked before the evening run keeps its old age, and
  offered widening `R` to the most recent run preceding *this run* as the principled
  fix. I executed both before choosing. Widening is not a larger-but-better trade,
  it is unsound against a date-keyed ledger: a run earlier today shares *today's*
  mention partition, and a card surviving the suppression filter has by construction
  no mention row today — so the third branch fires unconditionally and every card a
  second run emits renders `0d`. A card blocked for a month renders as new, which
  breaks the PRD's outcome outright. Making it sound needs mention rows keyed per
  run, dissolving the `(digest_scope, card_id, digest_date)` uniqueness that is the
  dedup mechanism itself. Resolved toward the exclusion — now stated explicitly
  instead of left implicit — and toward the day as the single unit of resolution.

### Scope and PR breakdown

**Scope: S.** Re-checked at v2 after adding the run record: still S — one extra
row written per scope per day on a write path PR 1 already introduces, no new
service and no new call site. Two PRs, neither touching the digest schedule or the
per-gate alert.

- **PR 1 — ledger and suppression.** Mention ledger, run record, write-on-send,
  suppression filter in assembly. Two flags, not one: write-on-send ships **on** so
  both ledgers warm with real data; suppression ships **off**. Digest output is
  unchanged in PR 1 — unchanged output, not an inert no-op, since rows are
  accumulating. The run record must ship here and not in PR 2: PR 2's age
  resolution reads run history, so it needs history already on disk at flip time.
- **PR 2 — age and rendering.** Spell resolution, age computation, the aged marker,
  ordering if decision 3 allows it, suppression flag flipped on against an
  already-warm ledger.

### What changed since v2

One finding from review round 2, in the aging half. The dedup half is unchanged
since v1, and the round-1 rework is unchanged since v2.

- **`R` excluded runs earlier today, implicitly.** Both definitions said "before
  today" while multiple runs per day is this design's founding premise, so a card
  observed unblocked by the morning run and re-blocked by the evening one kept its
  stale age — and a sentence in Age claimed re-blocked cards are *always* caught by
  the third branch, which execution disproves. The exclusion is now stated at both
  definitions and justified; the false universal is replaced by the two cases and
  which branch each lands in; the Failure modes entry, previously scoped to gaps no
  run observed, now covers both shapes of invisible unblock; and Verification pins
  the case plus a guard against the unsound fix. Nothing about *what the code does*
  changed in this round — v2's behaviour was already this; the document now
  describes it accurately and says why it is the right behaviour.

### What changed in v2 (review round 1)

Three findings, all in the aging half; the dedup half was unchanged.

- **Spell resolution, the load-bearing gap.** v1 never read a prior row, so age had
  no input, and it described `spell_start` three inconsistent ways. v2 adds the
  `digest_run` record, states the resolution rule explicitly (Age), adds it to the
  Assembly flow as step 3, and reconciles the Dedup key, Age and Verification
  passages to one behaviour. Consequence: the v1 assumption "a card's unblock is
  observable to the digest" is **deleted** — the design no longer needs it, since
  spell ends are now inferred from run history alone.
- **PR 1's "output unchanged" claim.** Within-run dedup (flow step 2) sat behind no
  flag while PR 1 claimed unchanged output. I cannot inspect candidate selection, so
  I will not assert it never double-emits; instead step 2 is now gated by the
  suppression flag, which makes PR 1's claim true either way.
- **Fail-open.** Kept as an author decision, not promoted; one line added to Failure
  modes justifying why it is settleable where decision 3's ordering question is not.

## Non-goals

- Changing when the digest is sent, its schedule, or its recipients.
- Changing the per-gate alert in any way, including its own dedup behaviour.
- Deduplicating anything other than blocked cards in the morning digest.
- Ever dropping an aging card out of the digest. The PRD asks for "at most once per
  day", not for an eventual stop; a card blocked for 30 days still appears on day 30.
- Backfilling the ledger for cards blocked before rollout. Day one treats every
  currently blocked card as a first mention, and ages run from rollout.
- Reconstructing block history from any source other than the digest's own run
  record — no card audit log, no blocked-state history table.

## Design

### Dedup key

`(digest_scope, card_id, digest_date)`, unique. `digest_date` is the calendar date
in the digest's send timezone at assembly time. `digest_scope` identifies the digest
audience (decision 1). The uniqueness constraint is the mechanism: a second run on
the same day finds rows already present and emits nothing, without application logic
deciding anything.

Each row also carries `spell_start`, the date the card's current blocked spell was
first mentioned, carried forward from the card's row on the most recent *prior date
with a recorded run* (see Age) rather than from the previous calendar day.

### Run record

`(digest_scope, digest_date)`, unique, written at the same point and in the same
transaction as that run's mention rows. It records that a digest for this scope
actually assembled and sent on this date.

This exists to make absence meaningful. Without it, a card having no row for a date
is ambiguous between "the card was not blocked" and "the digest did not run", and
those two demand opposite answers: the first ends a spell, the second must not
disturb it. With it, a date carrying a run row is a date on which the digest saw
every blocked card, so a card missing from it was demonstrably not blocked.

### Assembly flow

One filter stage between candidate selection and rendering:

1. Select blocked cards as today (unchanged).
2. Deduplicate within the run in memory — a set, not a ledger concern. Gated by the
   suppression flag, so PR 1 cannot alter output.
3. **Read** the ledger twice: once for `(digest_scope, *, digest_date)` to drop
   candidates already mentioned today, and once for `(digest_scope, *, R)` where `R`
   is the most recent recorded run date strictly before `digest_date`, to resolve
   each surviving candidate's `spell_start`. Runs earlier *today* are deliberately
   excluded from `R` — see Age.
4. Render the survivors, each annotated with its age.
5. **Write** mention rows and this run's record, only after the digest is
   successfully handed to the sender.

Read-to-filter and write-after-send are deliberately separate steps. Claiming rows at
filter time would suppress a card permanently the moment a send failed, since the
claim would outlive the message that was never delivered. Writing after the send
inverts the risk: a crash between send and write costs one duplicate mention
tomorrow. A duplicate is the exact failure the digest already tolerates today; a
silent drop is a new one, and worse. Because the run record is written in the same
transaction, that crash also leaves no run row — so the lost day reads as a skipped
day, not as a day on which nothing was blocked, and no card's age resets because of
it.

### Age

`digest_date - spell_start`, with `spell_start` resolved per card at step 3 by one
rule. Let `R` be the most recent date strictly before `digest_date` with a run
record for this scope. Runs recorded earlier on `digest_date` itself are excluded
from `R`; the paragraph after the branches explains why.

- No `R` at all — first run for this scope, including the first run after rollout:
  the card is new, `spell_start = digest_date`, no marker.
- A mention row exists for `(digest_scope, card_id, R)`: the spell is continuous,
  `spell_start` is copied from that row.
- No mention row for `(digest_scope, card_id, R)`: that run ran and did not mention
  the card, so the card was not blocked then. Any earlier spell has ended. This is a
  new spell, `spell_start = digest_date`, no marker.

Only the single most recent run date is consulted, so the rule is one indexed read
and does not walk history. Skipped days need no special case: they leave no run
record, so `R` steps over them and a week-old card stays a week old.

**The day is the unit of resolution, and `R` excludes every run earlier today.**
A card re-blocked after an unblock that an *earlier day's* run observed is caught by
the third branch, because that run recorded itself without the card. A card
re-blocked after an unblock that only a *same-day* run observed is not: `R` has
already stepped back past today, so the second branch finds yesterday's mention row
and the spell reads as continuous. The card is still mentioned once, on the right
day, with a marker; only the integer is older than the spell.

This exclusion is deliberate, not an oversight inherited from the schema. Mention
rows are keyed by date, so a run earlier today shares *today's* partition — and a
card that survived step 3's suppression filter has, by construction, no mention row
today. Letting `R` name a same-day run would therefore send every such card down the
third branch unconditionally, rendering a month-old card as `0d`. Resolving
same-day spell ends correctly would require mention rows keyed per run rather than
per date, which dissolves the `(digest_scope, card_id, digest_date)` uniqueness that
performs the deduplication the PRD actually asks for. Day-granular resolution also
buys an invariant worth keeping: the age a card renders on a given day does not
depend on which run of that day emits it.

Unblocking is never written anywhere and no row is ever mutated or deleted on
unblock; a spell's end is inferred, not recorded.

### Rendering

First-time entries first, aged entries below with a `blocked Nd` marker — subject to
decision 3. The top of the digest is then always new information, which is the
behaviour the PRD's problem statement is actually asking for.

### Failure modes

- **Ledger unavailable.** Fail open: send undeduplicated, log loudly, write neither
  mention rows nor a run record, so the outage reads downstream as skipped days and
  no age resets. This *violates* the PRD's success criterion for the duration of the
  outage, which is an accepted trade, not a free one — a noisy digest is the status
  quo, a missing digest is a new regression. I settled this rather than raising it
  alongside decision 3 because its worst case is precisely today's behaviour, which
  the reader already lives with and which reverts the moment the ledger returns,
  whereas decision 3 changes what a healthy digest looks like every single day.
- **An unblock the age rule cannot see.** Two shapes, one symptom: the spell looks
  continuous and the age is overstated. (a) The card is unblocked and re-blocked
  entirely within a stretch of *skipped* runs — no run observed the gap at all.
  Overstated by the length of the gap. (b) The card is unblocked and re-blocked
  within a *single day*, where a run earlier today did observe it unblocked but `R`
  excludes same-day runs by design (see Age). Overstated by the length of the spell
  that preceded it. Both accepted and bounded identically: the card is still
  mentioned once, on the correct day, with a marker — only the number is stale, and
  only until the next unblock that a prior day's run observes. Narrowing (a) would
  require a block-history source outside the digest, which is a non-goal; narrowing
  (b) would require per-run mention keys, which would dissolve the dedup key itself.
- **Send timezone reconfigured.** One day may be double-mentioned or skipped at the
  change. Bounded to the day of the change. Note that ordinary DST shifts are *not*
  a hazard here: a calendar-date key in a fixed local zone still yields one date per
  day.
- **Retention shorter than a block spell.** Age depends on the chain of carried-
  forward `spell_start` values and on run records surviving, so pruning either faster
  than the longest expected spell makes an old card render as new. Retention is a
  correctness constraint on both tables, not housekeeping.
- **Card deleted mid-run.** Rows outlive the card and are pruned on retention. No
  foreign key.

## Verification

The PRD's success criterion is directly testable and is PR 1's acceptance test: run
the digest twice in one day, assert each blocked card appears exactly once across the
combined output.

PR 2, age resolution — the four branches of the rule, each as a case:

- A card blocked across three consecutive simulated days appears once per day with
  the marker advancing 0d, 1d, 2d.
- A skipped day in the middle (no run record written) does not reset age: day 1 and
  day 3 run, day 2 does not, and day 3 renders 2d.
- A day that runs and omits the card *does* reset it: blocked days 1-2, unblocked
  day 3 with a run recorded, re-blocked day 4 renders as new with no marker.
- A first-time card, and the first run after rollout, render with no marker.
- **Same-day re-block does *not* reset** (the documented exclusion, pinned so a
  future change cannot alter it silently): blocked days 1-3, day 4 run 1 records a
  run with the card absent, day 4 run 2 re-blocks it — renders `3d`, not `0d`. Pair
  it with a regression guard that a card blocked for 30 days never renders `0d` from
  a second same-day run, which is what widening `R` to same-day runs would cause.

Multi-scope: with two digest scopes and one blocked card, both scopes mention it.
This is the regression that a global key would cause, so it is worth a standing test.

Crash-after-send: with mention rows and run record both absent for a date, the next
run neither resets any age nor treats that date as evidence of unblocking.

Flag-off regression: with suppression off, output matches a pre-change fixture
exactly — including a fixture where candidate selection emits a card twice, since
step 2 is gated too — while ledger rows accumulate.

## Assumptions

Every line is an inference of mine. The PRD and the card state none of them.

- The digest has a single configured send timezone that assembly can read.
- The digest assembles then sends as a batch, so a filter stage can sit between.
- Cards have a stable identifier usable as a ledger key across days.
- The digest has durable storage available, with transactions spanning the two
  writes at step 5; the ledgers are a schema addition, not new infrastructure.
- A digest run that sends successfully can record that fact at the same point it
  records its mentions.
- Candidate selection returns every currently blocked card in scope on every run, so
  a card's absence from a recorded run means it was not blocked.
- Nothing downstream of the digest depends on a blocked card being re-listed daily.
- I did not inspect the digest implementation. This repo holds documents only and the
  card scopes me to it, so component names describe roles, not verified modules, and
  the PR breakdown assumes those roles live in one service.

## Revision history

- **v1** — first draft from `prd.md` at `d15a167`.
- **v2** — review round 1 rework: added the `digest_run` record and an explicit
  `spell_start` resolution rule, gated within-run dedup behind the suppression flag,
  justified fail-open as author-settled. Dropped the "unblock is observable"
  assumption, now unnecessary.
- **v3** — review round 2 rework: made `R`'s exclusion of same-day runs explicit at
  both definitions and justified it against the alternative, replaced a false
  universal claim about re-blocked cards, widened the Failure modes entry to both
  shapes of invisible unblock, pinned the case in Verification. Document-only; no
  behaviour described in v2 changed.
