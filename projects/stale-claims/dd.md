# DD: Reclaim stale card claims

Covers: `projects/stale-claims/prd.md` (commit `d76e794`). Revision v3.

## Review header

### Decisions you resolved

- **The cap** (v2 decision 1), 2026-10-16: three consecutive reclaims, then park the card.
  Designed in §The cap. Two sub-questions your answer did not cover — what resets the
  count, and how a parked card is encoded — I answered myself; both are flagged where they
  are made, and the second is now decision 4.
- **Compare-and-set** (v2 decision 2a), 2026-10-16: SQLite, conditional writes, single
  transaction. Applied: the sweep is written against SQLite semantics, and the assumption
  is retired. The answer also disclosed the store's identity, which has three consequences
  the question did not cover — see What changed, and decision 2.

### Decisions needed from you

1. **How wide the fence goes** (decision 3 in v2). Fence every write that can mutate a
   claimed card, or only the transitions that can corrupt its terminal state — completion,
   block, status change — leaving comments, progress and logs unfenced.
   *Wide* is the guarantee, and a system-wide concurrency retrofit; it is the largest
   single driver of the L rating. *Narrow* is most of the safety for a fraction of the
   audit, and gives up the guarantee: a zombie that can still write *something* can still
   confuse an operator reading the card. Narrow does not by itself make this an M — the
   outcome-reader audit and the cap stay either way.
   I propose narrow for PR 2, with the wide audit as follow-up this PRD does not own.
2. **Three facts only you can supply.**
   (a) **Does anything but the dispatcher's host write claims?** This is the load-bearing
   one, and it is new because your SQLite answer created it. *Single-host:* everything
   below holds, and `journal_mode=WAL` is available, which this design depends on.
   *Multi-host:* WAL is unavailable by construction — SQLite documents that all processes
   using a WAL database must be on one host — so readers block the writer, and if the file
   is reached over a network filesystem SQLite's locking is documented as unreliable, which
   takes out the compare-and-set safety argument and not merely the clock comparison.
   There is no store-side clock either way: `now` is evaluated by whichever process runs
   the statement. If the answer is multi-host, liveness has to be *counted* rather than
   timed — a monotonic mark sequence the sweep watches for movement across ticks — which is
   a different §Mechanism, and I would rather re-open it than paper over it.
   (b) **Do claims already carry a liveness signal?** If so, PR 1 collapses to seeding and
   the epoch.
   (c) **Is the claim TTL really hours?** The PRD's "most of a day" implies it, and the
   premise that a three-minute `T_stale` is meaningfully prompter rests on it. If the TTL
   is already minutes, the Problem section means something other than what I read.
3. **Where a reclaimed card lands in the queue** (decision 4 in v2). Keep the queue's
   existing ordering, or restore the card's original position.
   If the ordering is enqueue time, a card claimed at 09:00 and reclaimed at 09:03 sorts
   behind everything queued in between — reclaimed in three minutes, dispatched hours
   later, the PRD's day lost anyway. Restoring position fixes that by pushing other
   people's cards back. That is a call about whose work waits, which is yours.
4. **Whether a parked card gets its own status.** New in v3, and cheap now, expensive after
   PR 5 ships. *Reuse `blocked`* (my proposal): it already means "not dispatched until a
   human acts", and it avoids a second system-wide enum audit on top of PR 3's. The cost is
   that a parked card is indistinguishable *by status* from a human-blocked one, so
   dashboards counting blocked work absorb it and an operator needs the reason string to
   tell them apart. *A distinct `parked` status*: legible at a glance, at the price of that
   audit. The functional half of this — how the unblock path knows to reset the count — I
   removed as a consideration by making the reset unconditional (§The cap), so this is now
   purely about legibility, which is why it is a short question rather than a long one.

### Council dissent

Where the critics disagreed with me or with each other. Findings I accepted are in What
changed, not here.

- **Fence extent** (v2, unresolved). My own fidelity pass called fencing scope the PRD does
  not grant; the fidelity reviewer agreed it is forced in substance — prompt reclaim
  necessarily acts on inference, so something must stop the mistakenly-reclaimed worker —
  but held that its *extent* is a scope dispute the author should escalate, not settle.
  Decision 1.
- **Reusing `blocked` for a parked card** (v3). One reviewer wanted a distinct status for
  legibility; a second argued the choice was reversible-now and expensive-later, which is
  the profile of a decision rather than a dissent footnote. I agreed with the second and
  escalated it as decision 4, keeping `blocked` as the proposal.
- **Whether the cap resets on a failed run** (v3). The reviewers split: one read "no
  completed run in between" as a *successful* run, the other as any run that ended under
  its own power. I took the second — a run that fails on the work's merits proves the card
  does not kill workers, which is the only thing the cap measures, and requiring success
  would park a legitimately failing card after three honest failures. This reading has a
  residual: it leaves the alternating loop in Failure modes, stated there rather than
  buried here. The clause is my interpretation, not your words; it is the part of §The cap
  most worth your eye.
- **An immediate alert for a parked card** (v2 withdrawn; v3 re-examined and closed). A
  reviewer found the real defect behind this: under cursor rendering a park produced one
  digest line on one morning and then nothing ever again, because the cursor renders events
  and a park is a *state*. That is the PRD's own failure — nobody is told — rebuilt by the
  mechanism meant to stop a loop. Fixed by rendering parked cards as a standing block
  (§Digest surfacing), which makes the card visible every morning until a human acts. The
  residual is latency: a card parked at 09:05 is first seen the next morning. I am not
  re-opening the alert decision over that, and I am not inviting an answer I called a
  non-goal two pages later. It stays a non-goal.
- **The circuit breaker** (v2). The operations reviewer wanted one; the fidelity objection
  is that the PRD never asked. I took it, and the argument that moved me is the clock, not
  volume: a forward jump makes every live claim look stale at once, and without a breaker
  this feature turns one bad timestamp into a system-wide outage. Still the thing here most
  open to a charge of creep.
- **Where the mark is emitted from** (v2). Not a disagreement, a straight error: v1 offered
  "a timer thread, or the wrapper supervising it" as interchangeable, and a wrapper
  survives its child's crash. Fixed and inverted into a requirement.

### Scope and PR breakdown

**Scope: L.** Six PRs, up from v2's five: your cap decision adds work in two paths the
sweep does not touch (run completion, and the unblock path), carries its own schema change,
and gates the sweep's production flag, so it is its own landing rather than a rider on
PR 4. Two call-site-wide audits remain the bulk of the estimate: every write path that can
mutate a claimed card, and every reader of the run outcome field. Nothing here changes the
claim TTL, the dispatcher's selection logic, or any existing digest entry.

- **PR 1 — liveness data.** `claim_epoch` and `liveness_at` added to the claim, stamped at
  claim time and on every mark, the mark emitted from a run-lifetime context. Nothing reads
  either field; behaviour is genuinely unchanged.
- **PR 2 — fencing.** The epoch carried on fenced writes and checked by the store, in two
  steps behind one flag: **observe** (mismatches logged and counted, writes allowed) until
  the counter is quiet, then **enforce**. Worker aborts its run on the first rejection,
  including on the mark path — on a true rejection only, never on a busy store or an
  unknown card (§Fencing). Extent is decision 1.
- **PR 3 — the `reclaimed` run outcome.** The value, plus the audit of every reader of that
  field. Nothing writes it yet. Deliberately separate so the audit has an owner.
- **PR 4 — the sweep.** Stale detection, the reclaim transaction, the reclaim record table,
  the circuit breaker, failure isolation, config validation. Behind a flag.
- **PR 5 — the cap and park.** A card-level `consecutive_reclaims` column, the two record
  columns that snapshot it, the park transition, and both reset paths.
- **PR 6 — digest entry.** Reclaim lines read by cursor, the standing parked block, and the
  sweep health footer. Delivers the Success criterion's second half.

**The sweep's production flag is flipped only when all four hold:** liveness data is warm,
the fence is in **enforce**, PR 5 has landed, and PR 6 has landed. The first because a
sweep against cold liveness data reclaims the living; the second because observe-mode
fencing is operationally identical to no fence, and a sweep without fencing converts a
false reclaim into two live writers; the third because the loop the cap stops is live from
the moment the sweep is on; the fourth because between the sweep going live and the digest
existing, cards are reclaimed and parked and the operator learns nothing — which is the
PRD's Outcome unmet in production, by design, for the length of a PR.

The rest of the order is forced: a sweep that writes an outcome value nothing has audited
breaks whatever counts failures, and a digest has nothing to read until records exist.

### What changed in v3

Your two answers, applied — and the second opened more than it closed.

- **The cap is designed, not escalated.** §The cap specifies a new card-level
  `consecutive_reclaims` column, who increments it and in which transaction, the park
  transition, and both resets. It also states what the cap costs: on the third reclaim the
  card does not return to the queue, so the PRD's Success criterion — runnable again
  without anyone intervening — is bounded to at most three consecutive reclaims per card.
- **Compare-and-set is a fact, not an assumption.** `BEGIN IMMEDIATE`, a conditional
  `UPDATE` whose match is read from the change count, four writes in one transaction.
- **Four consequences of that answer the question did not cover.** (a) No server clock, and
  WAL's single-host constraint — decision 2a, the biggest open risk in this revision.
  (b) SQLite serialises writers, so the sweep takes one short transaction per candidate
  over a bounded batch; an outage-sized batch under one lock would block every worker's
  mark and manufacture the staleness it is reclaiming. (c) `SQLITE_BUSY` is not a fence
  rejection, and neither is a write to a card that is not there; conflating either with
  rejection kills healthy workers, since the worker aborts on rejection. (d) Parking makes
  a card blocked, so the same card can qualify for two digest entry classes on one morning
  — the `digest-dedupe` collision got worse, not better (§Digest surfacing).
- **The park was invisible after one morning.** Cursor rendering is right for reclaims,
  which are events, and wrong for parks, which are states. Parked cards are now a standing
  block that persists until a human acts.
- **The breaker could not see a storm.** v3's own bounded batch truncated the candidate set
  the breaker measures. The breaker now reads the full candidate count from the scan,
  before truncation.
- **New assumptions**, all listed: `journal_mode=WAL` with a busy timeout; the blocked
  state's three properties, which the park depends on; that the card row can gain a column;
  that reclaim records are never pruned, which is what the cursor's exactly-once rests on.
- **Decisions:** two of v2's four closed by you, two carried (renumbered: v2's 3 is now 1,
  v2's 4 is now 3), two added — the deployment question (2a) and the parked-status
  encoding (4). Four open.
- **Scope: 5 PRs → 6**, and the flag-flip gate is now four conditions, not one. v2's gate
  silently lost the enforcing-fence clause in an earlier draft of this revision.
- **Trimmed.** v2's changelog moved to Revision history.

## Non-goals

- Preventing crashes (PRD).
- Changing the claim TTL — its value, its semantics, or its effective length by any indirect
  route (PRD). `T_stale` is a separate parameter on a separate signal, and the TTL remains
  the sole governor of claims the sweep skips.
- Retrying the work automatically (PRD): no backoff, no re-execution policy, no second
  attempt this design asks for. The cap is the one place a counter now gates dispatch, and
  it only ever *withholds* a card — but it does gate dispatch, which v2's version of this
  bullet denied, and on the third consecutive reclaim the card stops instead of returning.
  That narrows the PRD's Outcome and Success; you accepted the trade on 2026-10-16, and
  §The cap states what it costs.
- **Automatic** un-parking. A parked card waits for a human; nothing here ages it out,
  retries it, or escalates it beyond the digest. PR 5 touches the unblock path only to
  clear the counter.
- Undoing or compensating any side effect of the dead run. A branch it pushed, a PR it
  opened, a message it sent all survive the reclaim, and the next run may repeat them.
- Cleaning, resetting, or reusing the dead run's workspace. Reclaim returns the card, not
  the world — so "runnable again" means the card is dispatchable, not that the next run
  will succeed.
- Reaching the dead worker's process or host in any way — no kill, no signal, no remote
  login. The sweep acts only on the store, because by hypothesis the worker is gone.
- Detecting a *hung* worker that is alive and marking but making no progress. Liveness means
  the process exists, not that it is working.
- A supervisor-reported fast path (parent observes child exit, reclaims immediately). The
  supervisor is deliberately given no role; see §Where the mark comes from.
- Changing the database's deployment or topology — no move off SQLite, no new server
  process, no clock synchronisation infrastructure. Decision 2a asks what the deployment
  already is; this design does not propose to change it.
- Changing blocked-card digest behaviour, digest scheduling, or the per-gate alert.
- Surfacing reclaims or parks anywhere other than the morning digest — no immediate alert.
  PRD Success names the digest and grants no other surface.

## Design

### Mechanism: a second signal, not a shorter lease

The claim TTL may not change, so reclaim cannot come from expiring the lease sooner. It
comes from a second signal: the worker periodically marks that it is alive, and a sweep
reclaims claims whose marks have stopped. A claim carries two clocks —

- the **TTL**, untouched, which answers "may this claim still be held?";
- **`T_stale`**, new, which answers "is anyone still holding it?".

They are independent in *mechanism* — neither reads the other, and nothing here shortens
the lease — but coupled in *correctness* twice: configuration must keep `T_stale` below the
TTL, and the TTL is the backstop when the sweep is not running.

Two fields carry the signal:

- **`claim_epoch`** — minted when the claim is taken, unique to that claim, held by the
  worker for the life of its run, and **cleared whenever the claim is released**, by
  completion, block, TTL expiry or reclaim. A card with no claim has no current epoch.
- **`liveness_at`** — **written by the statement that takes or marks the claim, never sent
  as a value by the worker.** It is stamped **at claim time** and again on every mark.
  Seeding at claim time is not bookkeeping: without it, a worker that dies before its first
  mark leaves an empty field, and a rule that skips such claims skips it permanently —
  precisely the PRD's "crash at the start of the day".

The worker therefore cannot *lie* about liveness by sending a value. It can still be wrong
by its host's clock, because SQLite has no server to stamp anything: `now` is evaluated in
the process running the statement. On a single-host deployment that is free; on a
multi-host one it is not true by default, and `T_stale` becomes the skew budget. Decision
2a, and the one open question that can invalidate this mechanism rather than resize it.

Parameters: mark interval `M`, proposed 60s; `T_stale` = 3M = 3 minutes, so three marks must
be missed before a worker is declared dead. Three is a tolerance judgment about host stalls
and stop-the-world pauses whose cost is one duplicated run; it is my inference, not a PRD
figure. Sweep batch bound: 50 candidates per tick.

Configuration must reject `T_stale + tick_period >= TTL`, `tick_period > M`, a batch bound
below 1, and a breaker threshold outside (0, 1]. In the first two arrangements the feature
is inert or arbitrarily slow while appearing installed — the failure mode that looks like
success.

### Where the mark comes from

The mark must be emitted from a context whose lifetime is **identical to the run's** — a
timer thread inside the worker process, or equivalent. The requirement is that the mark
stops when the run stops.

A supervising wrapper is explicitly **disqualified**, even though it is the easier place for
a timer. A wrapper survives its child's crash, so it would mark a dead run forever and the
card would never be reclaimed — the feature would silently do nothing for the only case it
exists for. This is also the sharper reason the supervisor fast path is a non-goal: the
supervisor has no role here, so no part of this design can be defeated by a supervisor that
outlives its child.

Equally, the mark must not come from the work loop. A mark that only fires between units of
work makes any long synchronous step look like death.

### Fencing

Prompt reclaim buys the day back by acting on evidence that is sometimes wrong. A worker
that was merely silent is reclaimed as if dead, and its card handed to someone else while it
is still running. The slow TTL never had that hazard, so this design carries the control
that contains it.

A fenced write carries the writer's `claim_epoch`, and the store accepts it only if the
card's current claim epoch **is present and equal**. Absent counts as mismatch — that is the
state a card is in between reclaim and re-dispatch, exactly the window in which a zombie is
most likely to write.

The worker **aborts its run on the first rejection**, including on the mark path. Without
it, a zombie that treats rejection as transient retries forever and the "bounded to one
duplicated run" claim is false. Because that reaction is terminal, **rejection has to be
narrow**:

- A rejection is *the card row exists and its current epoch is absent or different*. The
  conditional write reports zero rows changed, and the writer distinguishes the two causes —
  `RETURNING`, or a read in the same transaction — because a zero-row result also means the
  card id was wrong or the row is gone, and killing a healthy worker over a bad id is the
  same class of mistake in different clothing. An unknown card is an internal error: logged,
  counted, surfaced, not a zombie verdict.
- It is **not** `SQLITE_BUSY`, not a lock timeout, not an IO error. Those are retried.
  Conflating them means a write-contended store aborts every live worker at once, so a
  mechanism built to save one card's day would cost everyone's.
- Retries must **roll back and restart**, not spin on the busy handler. A transaction that
  reads then writes can be refused promotion when another connection has written in the
  interim (`SQLITE_BUSY_SNAPSHOT`), which the busy handler does not resolve. The worker's
  mark and fenced writes are therefore single autocommit statements or `BEGIN IMMEDIATE`
  transactions — never a deferred read-then-write.

Which writes are fenced is decision 1.

Rollout: a worker that claimed its card before PR 2 deployed holds no epoch. Its writes are
**accepted and counted** during a bypass window that retires one TTL after rollout, since no
pre-rollout claim can outlive its own TTL. The counter is the evidence the window emptied. A
permanent bypass is a permanent hole, and fencing with one gap is not fencing.

### The sweep

A stage on the dispatcher's existing tick, before candidate selection, so a card reclaimed
on a tick is dispatchable on that tick. It is **time-bounded, and any failure is logged,
counted and swallowed** so selection still runs — including a busy failure at commit, where
the rollback is what keeps the record and the requeue from disagreeing.

A claim is a candidate when it has an epoch, its card is in the claimed/running state, and
`now - liveness_at > T_stale`. A claim with **no epoch** is pre-rollout, not stale: the
sweep skips it, its TTL governs it unchanged, and that population drains within one TTL of
deployment.

Claims on cards in **other** states — a card blocked by its worker before the worker died —
*are* swept, but only the claim is cleared: the run is terminated, the record is written,
the status is left alone, and the card is not queued. A card that was blocked before it died
is not silently forced back into the queue. That path **does not move the reclaim counter**,
because nothing was requeued and so no loop can form (§The cap).

Candidate selection is a **single scan outside the reclaim transactions**, which in WAL mode
sees one consistent snapshot. The scan returns the full candidate count and the live-claim
count — the breaker's two inputs, taken from the same snapshot — and at most `batch` rows to
act on. Each candidate is then reclaimed in **its own short `BEGIN IMMEDIATE` transaction**,
not one transaction across the batch. SQLite serialises writers, so a long sweep transaction
holds the write lock against every worker's mark; an outage-sized batch under one lock would
manufacture the staleness it is reclaiming.

Inside each transaction, conditioned on `claim_epoch` **and** `liveness_at` both unchanged
since the scan, with staleness re-evaluated inside the transaction:

1. terminate the run with outcome `reclaimed`;
2. clear the claim and its epoch;
3. return the card to the queue as ready — **or park it** (§The cap);
4. increment the card's reclaim counter and append a reclaim record.

The conditional `UPDATE` carries both fields in its `WHERE` clause, and zero rows changed
means **the precondition did not hold**: another writer moved first, the transaction is
abandoned, and no record is written. Abandoned transactions are counted and reported in the
digest footer, because a sweep whose predicate is wrong is also a sweep that changes zero
rows forever, and that is the one break the footer would otherwise not see.

Both halves of the condition do work. The epoch closes the completion race: a worker that
finishes microseconds before the sweep commits has already cleared the epoch, so a finished
card is not resurrected with its outcome overwritten. `liveness_at` closes the liveness
race: a mark landing between scan and commit moves the field, so a worker that just proved
it was alive keeps its card.

**Circuit breaker.** If the scan's **full** candidate count is at least 5 and exceeds 25% of
live claims, the sweep reclaims nothing, records the trip, and alarms. Full, not batched:
measuring the truncated batch would make a storm invisible precisely when it is largest. The
motivating case is the clock, not volume — a forward jump makes every live claim look stale
simultaneously. The PRD's unit of harm is one lost day for one card; a mechanism able to
lose everyone's day at once needs its own stop. A trip reclaims nothing, so it moves no
counter and parks no card.

The sweep never kills anything. It has no authority over the worker's host and, by
hypothesis, no process to kill.

### The cap: three reclaims, then the card parks

Your decision of 2026-10-16: three consecutive reclaims, then stop requeueing and park. A
stuck card that stops beats one burning workers all night. The clauses below are mine, not
yours — "consecutive" needs a definition, and your answer did not give one.

- **The counter is a new column on the card**, `consecutive_reclaims`, not a field of the
  reclaim record. The record is append-only, so it can only ever *snapshot* the count, and
  the resets fire on paths that write no record at all. PR 5 owns the column, plus the
  record's `consecutive_reclaims` and `parked` snapshot columns; PR 4's record ships without
  them rather than shipping them meaningless.
- **Increment** happens in the same transaction that clears the claim and requeues the card,
  so the count and the requeue cannot disagree. Only the requeueing path increments: a sweep
  that merely clears the claim of a non-queued card (§The sweep) leaves the count alone.
- **Park.** On the increment that reaches 3, step 3 of the sweep transaction sets the card
  **blocked** instead of ready, with a machine-written reason naming the count and the last
  dead run id, and the record's `parked` column is set so the digest need not re-derive it.
  Encoding `blocked` versus a distinct `parked` status is decision 4.
- **Reset, two paths, each with a named writer.** (a) The run-completion path, in the same
  transaction that writes a terminal run outcome, clears the card's counter — **except when
  that outcome is `reclaimed`**, which is the sweep's own write and would otherwise cancel
  the increment it just made in that same transaction. Any other terminal outcome resets:
  done, failed, blocked by the worker. A run that ended under its own power proves the card
  does not kill workers, which is the only thing the cap measures. (b) The unblock path
  clears the counter on **any** unblock, not only of a parked card. Conditioning on "was it
  parked" would mean reading a machine-written reason string to decide, and it would strand
  a card that was human-blocked while claimed, then reclaimed, then unblocked. Unconditional
  is both simpler and correct, and it means an unblock restores the full budget, which is
  what an unblock means everywhere else.
- **What the cap does not reach.** A claim released by **TTL expiry** writes no run outcome
  through the sweep and so touches no counter; a card can carry a count across such releases.
  And "consecutive" has no time window, so three reclaims three months apart park a card
  exactly as three in one night. I did not add a decay window, because that would edit your
  decision rather than implement it — but it is one parameter if you want one.
- **Cost, accepted.** Three reclaims from three *unrelated* infrastructure outages park a
  healthy card, and a false reclaim counts exactly like a true one. On the third reclaim the
  card is not runnable again without intervention, which narrows the PRD's Success criterion
  to at most three consecutive reclaims per card. PR 4's acceptance test passes because it
  is a single kill.

### Reclaim record

One row per reclaimed run, appended inside the sweep transaction. It is the digest's only
input, and because the claim row is gone afterwards, the only place this information
survives.

`(record_id, card_id, run_id, claim_epoch, worker_id, host, claimed_at, liveness_at,
reclaimed_at)` from PR 4, plus `(consecutive_reclaims, parked)` from PR 5.

- `record_id` is monotonic and commit-ordered, which the cursor's exactly-once rendering
  depends on in two ways: the ids must not be reused, so the column is `AUTOINCREMENT` and
  records are never pruned; and ids must not commit out of order, which holds because
  SQLite serialises writers and each reclaim is its own transaction.
- `worker_id` and `host` are captured from the claim being cleared. The outage morning's
  first question is one dead host or forty independently dead cards, and with decision 2a
  open, `host` is also the only way to see a single skewed clock reclaiming its own workers.
  The footer renders the host distribution so that question is answerable from the digest
  rather than by hand.

The run's own terminal outcome is `reclaimed`, distinct from `failed`. A reclaimed run made
no claim about the work; folding it into `failed` corrupts every count of how often a card
fails on its merits. It also matters to the cap, whose reset turns on whether a run ended
under its own power. PR 3 owns the audit of every reader of that field.

### Digest surfacing

Two surfaces, because reclaims and parks are different kinds of thing.

**Reclaims — an event list, read by cursor.** Records with `record_id` greater than the last
rendered cursor; cursor advanced after a successful send. Not a timestamp window: a record
committing after a window closes is dropped by it forever, and the clock can move. One line
**per card**, not per record: the card, its state at digest time, the dead run's id, when it
was reclaimed, and the reclaim count if greater than one. The state separates "this healed
itself overnight" from "this is still broken"; the run id lets the operator pull the dead
run's logs. How long the claim had been silent is deliberately not rendered — it is
`T_stale` by construction, carrying no information.

**Parked cards — a standing block, read by state.** Every currently parked card, every
morning, until a human acts. A park is a state, not an event, and under cursor rendering it
would appear once and then never again: miss that morning and the card is stopped
indefinitely with nothing to mention it — the PRD's own failure, rebuilt by the mechanism
added to stop a loop. The residual is latency, not silence: a card parked at 09:05 is first
seen the next morning.

**Footer — sweep health.** When the sweep last ran, fenced-write rejections, claims skipped
for having no epoch, breaker trips, abandoned reclaim transactions, and the host
distribution of this digest's reclaims. Without it, an absence of reclaim lines is
indistinguishable between "no crashes", "the sweep died a week ago" and "the flag is off" —
and the PRD's Outcome is that the operator *learns*.

**Open coordination, unresolved and now worse.** `projects/digest-dedupe/prd.md` states its
Outcome as "A card appears in the digest at most once per day" — phrased digest-wide, though
its Problem is about blocked cards. A card reclaimed repeatedly in one day already collided
with that rule; parking makes the card *blocked*, so one card can now qualify for the
blocked entry class and the parked block on the same morning, and the dedupe rule would have
to choose. This belongs to whichever project lands second, with the other's author in the
room. Flagged in PR 6 rather than decided.

## Failure modes

- **A live worker quiet for longer than `T_stale`.** Reclaimed and re-dispatched, so the
  work runs twice. The zombie stops at its next fenced write and loses what it did since.
  Bounded to one duplicated run *inside the store*. Mitigations sit upstream of fencing: the
  mark's independence from the work loop, and `T_stale` being several marks wide.
- **A skewed host.** With decision 2a open, a worker host running more than `T_stale` slow
  has every claim reclaimed immediately and repeatedly, and its cards park after three. A
  host running fast is never reclaimed at all. The footer's host distribution makes the
  first diagnosable in one digest; the second is silent.
- **A card that kills its worker on contact.** Three reclaims, then parked, then waits for a
  human who sees it in the next morning's standing block and every morning after.
- **The alternating loop the cap does not catch.** A card that kills two workers, then
  fails honestly once, then repeats — R, R, F, R, R, F — resets at every F and never reaches
  3, burning two workers per cycle indefinitely. This is the direct consequence of resetting
  on any self-terminating run (Council dissent). The cap stops the simple loop, not this
  one; a reclaim-rate alarm would, and this PRD does not grant one.
- **A stale count.** No decay window, and TTL-expiry releases touch no counter, so a card
  can park on three reclaims spread over months.
- **A contended store.** Marks and fenced writes hit `SQLITE_BUSY`, roll back and retry. If
  contention outlasts `T_stale`, marks stop landing and healthy workers are reclaimed; the
  breaker is the backstop, since contention of that size hits many claims at once.
- **The sweep not running, or failing.** Nothing is reclaimed, the untouched TTL remains the
  backstop, and the system degrades to today's behaviour — true only because the stage is
  isolated from selection. The footer makes this visible rather than silent.
- **Reclaim storm, and the clock.** An outage kills many workers; the next tick reclaims up
  to the batch bound and the dispatcher re-dispatches within its existing concurrency limit.
  A forward clock jump is the dangerous version, because the claims are *not* dead. The
  breaker bounds both, on the full count rather than the batch.
- **The epoch-less bypass window.** Between PR 2's deploy and one TTL later, pre-rollout
  workers write unfenced. Bounded in time, counted, retired on a schedule.
- **A hung worker that keeps marking.** Never reclaimed, by design, and invisible: no record,
  no digest line. The operator's experience of this failure is unchanged by this project,
  worth saying plainly because the feature's name suggests otherwise.
- **A reclaimed card that waits in the queue.** Reclaimed in three minutes and dispatched
  hours later if the queue orders by enqueue time; decision 3.

## Verification

PR 4 acceptance is the PRD's Success criterion: `SIGKILL` a worker mid-run, assert the card
is ready within `T_stale + tick_period` and is dispatched again with no human action. The
bound includes the tick period because detection happens on the first tick after the claim
goes stale; `T_stale` alone is not achievable by this mechanism.

Two companion cases, because the single mid-run kill is not the PRD's actual scenario:

- **Kill before the first mark.** Kill within one mark interval of the claim; the card is
  still reclaimed within the same bound. Regression guard for v1's headline defect.
- **Kill the work process, leave any parent alive.** Still reclaimed. Pins the requirement
  that the mark dies with the run.

PR 6 acceptance is Success's second half: the same kill produces exactly one line in the
next digest and no line in the digest after that — including when the record commits after
the digest run begins, which a timestamp window would drop.

PR 1:

- The epoch is minted at claim and stable for the life of the run; `liveness_at` is non-null
  from claim time onward.
- A worker that sends a liveness value cannot influence the stored one.

PR 2 (fencing):

- A write carrying a stale or forged epoch is rejected on every enumerated fenced path.
- A write to a card with no current claim is rejected — the reclaimed-but-not-yet-
  redispatched window.
- A write carrying no epoch is accepted and counted while the bypass window is open, and
  rejected after it retires.
- In **observe** mode a mismatching write is counted and still applied; in enforce it is
  rejected. Both sub-steps, not only enforce.
- The worker exits on first rejection, including on the mark path.
- **A busy or locked store does not abort the worker**: under contention the mark rolls back,
  retries and the run survives.
- **A write naming an unknown card does not abort the worker**: it raises an internal error.
- Mixed state: some claims with epochs and some without, sweep on, neither mishandled.

PR 3:

- A reclaimed run is distinguishable from a failed run at every reader the audit enumerated —
  run counts, failure rates, retry surfaces, dashboards. The audit's acceptance is a list of
  call sites with a test at each, not a grep.

PR 4 (the sweep):

- A mark landing between the scan and the commit prevents the reclaim.
- A worker completing immediately before the sweep wins: the card stays done, its outcome is
  not overwritten, no record is written.
- Two sweepers on one stale claim produce one record, one requeue, one digest line.
- A blocked-but-claimed card whose worker dies has its claim cleared, its status left
  blocked, and its counter untouched.
- The breaker trips on a candidate set that exceeds the threshold **and is larger than the
  batch bound** — the guard that the breaker measures the scan, not the batch. A forward
  clock jump trips it rather than reclaiming the world.
- A sweep stage that throws, exceeds its time bound, or fails at commit does not prevent
  candidate selection on that tick, and a commit failure leaves no record.
- **The sweep does not hold the write lock across a batch**: with a large candidate set,
  concurrent marks from healthy workers still land within the busy timeout.
- `worker_id` and `host` on the record match the claim that was cleared.
- **Runnability, not just dispatch:** a killed run whose side effects are idempotent by
  construction is reclaimed and the re-dispatched run completes. Scoped to such a fixture
  deliberately — the general claim is disclaimed in Non-goals.

PR 5 (the cap):

- Three consecutive reclaims park the card; the third does not requeue it.
- A run that ended `done`, `failed`, or blocked-by-worker between two reclaims resets the
  counter — all three outcomes, since the reviewers split on `failed`.
- A `reclaimed` outcome does **not** reset the counter: the guard that the sweep's own
  outcome write does not cancel its increment in the same transaction.
- Unblocking any card clears its counter, parked or not.
- A parked card is not dispatched while parked. *(Standing guard on the pre-existing blocked
  state, not on code PR 5 writes.)*
- A breaker trip moves no counter and parks nothing.

PR 6 (the digest):

- A parked card appears in the standing block on every digest until it is unblocked, not
  once. The regression guard for cursor-rendered parks.
- Several reclaims of one card in one window collapse to one line carrying the count.
- The footer reports a sweep that has not run, and a host distribution concentrated on one
  host.

Standing guards:

- A healthy worker that marks for 10x `T_stale` is never reclaimed — which also pins the
  hung-worker non-goal from the other side.
- A claim with no epoch is not swept and still expires on its original TTL schedule: the
  guard that this design did not change the TTL by accident.
- `T_stale + tick_period >= TTL`, `tick_period > M`, a batch bound below 1, and a breaker
  threshold outside (0, 1] are all rejected at configuration load rather than silently
  disabling or slowing the feature.
- Flag-off regression: with fencing and sweep off, claim and dispatch behaviour match a
  pre-change fixture exactly, while epoch and liveness populate.

## Assumptions

Every line is an inference of mine; the PRD and the card state none of them. The ones whose
falsity changes the design's *shape* rather than its size are escalated as decisions; the
rest are listed because an unflagged inference arrives downstream looking finished.

- Claims live in the SQLite database the dispatcher and workers share, and claimed cards can
  be enumerated from it. (Your answer established SQLite; that it is one database holding
  both claims and cards is still my inference.)
- The database runs in `journal_mode=WAL` with a busy timeout set on every connection.
  Without WAL, readers block the writer and the sweep's scan contends with the marks it is
  measuring. WAL also requires every process to be on one host, which is why decision 2a is
  about more than clocks.
- Claim-mutating statements run in processes that agree on the time to well inside `T_stale`.
  On a single-host deployment this is free; on a multi-host one it is not true by default.
  Decision 2a, and the one assumption that can invalidate the mechanism rather than resize it.
- A card has at most one claim at a time, and claims are one-to-one with runs.
- The claim transition itself is atomic and conditional on the card being unclaimed. This
  design fences writes to an *already claimed* card and inherits the safety of the
  unclaimed-to-claimed transition without examining it.
- Claims do not already carry a liveness signal; if they do, PR 1 mostly disappears
  (decision 2b).
- The card row can gain a column, and the reclaim record is a new table that is never pruned.
  The cursor's exactly-once guarantee rests on the second.
- The dispatcher has a periodic tick a sweep stage can attach to, so no new scheduler is
  introduced; the tick period is short relative to `T_stale`; and a stage can read its own
  writes later in the same tick, which is what makes same-tick dispatch true.
- Workers are a single codebase the mark can be added to, with a context whose lifetime
  equals the run's.
- `M` = 60s, a three-mark tolerance, a 50-candidate batch and a 25% breaker threshold are my
  figures, not the PRD's.
- Cards have a queued/ready state, and a reclaimed card in it is treated like any other ready
  card. Its *position* is decision 3.
- Re-dispatch may select the same host the dead worker died on; nothing here avoids that.
- Every write path that can mutate a claimed card is enumerable and can be made to carry the
  epoch. If that enumeration is not tractable, decision 1's narrow option is not a preference
  but the only available answer, and PR 2's estimate is fiction either way.
- Runs have an identity and a terminal outcome field that can take a new value; PR 3's audit
  is the check on this rather than the hope.
- The blocked state is reachable from a machine-written transition, is not dispatched while
  set, and has a human unblock path that PR 5 can hook. The park depends on all three.
- The dispatcher has an existing concurrency limit that applies to reclaimed cards
  identically. This is the entire blast bound on a reclaim storm.
- A single claim TTL exists and is visible at configuration load. If the TTL is per-queue or
  dynamic, the `T_stale` guard cannot be validated at load and needs another home.
- The morning digest assembles from durable records, can gain an entry class, a standing
  block and a footer, and can persist a cursor across runs.
- Nothing downstream treats a claim disappearing before its TTL as impossible.
- The claim TTL is hours, not minutes (decision 2c).
- I did not inspect the implementation. This repo holds documents only and the card scopes me
  to it, so the component names above describe roles, not verified modules.

## Revision history

- **v1** — first draft from `prd.md` at `d76e794`. Scope M, 3 PRs, 3 decisions.
- **v2** — review rework after two independent critiques, both of which returned "not safe
  as-is". Fixed the defect that made the design miss the PRD's primary scenario (a worker
  dying before its first mark was skipped by the sweep forever, because `liveness_at` was
  populated only by marks); strengthened the sweep's condition from epoch-only, which closed
  neither the liveness race nor the completion race; specified fencing in the reclaim window
  and at rollout; gave owners to the zombie-abort behaviour and the outcome-reader audit;
  replaced timestamp-keyed digest rendering with a cursor; isolated the sweep from the
  dispatch hot path. Scope re-rated L, 5 PRs; decisions 3 to 4.
- **v3** — 2026-10-16, applying the owner's two answers, then reworked after two further
  critiques that again returned "not safe as-is". Designed the cap and the park — including
  the card-level counter the first draft of this revision referenced but never created —
  wrote the sweep against SQLite semantics, and raised the consequences of the SQLite answer
  the question did not cover: WAL's single-host constraint and the absent server clock (now
  decision 2a), writer serialisation forcing per-candidate transactions, and the narrowing of
  "rejection" so that a busy store or an unknown card id cannot kill a healthy worker. Also
  fixed in rework: the breaker measuring the truncated batch instead of the full candidate
  set, parked cards rendering once and then disappearing forever, and a flag-flip gate that
  had lost its enforcing-fence clause. Scope 6 PRs; 4 decisions open.
