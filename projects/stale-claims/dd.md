# DD: Reclaim stale card claims

Covers: `projects/stale-claims/prd.md` (commit `d76e794`). Revision v1.

## Review header

### Decisions needed from you

1. **A card that keeps killing its worker.** Reclaim returns the card to the queue and
   the dispatcher picks it up again. If the card is *what kills the worker*, that loop
   has no exit: it burns a worker every few minutes and writes a digest line every
   time. The obvious brake is a cap — after N consecutive reclaims with no completed
   run in between, stop requeueing and park the card for a human. But a cap is attempt
   accounting, and attempt accounting is the shape of your "retrying the work
   automatically" non-goal. I propose a cap of 3, because an uncapped loop turns one
   dead worker into an unbounded number of them, which is a worse failure than the one
   the PRD is fixing. The cost is real though: a card reclaimed three times for three
   *unrelated* infrastructure outages stops dead instead of recovering, which is
   exactly the outcome the PRD wants to prevent. The non-goal is yours to bend or hold.
2. **Whether the claim record already carries a liveness signal, and whether the store
   can do a conditional write.** I could not see the implementation (see Assumptions).
   This design adds a per-claim epoch and a store-stamped liveness mark, and leans on a
   compare-and-set to make the sweep safe. If claims already carry a heartbeat, PR 1
   collapses to the fencing work alone. If the store *cannot* condition a write on the
   epoch, the design changes shape rather than shrinking — the sweep would need an
   external lock, and a false reclaim would stop being survivable.
3. **Whether the operator should hear about it before morning.** Success says the event
   appears in the morning digest. Outcome says the operator "learns that it happened
   rather than discovering it later" — and next morning is later. I propose
   digest-only: once reclaim works, the notification is a record rather than a rescue,
   because the card is already moving again by the time anyone could read an alert. If
   you want an immediate alert as well, it is a small addition to PR 3, but it lands on
   an alert surface this PRD does not own.

### Council dissent

Independent reviewer delegation was unavailable at v1. The critique below is my own,
run as three passes over the draft — correctness, PRD fidelity, operations. Treat it as
self-review, not as three reviewers.

- **Shortening the lease vs. adding a second signal.** The correctness pass started from
  the simplest reading: the claim looks live because the TTL is long, so detect lapse
  sooner. The fidelity pass rejected every version of that — "changing the claim TTL" is
  an explicit non-goal, and a shorter *effective* lease is a changed TTL whatever it is
  called. The fidelity pass prevailed, and that produced the design's spine: liveness is
  a second, independent signal, and the TTL is untouched and stays as the outer backstop.
- **Whether fencing belongs in this PRD at all.** The fidelity pass called claim fencing
  scope the PRD does not grant; it asks for reclaim and notification, not for a
  concurrency control. The correctness pass prevailed, and I record the disagreement
  rather than hide it: reclaiming promptly means reclaiming on evidence that is
  sometimes wrong, and an unfenced false reclaim puts two live workers on one card.
  Today's slow TTL does not carry that hazard. Fencing is not an improvement bundled in
  — it is what keeps "prompt" from being a regression.
- **The supervisor fast path.** The operations pass wanted the parent process to notice
  its child exited and reclaim instantly, which is both promptest and most accurate for
  the common case. The fidelity pass killed it on budget: the PRD's unit of harm is
  "most of a day", and minutes already close that gap to the point where the difference
  is invisible to the stated Outcome. It is now an explicit non-goal rather than an
  unbuilt idea, so a later revision cannot adopt it as a refinement.
- **Reclaimed vs. failed as a run outcome.** Settled without escalating. A reclaimed run
  made no claim about the work, so folding it into `failed` would corrupt every count of
  how often a card fails on its merits. It gets its own terminal value.

### Scope and PR breakdown

**Scope: M.** Three PRs. The size is not in the sweep, which is small — it is in PR 1,
which touches every write path that can mutate a claimed card. Nothing here changes the
claim TTL, the dispatcher's selection logic, or any existing digest entry.

- **PR 1 — liveness and fencing.** Claim epoch minted at claim time, store-stamped
  liveness mark, and an epoch condition on every write that touches a claimed card. No
  sweep. Ships **on**: behaviour is unchanged, but both fields must be populated with
  real data before the sweep in PR 2 can be correct, and fencing must exist before
  anything can reclaim.
- **PR 2 — the sweep.** Stale detection, the reclaim transaction, the reclaim record.
  Ships behind a flag, flipped on against already-warm liveness data. Delivers the
  Success criterion's first half: killed worker, card runnable again, nobody intervened.
- **PR 3 — digest entry.** Reclaim records rendered as a new digest entry class.
  Delivers the second half.

The order is forced, not a preference. A sweep without fencing converts a false reclaim
into two live writers; a digest without reclaim records has nothing to read.

## Non-goals

- Preventing crashes (PRD).
- Changing the claim TTL — its value, its semantics, or its effective length by any
  indirect route (PRD). `T_stale` is a separate parameter on a separate signal, and the
  TTL remains the sole governor of claims the sweep skips.
- Retrying the work automatically (PRD): no attempt counter, no backoff, no failure
  accounting, no re-execution policy. A reclaimed card re-enters the queue on exactly
  the terms a never-claimed card does. Decision 1 is the single place this boundary is
  under pressure, and it is escalated rather than quietly crossed.
- Undoing or compensating any side effect of the dead run. A branch it pushed, a PR it
  opened, a message it sent all survive the reclaim, and the next run may repeat them.
- Cleaning, resetting, or reusing the dead run's workspace. Reclaim returns the card,
  not the world.
- Reaching the dead worker's process or host in any way — no kill, no signal, no remote
  login. The sweep acts only on the store, because by hypothesis the worker is gone.
- Detecting a *hung* worker that is alive and marking liveness but making no progress.
  Liveness means the process exists, not that it is working.
- A supervisor-reported fast path (parent observes child exit, reclaims immediately).
  See Council dissent.
- Changing blocked-card digest behaviour, digest scheduling, or the per-gate alert.
- Surfacing reclaims anywhere other than the morning digest (see decision 3).

## Design

### Mechanism: a second signal, not a shorter lease

The claim TTL may not change, so reclaim cannot come from expiring the lease sooner. It
comes from a second signal, independent of the lease: the worker periodically marks that
it is alive, and a sweep reclaims claims whose marks have stopped. A claim then has two
clocks that never interact —

- the **TTL**, untouched, which answers "may this claim still be held?";
- **`T_stale`**, new, which answers "is anyone still holding it?".

`T_stale` is a multiple of the mark interval `M` — proposed `M` = 60s, `T_stale` = 3M =
3 minutes, so a worker must miss three marks before it is declared dead. Against a TTL
measured in hours, that is the whole of the PRD's "promptly". Configuration must reject
`T_stale` >= TTL: in that arrangement the TTL always fires first and the feature is inert
while appearing to be installed, which is the failure mode that looks like success.

Two fields carry it:

- **`claim_epoch`** — minted when the claim is taken, unique to that claim, held by the
  worker for the life of its run.
- **`liveness_at`** — the time of the worker's last mark, **stamped by the store on
  receipt**, never sent by the worker. A worker with a wrong clock therefore cannot make
  itself look alive, or dead.

The mark must be emitted from somewhere that survives the worker's own work — a timer
thread, or the wrapper supervising it — and not from the work loop. A mark that only
fires between units of work makes any long synchronous step look like death.

### Fencing

Prompt reclaim buys the day back by acting on evidence that is sometimes wrong. A worker
that was merely silent — paused host, starved timer thread, a stop-the-world pause — is
reclaimed as if dead, and its card handed to someone else while it is still running. The
slow TTL never had that hazard, so this design has to carry the control that contains it.

Every write that touches a claimed card — the liveness mark, completion, block, status
change, any worker-attributed write — carries the writer's `claim_epoch`, and the store
rejects it when that epoch is not the card's current claim epoch. The first rejected
write is the zombie's signal that it no longer owns the card, and its instruction to stop.

This is what makes a false reclaim merely wasteful instead of corrupting: the cost is one
duplicated run, not two writers racing on one card's terminal state. It is also why PR 1
ships before PR 2 rather than alongside it.

### The sweep

A stage on the dispatcher's existing tick, placed immediately **before** candidate
selection, so a card reclaimed on a tick is dispatchable on that same tick rather than
the next one. It needs no scheduler of its own.

For each claimed card where `now - liveness_at > T_stale`, in one transaction conditioned
on `claim_epoch` being unchanged:

1. terminate the run with outcome `reclaimed`;
2. clear the claim;
3. return the card to the queue as ready;
4. write a reclaim record.

The epoch condition is what makes the sweep safe to run from more than one process and
safe to retry: two sweepers racing on one stale claim produce one reclaim, and the
loser's transaction fails its condition and does nothing. It also resolves the narrow
race where the worker completes the card microseconds before the sweep reads it — the
completion moves the claim on, the sweep's condition fails, and a finished card is not
resurrected.

A claim with **no `liveness_at` at all** is not stale, it is unknown, and the sweep skips
it. Claims taken before rollout therefore keep expiring by TTL exactly as they do today,
and rollout needs no backfill.

The sweep never kills anything. It has no authority over the worker's host and, by
hypothesis, no process to kill.

### Reclaim record

One row per reclaimed run — `(card_id, run_id, claim_epoch, claimed_at, liveness_at,
reclaimed_at)` — written inside the sweep transaction, so the record and the requeue
cannot disagree. It is the only durable evidence that a day was saved, and the digest's
only input.

The run's own terminal outcome is `reclaimed`, distinct from `failed`. A reclaimed run
made no claim about the work; folding it into `failed` would corrupt every count of how
often a card fails on its merits, and "this card keeps failing" is a sentence an operator
needs to stay true.

### Digest surfacing

A new entry class listing reclaims since the previous digest: one line per reclaim
record, naming the card, how long its claim had been silent, and when it was reclaimed.

A reclaim record renders **once**, in the first digest after it is written, keyed by
`reclaimed_at`. No aging, no repetition, no suppression logic: a reclaim is an event, not
a state, and the thing that makes blocked cards repeat in the digest — the condition
persisting across days — has no analogue here. This is deliberately independent of the
blocked-card dedup work in `projects/digest-dedupe`; the only coupling is layout, so
whichever of the two lands second rebases on the other's section arrangement.

### Failure modes

- **A live worker that went quiet.** Reclaimed, re-dispatched, so the work runs twice.
  The zombie is stopped at its next write by fencing, and loses whatever it had done
  since. Bounded to one duplicated run *inside the store* — side effects it already
  emitted outside the store are neither fenced nor undone. The real mitigations sit
  upstream of fencing: the mark being independent of the work loop, and `T_stale` being
  several marks wide rather than one.
- **A card that kills its worker on contact.** Unbounded reclaim loop; see decision 1.
  This is the only failure mode in this list I am not settling.
- **Side effects of the dead run.** Not undone, by design. The next run starts in a world
  the dead run already changed, and may change it again.
- **Workspace state.** For a shared or directory workspace, the new run starts on
  whatever the dead run left behind. No clean, no revert, no isolation added here.
- **The sweep itself not running** (dispatcher down, stage crashed, flag off). Nothing is
  reclaimed and the untouched TTL remains the backstop, so the system degrades precisely
  to today's behaviour rather than to something new. That property is the second reason
  to leave the TTL alone, beyond its being a non-goal.
- **Reclaim storm.** An infrastructure outage kills many workers at once; the next tick
  reclaims all of them and the dispatcher re-dispatches up to its existing concurrency
  limit, which bounds the blast. The digest is not bounded: it gets one line per reclaim
  and will be long on such a morning. Accepted — a long digest on an outage morning is
  information, not noise.
- **Store clock shift.** Liveness is stamped and compared by the store, so every
  threshold moves together and no worker is singled out. A backwards jump delays reclaim;
  a forwards jump can reclaim a cohort of live workers at once, which fencing contains.

## Verification

PR 2 acceptance is the PRD's Success criterion, almost verbatim: `SIGKILL` a worker
mid-run, assert the card is ready within `T_stale` and is dispatched again with no human
action taken.

PR 3 acceptance is its second half: the same kill produces exactly one line in the next
digest, and no line in the digest after that.

Fencing (PR 1), each as a case:

- After a reclaim, the zombie's mark, completion and block are all rejected, and the
  card's state is unchanged by any of them.
- A worker that completes immediately before the sweep wins: the card stays done, the
  sweep's epoch condition fails, and no reclaim record is written.
- Two sweepers on one stale claim produce one reclaim record, one requeue, one digest
  line.

Standing guards:

- A healthy worker that marks liveness for 10x `T_stale` is never reclaimed. This pins
  the "hung worker" non-goal from the other side too: alive and marking is not reclaimed,
  whatever it is doing.
- A claim with no `liveness_at` is not swept, and still expires on its original TTL
  schedule — the regression guard that this design did not change the TTL by accident.
- `T_stale` >= TTL is rejected at configuration load rather than silently disabling the
  feature.
- A reclaimed run is distinguishable from a failed run wherever runs are counted.
- Flag-off regression: with the sweep off, claim and dispatch behaviour match a
  pre-change fixture exactly, while epoch and liveness populate.

## Assumptions

Every line is an inference of mine. The PRD and the card state none of them.

- Claims are recorded in a durable store that both the worker and the dispatcher can
  write, and claimed cards can be enumerated from it.
- The store can condition a write on the claim epoch being unchanged, and can carry the
  sweep's four writes in one transaction. Decision 2 exists because this is load-bearing.
- The dispatcher has a periodic tick that a sweep stage can attach to, so no new
  scheduler or daemon is introduced.
- A worker can emit a cheap periodic mark from a context that survives its own work loop.
- The store stamps liveness on receipt, so worker clocks do not enter the comparison.
- Cards have a queued/ready state, and a reclaimed card sitting in it is
  indistinguishable to the dispatcher from any other ready card.
- Every write path that can mutate a claimed card is enumerable and can be made to carry
  the epoch. Fencing with one gap in it is not fencing.
- Runs have an identity and a terminal outcome field that can take a new value without
  other readers treating the new value as failure.
- The morning digest assembles from durable records and can gain an entry class.
- Nothing downstream treats a claim disappearing before its TTL as impossible.
- The claim TTL is long relative to minutes — the PRD's "most of a day" implies hours —
  so a three-minute `T_stale` is meaningfully prompter. If the TTL is already minutes,
  the premise of the PRD's Problem section is different from what I read.
- I did not inspect the implementation. This repo holds documents only and the card
  scopes me to it, so the component names above describe roles, not verified modules.

## Revision history

- **v1** — first draft from `prd.md` at `d76e794`.
