# DD: Reclaim stale card claims

Covers: `projects/stale-claims/prd.md` (commit `d76e794`). Revision v2.

## Review header

### Decisions needed from you

1. **A card that keeps killing its worker.** Reclaim returns the card to the queue and
   the dispatcher picks it up again. If the card is *what kills the worker*, that loop
   has no exit: it burns a worker every few minutes until a human notices. The brake is
   a cap — after N consecutive reclaims with no completed run in between, stop
   requeueing and park the card. I am asking rather than deciding because the cap sits
   next to your "retrying the work automatically" non-goal, and I want to state the
   boundary honestly rather than argue myself across it: the *automatic requeue is*
   already the automatic re-execution, and the cap only takes that automaticity away.
   A stop is a circuit breaker, not a retry policy. I propose a cap of 3. The cost is
   real — a card reclaimed three times for three *unrelated* infrastructure outages
   stops dead instead of recovering, which is the outcome the PRD exists to prevent.
   Independent of your answer, the sweep records a consecutive-reclaim count and the
   digest shows it (§Reclaim record). Nothing in the dispatch path reads that count, so
   it is observability, not accounting, and it ships either way — otherwise the default
   behaviour while this decision is open is the unbounded loop.
2. **Three facts about the system that only you can supply.** I could not see the
   implementation (see Assumptions). (a) Can the store condition a write on the claim
   epoch being unchanged, and carry the sweep's four writes in one transaction? This is
   the load-bearing one: without a compare-and-set the sweep cannot be made safe by this
   design, and the fallback — an external lock around claim mutation, serialising a hot
   path to protect against a rare event — is worse than the problem and I would want to
   re-open the approach rather than build it. (b) Do claims already carry a liveness
   signal? If so, PR 1 collapses to seeding and the epoch. (c) Is the claim TTL really
   hours? The PRD's "most of a day" implies it, and the whole premise that a three-minute
   `T_stale` is meaningfully prompter rests on it. If the TTL is already minutes, the
   Problem section means something other than what I read.
3. **How wide the fence goes.** Fencing every write that can mutate a claimed card is a
   system-wide concurrency retrofit, and it is the single reason this is scope L rather
   than M. The narrow alternative is to fence only the transitions that can corrupt a
   card's terminal state — completion, block, status change — and leave incidental
   worker writes (comments, progress, logs) unfenced. Narrow is most of the safety for a
   fraction of the audit; what it gives up is the guarantee, because a zombie that can
   still write *something* can still confuse an operator reading the card. My passes
   split on this (see Council dissent) and I did not want to settle a scope question of
   that size on my own. I propose narrow for PR 2 with the wide audit as follow-up work
   this PRD does not own.
4. **Where a reclaimed card lands in the queue.** The design returns it as ready and
   says nothing about position, which means it inherits whatever ordering the queue
   already has. If that is enqueue time, a card claimed at 09:00 and reclaimed at 09:03
   now sorts behind everything queued in the interval — reclaimed in three minutes and
   dispatched hours later, with the PRD's day lost anyway. Preserving the card's
   original enqueue position fixes that, and it does so by pushing other people's cards
   back. That is a prioritisation call about whose work waits, which is yours, not mine.

### Council dissent

Two independent reviewers critiqued v1 — one on correctness and internal consistency,
one on PRD fidelity and operational realism. Both returned "not safe to hand to a human
as-is". Most of what they found I accepted outright and it is simply fixed in v2 (see
What changed). Recorded here is only where there was a real disagreement, or where I
declined.

- **Whether fencing belongs in this PRD.** My own fidelity pass at v1 called it scope
  the PRD does not grant; I overruled it and shipped it. The fidelity reviewer agreed
  fencing is *forced in substance* — prompt reclaim necessarily acts on inference, so
  something must stop the mistakenly-reclaimed worker — but held that its *extent* was
  an unresolved internal scope dispute of exactly the size that should be escalated
  rather than settled by the author. That is right, and it is now decision 3. The
  v1 behaviour, where the document recorded the dispute in dissent and then decided it
  anyway, was the wrong disposal.
- **The `reclaimed` run outcome.** v1 settled this without escalating: a reclaimed run
  made no claim about the work, so folding it into `failed` corrupts every count of how
  often a card fails on its merits. The correctness reviewer accepted the reasoning but
  noted a new enum value in a system I cannot inspect touches every reader of that
  field, and that no PR owned the audit. I am holding the decision and fixing the
  defect: the outcome value and its reader audit are now PR 3, on their own, ahead of
  anything that writes the value. Escalating this would have handed you a question whose
  answer I already know; giving the audit an owner was the actual gap.
- **An immediate alert.** v1 escalated digest-vs-alert as a decision. The fidelity
  reviewer pointed out that the same document listed "surfacing reclaims anywhere other
  than the morning digest" as a non-goal, so v1 declared something out of scope and
  asked about it in the same breath. Withdrawn: PRD Success names the digest, the PRD
  grants no alert surface, and once reclaim works the notification is a record rather
  than a rescue. It stays a non-goal. If you want an alert, say so and it is a small
  addition to PR 5.
- **Reclaim storm and the circuit breaker.** The operations reviewer wanted a breaker;
  the fidelity objection is that the PRD never asked for one. I took the breaker, and
  the argument that moved me is not volume but the clock: a forward jump in the store
  clock makes every live claim look stale at once, and without a breaker this feature
  converts one bad timestamp into a system-wide outage. The PRD's unit of harm is one
  lost day for one card; a mechanism that can lose everyone's day at once needs its own
  stop. I am flagging it as the thing in this document most open to a charge of creep.
- **Where the liveness mark is emitted from.** v1 offered "a timer thread, or the
  wrapper supervising it" as interchangeable. The fidelity reviewer caught that they are
  opposites: a supervising wrapper *survives* its child's crash and would keep marking a
  dead run forever, so the feature would silently never fire for the only case it
  exists for. Not a disagreement — a straight error, now fixed and inverted into a
  stated requirement (§Where the mark comes from).

### Scope and PR breakdown

**Scope: L.** Five PRs — raised from v1's "M, 3 PRs", which was wrong in both numbers.
Two separate call-site-wide audits sit in this work (every write path that can mutate a
claimed card; every reader of the run outcome field), and v1 had assigned one of them to
an assumption and the other to nobody. Nothing here changes the claim TTL, the
dispatcher's selection logic, or any existing digest entry.

- **PR 1 — liveness data.** Epoch minted at claim time, `liveness_at` stamped by the
  store at claim time and on every mark, the mark emitted from a run-lifetime context.
  Nothing reads either field. Ships on; behaviour is genuinely unchanged, because
  nothing is enforced and nothing sweeps.
- **PR 2 — fencing.** The epoch carried on fenced writes and checked by the store, in
  two steps behind one flag: **observe** (mismatches logged and counted, writes allowed)
  until the counter is quiet, then **enforce** (mismatches rejected). Worker aborts its
  run on the first rejection, including a rejection on the mark path. Scope of "fenced
  writes" is decision 3. v1 called this "ships on, behaviour unchanged", which was
  false: its only possible pre-sweep effect was rejecting writes from a bug, silently,
  with no flag to turn it off.
- **PR 3 — the `reclaimed` run outcome.** The value introduced, plus the audit of every
  reader of that field. Nothing writes it yet. Small, and deliberately separate, so that
  the audit has an owner and a landing of its own rather than riding in with the sweep.
- **PR 4 — the sweep.** Stale detection, the reclaim transaction, the reclaim record,
  the circuit breaker, failure isolation, config validation. Behind a flag, flipped on
  against warm liveness data and an enforcing fence. Delivers the Success criterion's
  first half.
- **PR 5 — digest entry.** Reclaim records rendered as a new entry class, read by
  cursor, plus the sweep health footer. Delivers the second half.

The order is forced. A sweep without fencing converts a false reclaim into two live
writers; a sweep that writes an outcome value nothing has audited breaks whatever counts
failures; a digest has nothing to read until records exist.

### What changed in v2

Review rework. No decision was closed, one was withdrawn and two were added, and the
scope rating moved on evidence rather than on reflection.

- **The headline defect: a worker that died before its first mark was never reclaimed.**
  v1 populated `liveness_at` only from marks and skipped claims that had none, so for
  the first mark interval of every run the field was empty, and a worker that died in
  that window left a claim the sweep skipped *forever*. The PRD's motivating sentence is
  "a crash at the start of the day", so v1 missed its primary case. `liveness_at` is now
  stamped at claim time, and pre-rollout claims are identified by having no epoch.
- **The sweep's condition was too weak.** Conditioning only on the epoch let a mark
  landing between read and commit pass, reclaiming a worker that had just proved it was
  alive; and it only closed the race with a completing worker if claim release clears
  the epoch, which v1 never said. The condition now covers epoch and `liveness_at`,
  staleness is re-evaluated inside the transaction, and claim release explicitly clears
  the epoch.
- **Fencing was underspecified in the one window it exists for.** v1 never said what
  happens to a write whose card has no current claim — the state a card is in between
  reclaim and re-dispatch. Absent now counts as mismatch. The epoch-less writes of
  pre-rollout workers, which v1 did not mention at all, get an explicit bypass with a
  retirement.
- **Unowned work given owners.** The zombie's abort-on-rejection was asserted as a
  property and built by no PR; the outcome-value reader audit was filed as an assumption.
  Both are now in a PR's scope.
- **Exactly-once digest rendering.** v1 keyed rendering on `reclaimed_at` within a
  "since the last digest" window, which drops a record that commits after the window
  closes and misbehaves under the clock shift v1's own failure modes conceded. Now a
  monotonic cursor.
- **Operability.** The reclaim record gains worker and host identity (without which an
  outage morning cannot distinguish one dead host from forty dead cards), the digest line
  gains the dead run id and the card's current state, repeats collapse into one counted
  line, and a footer reports sweep health — because with v1's design, no reclaim lines
  was indistinguishable from a sweep that died a week ago.
- **The sweep can no longer stall dispatch.** v1 put it upstream of candidate selection
  and then claimed a broken sweep degrades to today's behaviour. It degraded to worse
  than today: a throwing or slow sweep stage blocked dispatch entirely. Now time-bounded
  and swallowed.
- **Three claims corrected.** "Two clocks that never interact" (they are coupled in
  correctness, twice), "ready within `T_stale`" (the bound is `T_stale + tick_period`),
  and "the only coupling with digest-dedupe is layout" (that project's PRD states a
  digest-wide once-per-card-per-day outcome; the collision is semantic and I have not
  resolved it — see §Digest surfacing).
- **Decisions: 3 → 4.** The alert question withdrawn as self-contradictory; fence extent
  and queue position added; decision 1 reframed and given an interim mitigation that
  ships regardless of the answer.

## Non-goals

- Preventing crashes (PRD).
- Changing the claim TTL — its value, its semantics, or its effective length by any
  indirect route (PRD). `T_stale` is a separate parameter on a separate signal, and the
  TTL remains the sole governor of claims the sweep skips.
- Retrying the work automatically (PRD): no attempt counter in the dispatch path, no
  backoff, no re-execution policy. A reclaimed card re-enters the queue on exactly the
  terms a never-claimed card does. Decision 1 is the one place this boundary is under
  pressure, and it is escalated rather than quietly crossed.
- Undoing or compensating any side effect of the dead run. A branch it pushed, a PR it
  opened, a message it sent all survive the reclaim, and the next run may repeat them.
- Cleaning, resetting, or reusing the dead run's workspace. Reclaim returns the card,
  not the world — and so "runnable again" means the card is dispatchable, not that the
  next run will succeed.
- Reaching the dead worker's process or host in any way — no kill, no signal, no remote
  login. The sweep acts only on the store, because by hypothesis the worker is gone.
- Detecting a *hung* worker that is alive and marking but making no progress. Liveness
  means the process exists, not that it is working.
- A supervisor-reported fast path (parent observes child exit, reclaims immediately).
  The supervisor is deliberately given no role in this design; see §Where the mark comes
  from for why that is a correctness position and not only a budget one.
- Changing blocked-card digest behaviour, digest scheduling, or the per-gate alert.
- Surfacing reclaims anywhere other than the morning digest — no immediate alert. PRD
  Success names the digest and grants no other surface.

## Design

### Mechanism: a second signal, not a shorter lease

The claim TTL may not change, so reclaim cannot come from expiring the lease sooner. It
comes from a second signal: the worker periodically marks that it is alive, and a sweep
reclaims claims whose marks have stopped. A claim then carries two clocks —

- the **TTL**, untouched, which answers "may this claim still be held?";
- **`T_stale`**, new, which answers "is anyone still holding it?".

The two are independent in *mechanism* — neither reads the other, and nothing this
design does shortens the lease — but they are coupled in *correctness*, in two places:
configuration must keep `T_stale` below the TTL, and the TTL is the backstop when the
sweep is not running. v1 claimed they "never interact", which was a false safety claim.

Two fields carry the signal:

- **`claim_epoch`** — minted when the claim is taken, unique to that claim, held by the
  worker for the life of its run, and **cleared whenever the claim is released**, by
  completion, block, TTL expiry or reclaim. A card with no claim has no current epoch.
- **`liveness_at`** — **stamped by the store**, never sent by the worker, so a worker
  with a wrong clock cannot make itself look alive or dead. It is stamped **at claim
  time** and again on every mark. Seeding it at claim time is not bookkeeping: without
  it, a worker that dies before its first mark leaves a claim with an empty field, and
  a rule that skips such claims would skip it permanently — which is precisely the
  PRD's "crash at the start of the day".

Parameters: mark interval `M`, proposed 60s; `T_stale` = 3M = 3 minutes, so three marks
must be missed before a worker is declared dead. Three is a tolerance judgment about
host stalls and stop-the-world pauses whose cost is one duplicated run; it is an
inference of mine, not a PRD figure (see Assumptions).

Configuration must reject `T_stale + tick_period >= TTL`, and `tick_period > M`. In
either arrangement the feature is inert or arbitrarily slow while appearing to be
installed — the failure mode that looks like success.

### Where the mark comes from

The mark must be emitted from a context whose lifetime is **identical to the run's** — a
timer thread inside the worker process, or equivalent. The requirement is that the mark
stops when the run stops.

A supervising wrapper is explicitly **disqualified**, even though it is the easier place
to put a timer. A wrapper survives its child's crash, so it would keep marking a dead
run forever and the card would never be reclaimed — the feature would silently do
nothing for the only case it exists for. This is also the sharper reason the supervisor
fast path is a non-goal: the supervisor is given no role here at all, so no part of this
design can be defeated by a supervisor that outlives its child.

Equally, the mark must not come from the work loop. A mark that only fires between units
of work makes any long synchronous step look like death.

### Fencing

Prompt reclaim buys the day back by acting on evidence that is sometimes wrong. A worker
that was merely silent is reclaimed as if dead, and its card handed to someone else while
it is still running. The slow TTL never had that hazard, so this design has to carry the
control that contains it.

A fenced write carries the writer's `claim_epoch`, and the store accepts it only if the
card's current claim epoch **is present and equal**. Absent counts as mismatch — that is
the state a card is in between reclaim and re-dispatch, which is exactly the window in
which a zombie is most likely to write.

The worker **aborts its run on the first rejection**, including a rejection on the mark
path. This is worker-side behaviour, it is in PR 2's scope, and it is tested: without it,
a zombie that treats rejection as a transient error retries forever and the "bounded to
one duplicated run" claim is false.

Which writes are fenced is decision 3 — all worker-attributed writes, or only the
transitions that can corrupt terminal state.

Rollout: a worker that claimed its card before PR 2 deployed holds no epoch. Its writes
are **accepted and counted** during a bypass window, which retires one TTL after rollout,
since no pre-rollout claim can outlive its own TTL. The counter is the evidence that the
window actually emptied. A permanent bypass would be a permanent hole, and fencing with
one gap in it is not fencing.

### The sweep

A stage on the dispatcher's existing tick, before candidate selection, so a card
reclaimed on a tick is dispatchable on that tick. It is **time-bounded, and any failure
is logged, counted and swallowed** so that selection still runs. A sweep that throws or
runs slow against a degraded store must degrade to today's behaviour, not to no dispatch
at all — v1 placed it in the dispatch hot path without isolation and then claimed the
first property while having built the second.

A claim is a candidate when it has an epoch, its card is in the claimed/running state,
and `now - liveness_at > T_stale`. Cards in any other state are not swept; their claim is
released but their status is left alone, so a card that was blocked before it died is not
silently forced back into the queue. A claim with **no epoch** is pre-rollout, not stale:
the sweep skips it and its TTL governs it unchanged, and that population drains within
one TTL of deployment.

For each candidate, in one transaction conditioned on `claim_epoch` **and** `liveness_at`
both unchanged, with staleness re-evaluated against the store clock inside the
transaction:

1. terminate the run with outcome `reclaimed`;
2. clear the claim and its epoch;
3. return the card to the queue as ready;
4. append a reclaim record.

Both halves of the condition do work. The epoch closes the completion race: a worker that
finishes microseconds before the sweep commits has already cleared the epoch, so the
condition fails and a finished card is not resurrected with its outcome overwritten.
`liveness_at` closes the liveness race: a mark landing between the sweep's read and its
commit moves the field, the condition fails, and a worker that just proved it was alive
keeps its card. Conditioning on the epoch alone — v1's design — closed neither.

**Circuit breaker.** If one tick's candidate set exceeds 25% of live claims (floor: 5
claims), the sweep reclaims nothing, records the trip, and alarms. The motivating case is
not volume but the store clock: a forward jump makes every live claim look stale
simultaneously, and without a breaker this feature turns one bad timestamp into a
system-wide outage. The PRD's unit of harm is one lost day for one card; a mechanism able
to lose everyone's day at once needs its own stop.

The sweep never kills anything. It has no authority over the worker's host and, by
hypothesis, no process to kill.

### Reclaim record

One row per reclaimed run, appended inside the sweep transaction so the record and the
requeue cannot disagree. It is the only durable evidence that a day was saved, the
digest's only input, and — because the claim row is gone afterwards — the only place
this information survives at all.

`(record_id, card_id, run_id, claim_epoch, worker_id, host, claimed_at, liveness_at,
reclaimed_at, consecutive_reclaims)`

- `record_id` is monotonic and commit-ordered. The digest reads by cursor, not by time.
- `worker_id` and `host` are captured from the claim being cleared. Without them, the
  outage morning this design anticipates cannot answer its first question — one dead
  host, or forty independently dead cards?
- `consecutive_reclaims` counts reclaims of this card with no completed run in between.
  Nothing in the dispatch path reads it; it exists so the digest can show a looping card
  (decision 1) whichever way that decision goes.

The run's own terminal outcome is `reclaimed`, distinct from `failed`. A reclaimed run
made no claim about the work; folding it into `failed` would corrupt every count of how
often a card fails on its merits, and "this card keeps failing" is a sentence an operator
needs to stay true. PR 3 owns the audit of every reader of that field.

### Digest surfacing

A new entry class listing reclaims since the last digest, read by **cursor**: records
with `record_id` greater than the last rendered cursor, cursor advanced after a
successful send. Not a timestamp window — a record that commits after a time window
closes is dropped by it forever, and the store clock can move. A reclaim is an event, not
a state, so there is no aging and no suppression logic; the cursor is the whole mechanism.

One line **per card**, not per record: the card, its state at digest time, the dead run's
id, when it was reclaimed, and the reclaim count if greater than one. The state is what
separates "this healed itself overnight" from "this is still broken"; the run id is what
lets the operator pull the dead run's logs. How long the claim had been silent is
deliberately not rendered — it is `T_stale` by construction, so it would be a
near-constant field carrying no information.

A footer line reports sweep health: when the sweep last ran, fenced-write rejections,
claims skipped for having no epoch, and breaker trips. Without it, an absence of reclaim
lines is indistinguishable between "no crashes", "the sweep stage died a week ago" and
"the flag is off" — and the PRD's Outcome is that the operator *learns*, which a silent
degradation defeats.

**Open coordination, unresolved.** `projects/digest-dedupe/prd.md` states its Outcome as
"A card appears in the digest at most once per day" — phrased digest-wide, though its
Problem is about blocked cards. If that rule is meant across entry classes, a card
reclaimed repeatedly in one day collides with it. Per-card collapsing reduces the
collision but does not settle whether the rule applies here. I did not resolve this: it
belongs to whichever of the two projects lands second, with the other's author in the
room. Flagged in PR 5 rather than decided here.

### Failure modes

- **A live worker that went quiet for longer than `T_stale`.** Reclaimed, re-dispatched,
  so the work runs twice. The zombie stops at its next fenced write and loses what it
  had done since. Bounded to one duplicated run *inside the store*; side effects already
  emitted outside the store are neither fenced nor undone. The mitigations sit upstream
  of fencing — the mark's independence from the work loop, and `T_stale` being several
  marks wide. The narrower read/commit race is closed by the transaction condition.
- **A card that kills its worker on contact.** Unbounded reclaim loop; decision 1. The
  count ships regardless, so the loop is at least visible from the first digest.
- **Side effects of the dead run.** Not undone, by design. The next run starts in a
  world the dead run already changed, and may change it again — or may fail immediately
  on the mess, which reclaim does not promise to prevent.
- **Workspace state.** For a shared or directory workspace, the new run starts on
  whatever the dead run left behind. No clean, no revert.
- **The sweep not running, or failing.** Nothing is reclaimed, the untouched TTL remains
  the backstop, and the system degrades to today's behaviour — which is only true
  because the stage is isolated from selection. The digest footer is what makes this
  state visible rather than silent.
- **Reclaim storm, and the clock.** An outage kills many workers; the next tick reclaims
  them and the dispatcher re-dispatches up to its existing concurrency limit. A forward
  clock jump is the dangerous version, because the claims are *not* dead. The breaker
  bounds both. Digest volume is bounded separately by per-card collapsing.
- **The epoch-less bypass window.** Between PR 2's deploy and one TTL later, pre-rollout
  workers can write unfenced. Bounded in time, counted, and retired on a schedule rather
  than on a judgment.
- **A hung worker that keeps marking.** Never reclaimed, by design, and invisible: it
  produces no reclaim record and no digest line. The operator's experience of this
  failure is unchanged by this project, which is worth saying plainly because the
  feature's name suggests otherwise.
- **A reclaimed card that waits in the queue.** Reclaimed in three minutes and dispatched
  hours later if the queue orders by enqueue time; decision 4.

## Verification

PR 4 acceptance is the PRD's Success criterion: `SIGKILL` a worker mid-run, assert the
card is ready within `T_stale + tick_period` and is dispatched again with no human action.
The bound includes the tick period because detection happens on the first tick after the
claim goes stale; `T_stale` alone is not achievable by this mechanism.

Two companion cases, because the single mid-run kill is not the PRD's actual scenario:

- **Kill before the first mark.** Kill the worker within one mark interval of the claim.
  The card is still reclaimed within the same bound — the regression guard for v1's
  headline defect, where such a claim was skipped forever.
- **Kill the work process, leave any parent alive.** Still reclaimed. This pins the
  requirement that the mark dies with the run (§Where the mark comes from).

PR 5 acceptance is Success's second half: the same kill produces exactly one line in the
next digest, and no line in the digest after that — including when the record commits
after the digest run begins, which a timestamp window would drop.

PR 1:

- The epoch is minted at claim and stable for the life of the run; `liveness_at` is
  non-null from claim time onward.
- A worker with a badly skewed clock still produces correct store-side `liveness_at`.

PR 2 (fencing):

- A write carrying a stale or forged epoch is rejected on every enumerated fenced path.
- A write to a card with no current claim is rejected — the reclaimed-but-not-yet-
  redispatched window.
- A write carrying no epoch is accepted and counted while the bypass window is open, and
  rejected after it retires.
- The worker exits on first rejection, including a rejection on the mark path.
- Mixed state: some claims with epochs and some without, sweep on, neither population
  mishandled.

PR 4 (the sweep):

- A mark landing between the sweep's read and its commit prevents the reclaim.
- A worker completing immediately before the sweep wins: the card stays done, its
  outcome is not overwritten, and no reclaim record is written.
- Two sweepers on one stale claim produce one reclaim record, one requeue, one digest
  line.
- A blocked-but-claimed card whose worker dies has its claim cleared and its status left
  blocked, not forced to ready.
- The breaker trips rather than reclaiming when a tick's candidates exceed the threshold,
  and a forward store-clock jump trips it rather than reclaiming the world.
- A sweep stage that throws or exceeds its time bound does not prevent candidate
  selection on that tick.
- **Runnability, not just dispatch:** a killed run that had already produced partial side
  effects is reclaimed and the re-dispatched run completes. PRD Success says "runnable",
  and dispatching is not the same claim.

Standing guards:

- A healthy worker that marks for 10x `T_stale` is never reclaimed — which also pins the
  hung-worker non-goal from the other side.
- A claim with no epoch is not swept, and still expires on its original TTL schedule:
  the regression guard that this design did not change the TTL by accident.
- `T_stale + tick_period >= TTL`, and `tick_period > M`, are both rejected at
  configuration load rather than silently disabling or slowing the feature.
- A reclaimed run is distinguishable from a failed run wherever runs are counted.
- Flag-off regression: with fencing and sweep off, claim and dispatch behaviour match a
  pre-change fixture exactly, while epoch and liveness populate.

## Assumptions

Every line is an inference of mine. The PRD and the card state none of them. The ones
whose falsity changes the design's *shape* rather than its size are escalated as decision
2; the rest are listed because an unflagged inference arrives downstream looking finished.

- Claims are recorded in a durable store that both worker and dispatcher can write, and
  claimed cards can be enumerated from it.
- The store can condition a write on the claim epoch being unchanged and carry the
  sweep's four writes in one transaction (decision 2a).
- A card has at most one claim at a time, and claims are one-to-one with runs.
- The claim transition itself is atomic and conditional on the card being unclaimed. This
  design fences writes to an *already claimed* card and inherits the safety of the
  unclaimed-to-claimed transition without examining it.
- The dispatcher has a periodic tick a sweep stage can attach to, so no new scheduler is
  introduced; the tick period is short relative to `T_stale`; and a stage can read its
  own writes later in the same tick, which is what makes same-tick dispatch true.
- Workers are a single codebase the mark can be added to, with a context whose lifetime
  equals the run's.
- The store stamps liveness on receipt, so worker clocks do not enter the comparison.
- `M` = 60s and a three-mark tolerance are my figures, not the PRD's: a judgment about
  how long a live worker may plausibly go silent, priced in duplicated runs.
- Cards have a queued/ready state, and a reclaimed card in it is treated by the
  dispatcher like any other ready card. Its *position* in that queue is decision 4.
- Re-dispatch may select the same host the dead worker died on; nothing here avoids that.
- Every write path that can mutate a claimed card is enumerable and can be made to carry
  the epoch. If that enumeration is not tractable, decision 3's narrow option is not a
  preference but the only available answer, and PR 2's estimate is fiction either way.
- Runs have an identity and a terminal outcome field that can take a new value; PR 3's
  audit is the check on this rather than the hope.
- The dispatcher has an existing concurrency limit that applies to reclaimed cards
  identically. This is the entire blast bound on a reclaim storm.
- A single claim TTL exists and is visible at configuration load. If the TTL is per-queue
  or dynamic, the `T_stale` guard cannot be validated at load and needs another home.
- The morning digest assembles from durable records, can gain an entry class and a
  footer, and can persist a cursor across runs.
- Nothing downstream treats a claim disappearing before its TTL as impossible.
- The claim TTL is hours, not minutes (decision 2c).
- I did not inspect the implementation. This repo holds documents only and the card
  scopes me to it, so the component names above describe roles, not verified modules.

## Revision history

- **v1** — first draft from `prd.md` at `d76e794`. Scope M, 3 PRs, 3 decisions.
- **v2** — review rework after two independent critiques, both of which returned "not
  safe as-is". Fixed the defect that made the design miss the PRD's primary scenario
  (death before first mark), strengthened the sweep's transaction condition, specified
  fencing in the reclaim window and at rollout, gave owners to two pieces of unowned
  work, replaced timestamp-keyed digest rendering with a cursor, isolated the sweep from
  dispatch, corrected three overstated claims, and added operability the morning after an
  incident actually needs. Scope re-rated L, 5 PRs; decisions 3 to 4.
