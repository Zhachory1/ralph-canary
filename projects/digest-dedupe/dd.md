# DD: Morning digest deduplication

Covers: `projects/digest-dedupe/prd.md` (commit `d15a167`). Revision v4.

## Review header

### Decisions needed from you

1. **Day boundary.** "Once per day" does not say whose day. With a single recipient
   that is now settled — it is your day. What is still open is the reset: your local
   midnight (proposed) or a rolling 24h window. A rolling window lets a delayed or
   retried run suppress the next morning's legitimate mention, which is why I propose
   local midnight, but the trade is yours to take.
2. **Whether a send ledger already exists.** I cannot see the digest implementation
   (see Assumptions). This design adds a `digest_mention` ledger and a `digest_run`
   record. If the digest already records what it sent and when it ran, dedup becomes
   a read against that record and PR 1 shrinks to a filter.

### Decisions you resolved

Both answered on PR #3 and applied here as fact, not inferred from silence.

- **Digest scope — global, single recipient** (decision 1 in v1–v3). The dedup key is
  now `(card_id, digest_date)` and the run record `(digest_date)`; the `digest_scope`
  column is gone from both tables, from the Assembly flow and from Age. The
  multi-scope regression test is deleted with it — the regression it guarded against,
  a global key consuming a card for every recipient but the first, cannot occur when
  there is one recipient. This was the structural decision; it is now closed.
- **Aged presentation — marker only** (decision 3 in v1–v3). Aged entries render with
  a `blocked Nd` marker in the position they would have had anyway. All ordering work
  is dropped from PR 2, and Rendering no longer claims the top of the digest is always
  new information: the marker carries the PRD's Outcome clause on its own. Ordering is
  now an explicit non-goal, so a later revision cannot reintroduce it by drift.

### Council dissent

Independent reviewer delegation was unavailable in this run, so the critique below is
my own, run as three passes (correctness, PRD fidelity, operations), plus three review
rounds recorded on the card. Treat it as a self-review, not as three reviewers.

- **Dedup key, scope of.** The correctness pass accepted a global key; the fidelity
  pass rejected it, since with more than one audience it does not deduplicate, it
  *loses* mentions. v1–v3 held a scoped key and escalated the scope's identity rather
  than guessing. You have now answered global, which makes the fidelity pass's
  objection moot on the facts rather than overruled — it was contingent on multiple
  audiences, and there are none. The key collapses accordingly.
- **Reordering aged entries.** The correctness and operations passes treated demotion
  as part of "shown as aged"; the fidelity pass called it scope the PRD does not
  grant. The fidelity pass prevailed, by your decision rather than by my argument.
  Holding this open instead of shipping it as implied consent was the right call.
- **Re-block within the same day.** Resolved without escalating: the PRD's success
  criterion is an unconditional "one mention of each blocked card", so a card
  unblocked and re-blocked the same day stays suppressed until tomorrow. v3 extends
  the same reasoning to the card's *age* — if an earlier run today saw it unblocked,
  it still renders with its prior age rather than as new.
- **Whether spell ends are inferable at all (v2).** Review of v1 found that a skipped
  run and an unblock are the same shape in the mention ledger — a gap — and argued it
  might belong to you as a fifth decision. I resolved it instead by recording runs as
  well as mentions: a gap on a date that *has* a run row means the card was not
  blocked; a gap on a date with *no* run row means the digest never looked. The
  residual case is recorded under Failure modes, not escalated.
- **Whether a run earlier today should end a spell (v3).** Review of v2 showed `R`
  silently excludes same-day runs, and offered widening it as the principled fix. I
  executed both options before choosing. Widening is not a larger-but-better trade, it
  is unsound against a date-keyed ledger: a card surviving the suppression filter has
  by construction no mention row today, so the third branch would fire unconditionally
  and a month-old card would render `0d`. Resolved toward the exclusion, now stated
  explicitly, and toward the day as the single unit of resolution.

### Scope and PR breakdown

**Scope: S.** Re-checked at v4 and now slightly smaller than v3: one fewer column on
both tables, no scope resolution at assembly, and no ordering change in PR 2. Two PRs,
neither touching the digest schedule, the per-gate alert, or entry order.

- **PR 1 — ledger and suppression.** Mention ledger, run record, write-on-send,
  suppression filter in assembly. Two flags, not one: write-on-send ships **on** so
  both tables warm with real data; suppression ships **off**. Digest output is
  unchanged in PR 1 — unchanged output, not an inert no-op, since rows are
  accumulating. The run record must ship here and not in PR 2: PR 2's age resolution
  reads run history, so it needs history already on disk at flip time.
- **PR 2 — age and marker.** Spell resolution, age computation, the `blocked Nd`
  marker, suppression flag flipped on against an already-warm ledger. No change to
  entry order or grouping.

### What changed since v3

Your two answers applied, and the changelog compressed. No design question was
reopened and no new inference was introduced this round.

- **Scope collapsed to global.** `digest_scope` removed from the dedup key, the run
  record, the Assembly flow, Age and Failure modes. The multi-scope standing test is
  deleted, and decision 1 is closed rather than restated.
- **Ordering dropped.** Rendering now specifies marker-only in place, ordering is an
  explicit non-goal, PR 2 loses the ordering work, and Verification gains a guard that
  entry order matches the pre-change fixture exactly.
- **Changelog compressed.** The per-round "what changed in v2 / since v2" sections are
  folded into Revision history at the bottom; nothing they recorded is lost, and the
  review header no longer carries three rounds of rework narrative a new reader does
  not need.

## Non-goals

- Changing when the digest is sent, its schedule, or its recipient.
- Changing the per-gate alert in any way, including its own dedup behaviour.
- Changing the order, grouping or position of digest entries. An aged entry stays
  where it would have been; only a marker is added.
- Deduplicating anything other than blocked cards in the morning digest.
- Ever dropping an aging card out of the digest. The PRD asks for "at most once per
  day", not for an eventual stop; a card blocked for 30 days still appears on day 30.
- Backfilling the ledger for cards blocked before rollout. Day one treats every
  currently blocked card as a first mention, and ages run from rollout.
- Reconstructing block history from any source other than the digest's own run record
  — no card audit log, no blocked-state history table.

## Design

### Dedup key

`(card_id, digest_date)`, unique. `digest_date` is the calendar date in the digest's
send timezone at assembly time. There is one digest and one recipient, so no audience
component is needed. The uniqueness constraint is the mechanism: a second run on the
same day finds rows already present and emits nothing, without application logic
deciding anything.

Each row also carries `spell_start`, the date the card's current blocked spell was
first mentioned, carried forward from the card's row on the most recent *prior date
with a recorded run* (see Age) rather than from the previous calendar day.

### Run record

`(digest_date)`, unique, written at the same point and in the same transaction as that
run's mention rows. It records that a digest actually assembled and sent on this date.

This exists to make absence meaningful. Without it, a card having no row for a date is
ambiguous between "the card was not blocked" and "the digest did not run", and those
two demand opposite answers: the first ends a spell, the second must not disturb it.
With it, a date carrying a run row is a date on which the digest saw every blocked
card, so a card missing from it was demonstrably not blocked.

### Assembly flow

One filter stage between candidate selection and rendering:

1. Select blocked cards as today (unchanged).
2. Deduplicate within the run in memory — a set, not a ledger concern. Gated by the
   suppression flag, so PR 1 cannot alter output.
3. **Read** the ledger twice: once for `(*, digest_date)` to drop candidates already
   mentioned today, and once for `(*, R)` where `R` is the most recent recorded run
   date strictly before `digest_date`, to resolve each surviving candidate's
   `spell_start`. Runs earlier *today* are deliberately excluded from `R` — see Age.
4. Render the survivors in their existing order, each annotated with its age.
5. **Write** mention rows and this run's record, only after the digest is successfully
   handed to the sender.

Read-to-filter and write-after-send are deliberately separate steps. Claiming rows at
filter time would suppress a card permanently the moment a send failed, since the
claim would outlive the message that was never delivered. Writing after the send
inverts the risk: a crash between send and write costs one duplicate mention tomorrow.
A duplicate is the exact failure the digest already tolerates today; a silent drop is
a new one, and worse. Because the run record is written in the same transaction, that
crash also leaves no run row — so the lost day reads as a skipped day, not as a day on
which nothing was blocked, and no card's age resets because of it.

### Age

`digest_date - spell_start`, with `spell_start` resolved per card at step 3 by one
rule. Let `R` be the most recent date strictly before `digest_date` with a run record.
Runs recorded earlier on `digest_date` itself are excluded from `R`; the paragraph
after the branches explains why.

- No `R` at all — the first run ever, including the first run after rollout: the card
  is new, `spell_start = digest_date`, no marker.
- A mention row exists for `(card_id, R)`: the spell is continuous, `spell_start` is
  copied from that row.
- No mention row for `(card_id, R)`: that run ran and did not mention the card, so the
  card was not blocked then. Any earlier spell has ended. This is a new spell,
  `spell_start = digest_date`, no marker.

Only the single most recent run date is consulted, so the rule is one indexed read and
does not walk history. Skipped days need no special case: they leave no run record, so
`R` steps over them and a week-old card stays a week old.

**The day is the unit of resolution, and `R` excludes every run earlier today.** A
card re-blocked after an unblock that an *earlier day's* run observed is caught by the
third branch, because that run recorded itself without the card. A card re-blocked
after an unblock that only a *same-day* run observed is not: `R` has already stepped
back past today, so the second branch finds yesterday's mention row and the spell
reads as continuous. The card is still mentioned once, on the right day, with a
marker; only the integer is older than the spell.

This exclusion is deliberate, not an oversight inherited from the schema. Mention rows
are keyed by date, so a run earlier today shares *today's* partition — and a card that
survived step 3's suppression filter has, by construction, no mention row today.
Letting `R` name a same-day run would therefore send every such card down the third
branch unconditionally, rendering a month-old card as `0d`. Resolving same-day spell
ends correctly would require mention rows keyed per run rather than per date, which
dissolves the `(card_id, digest_date)` uniqueness that performs the deduplication the
PRD actually asks for. Day-granular resolution also buys an invariant worth keeping:
the age a card renders on a given day does not depend on which run of that day emits
it.

Unblocking is never written anywhere and no row is ever mutated or deleted on unblock;
a spell's end is inferred, not recorded.

### Rendering

Entry order is unchanged. An aged entry renders in the position it would occupy today,
with a `blocked Nd` marker appended; a first-time entry renders exactly as it does
today, with no marker. Nothing is reordered, regrouped or removed, so the only
difference in a rendered digest is marker text on entries that have been seen before.

### Failure modes

- **Ledger unavailable.** Fail open: send undeduplicated, log loudly, write neither
  mention rows nor a run record, so the outage reads downstream as skipped days and no
  age resets. This *violates* the PRD's success criterion for the duration of the
  outage, which is an accepted trade, not a free one — a noisy digest is the status
  quo, a missing digest is a new regression. I settled this rather than escalating it
  because its worst case is precisely today's behaviour, which you already live with
  and which reverts the moment the ledger returns.
- **An unblock the age rule cannot see.** Two shapes, one symptom: the spell looks
  continuous and the age is overstated. (a) The card is unblocked and re-blocked
  entirely within a stretch of *skipped* runs — no run observed the gap at all.
  Overstated by the length of the gap. (b) The card is unblocked and re-blocked within
  a *single day*, where a run earlier today did observe it unblocked but `R` excludes
  same-day runs by design (see Age). Overstated by the length of the spell that
  preceded it. Both accepted and bounded identically: the card is still mentioned
  once, on the correct day, with a marker — only the number is stale, and only until
  the next unblock that a prior day's run observes. Narrowing (a) would require a
  block-history source outside the digest, which is a non-goal; narrowing (b) would
  require per-run mention keys, which would dissolve the dedup key itself.
- **Send timezone reconfigured.** One day may be double-mentioned or skipped at the
  change. Bounded to the day of the change. Ordinary DST shifts are *not* a hazard
  here: a calendar-date key in a fixed local zone still yields one date per day.
- **Retention shorter than a block spell.** Age depends on the chain of carried-forward
  `spell_start` values and on run records surviving, so pruning either faster than the
  longest expected spell makes an old card render as new. Retention is a correctness
  constraint on both tables, not housekeeping.
- **Card deleted mid-run.** Rows outlive the card and are pruned on retention. No
  foreign key.

## Verification

The PRD's success criterion is directly testable and is PR 1's acceptance test: run
the digest twice in one day, assert each blocked card appears exactly once across the
combined output.

PR 2, age resolution — the branches of the rule, each as a case:

- A card blocked across three consecutive simulated days appears once per day with the
  marker advancing 0d, 1d, 2d.
- A skipped day in the middle (no run record written) does not reset age: day 1 and
  day 3 run, day 2 does not, and day 3 renders 2d.
- A day that runs and omits the card *does* reset it: blocked days 1-2, unblocked day
  3 with a run recorded, re-blocked day 4 renders as new with no marker.
- A first-time card, and the first run after rollout, render with no marker.
- **Same-day re-block does *not* reset** (the documented exclusion, pinned so a future
  change cannot alter it silently): blocked days 1-3, day 4 run 1 records a run with
  the card absent, day 4 run 2 re-blocks it — renders `3d`, not `0d`. Pair it with a
  regression guard that a card blocked for 30 days never renders `0d` from a second
  same-day run, which is what widening `R` to same-day runs would cause.

Ordering stability: with suppression and marker both on, rendered entry order matches
the pre-change fixture exactly and the only diff is appended marker text. This pins
your marker-only decision so reordering cannot return as an unreviewed refinement.

Crash-after-send: with mention rows and run record both absent for a date, the next
run neither resets any age nor treats that date as evidence of unblocking.

Flag-off regression: with suppression off, output matches a pre-change fixture exactly
— including a fixture where candidate selection emits a card twice, since step 2 is
gated too — while ledger rows accumulate.

## Assumptions

Every line is an inference of mine. The PRD and the card state none of them. Nothing
was added in v4.

- The digest has a single configured send timezone that assembly can read.
- The digest assembles then sends as a batch, so a filter stage can sit between.
- Cards have a stable identifier usable as a ledger key across days.
- The digest has durable storage available, with transactions spanning the two writes
  at step 5; the ledgers are a schema addition, not new infrastructure.
- A digest run that sends successfully can record that fact at the same point it
  records its mentions.
- Candidate selection returns every currently blocked card on every run, so a card's
  absence from a recorded run means it was not blocked.
- Nothing downstream of the digest depends on a blocked card being re-listed daily.
- Appending a marker to an existing entry does not itself disturb entry order in the
  renderer — the marker is text on a line, not a sort key.
- I did not inspect the digest implementation. This repo holds documents only and the
  card scopes me to it, so component names describe roles, not verified modules, and
  the PR breakdown assumes those roles live in one service.

## Revision history

- **v1** — first draft from `prd.md` at `d15a167`.
- **v2** — review round 1 rework: added the `digest_run` record and an explicit
  `spell_start` resolution rule (v1 never read a prior row, so age had no input and
  `spell_start` was described three inconsistent ways), gated within-run dedup behind
  the suppression flag so PR 1's "output unchanged" claim holds either way, justified
  fail-open as author-settled. Dropped the "unblock is observable to the digest"
  assumption as no longer needed.
- **v3** — review round 2 rework: made `R`'s exclusion of same-day runs explicit at
  both definitions and justified it against the alternative, replaced a false universal
  claim that re-blocked cards are always caught by the third branch, widened the
  Failure modes entry to both shapes of invisible unblock, pinned the case in
  Verification with a guard against the unsound fix. Document-only; no behaviour
  described in v2 changed.
- **v4** — your decisions applied: scope collapsed to a global `(card_id, digest_date)`
  key with `digest_scope` and the multi-scope test removed, and aged presentation
  reduced to a marker in place with ordering dropped from PR 2 and added to Non-goals.
  Per-round changelog sections folded into this history.
