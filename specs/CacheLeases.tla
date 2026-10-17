---------------------------- MODULE CacheLeases ----------------------------
(***************************************************************************)
(* N regional_generate.py workers share one capped LRU source cache        *)
(* (scripts/regional_cache.py). Each tile needs a set of ArcGrid strip      *)
(* members (~1.1 GiB each, modelled as 1 unit). A worker pins every member *)
(* it fetched for its running tile; only unpinned entries may be evicted.  *)
(*                                                                         *)
(* Fix = FALSE models the pre-fix algorithm: fetch members one by one; when *)
(* the cap refuses a member and another tile is running, release the tile  *)
(* (unpin) and retry it later ("tile_paused_cache"); alone, fail it with a *)
(* capacity receipt. TLC finds a fair livelock (CacheLeases.livelock.txt). *)
(*                                                                         *)
(* Fix = TRUE models the admission control now in regional_cache.py: a     *)
(* worker queues (FIFO) and reserves its tile's whole member set atomically *)
(* before it pins anything; a reservation is granted when the union of all *)
(* reserved members fits the cap (shared strips count once). While waiting *)
(* it holds no pins. A tile whose set cannot fit even with no other        *)
(* reservation fails with a capacity receipt. Eviction skips reserved      *)
(* members.                                                                *)
(***************************************************************************)
EXTENDS Naturals, Sequences, FiniteSets

CONSTANTS Workers,   \* worker ids
          Members,   \* ArcGrid strip members (unit size)
          Jobs,      \* [Workers -> Seq(SUBSET Members)]: each worker's tiles, as member sets
          Cap,       \* cache cap in members
          Fix        \* BOOLEAN: admission control on/off

VARIABLES pc,        \* worker phase
          job,       \* index of the worker's current tile in Jobs[w]
          pins,      \* members pinned by the worker's running tile
          reserved,  \* members reserved for the worker's tile (Fix only)
          queue,     \* FIFO of workers waiting for a reservation (Fix only)
          cache,     \* members present in the cache
          outcome,   \* per worker: sequence of "done"/"capacity" receipts
          refusedWithReservation \* a reserved fetch was refused (must never happen)

vars == <<pc, job, pins, reserved, queue, cache, outcome, refusedWithReservation>>

Need(w) == Jobs[w][job[w]]
AllPinned == UNION {pins[w] : w \in Workers}
AllReserved == UNION {reserved[w] : w \in Workers}
Protected == AllPinned \cup AllReserved

Init == /\ pc = [w \in Workers |-> IF Len(Jobs[w]) > 0 THEN "idle" ELSE "finished"]
        /\ job = [w \in Workers |-> 1]
        /\ pins = [w \in Workers |-> {}]
        /\ reserved = [w \in Workers |-> {}]
        /\ queue = <<>>
        /\ cache = {}
        /\ outcome = [w \in Workers |-> <<>>]
        /\ refusedWithReservation = FALSE

\* Claim the next tile.
Start(w) == /\ pc[w] = "idle"
            /\ IF Fix THEN /\ pc' = [pc EXCEPT ![w] = "waiting"]
                           /\ queue' = Append(queue, w)
                      ELSE /\ pc' = [pc EXCEPT ![w] = "fetch"]
                           /\ UNCHANGED queue
            /\ UNCHANGED <<job, pins, reserved, cache, outcome, refusedWithReservation>>

\* Fix: the head of the queue reserves its full member set atomically, or fails alone.
Reserve(w) == /\ Fix /\ pc[w] = "waiting" /\ queue # <<>> /\ Head(queue) = w
              /\ \/ /\ Cardinality(AllReserved \cup Need(w)) <= Cap
                    /\ reserved' = [reserved EXCEPT ![w] = Need(w)]
                    /\ pc' = [pc EXCEPT ![w] = "fetch"]
                    /\ queue' = Tail(queue)
                    /\ UNCHANGED outcome
                 \/ /\ Cardinality(Need(w)) > Cap      \* cannot fit even alone
                    /\ pc' = [pc EXCEPT ![w] = "next"]
                    /\ outcome' = [outcome EXCEPT ![w] = Append(@, "capacity")]
                    /\ queue' = Tail(queue)
                    /\ UNCHANGED reserved
              /\ UNCHANGED <<job, pins, cache, refusedWithReservation>>

Pin(w, m) == pins' = [pins EXCEPT ![w] = @ \cup {m}]

\* Fetch (or hit) one missing member of the running tile.
Fetch(w) ==
  /\ pc[w] = "fetch"
  /\ \E m \in Need(w) \ pins[w]:
       \/ /\ m \in cache                              \* cache hit
          /\ Pin(w, m) /\ UNCHANGED <<cache, pc, outcome, refusedWithReservation>>
       \/ /\ m \notin cache /\ Cardinality(cache) < Cap \* room
          /\ cache' = cache \cup {m}
          /\ Pin(w, m) /\ UNCHANGED <<pc, outcome, refusedWithReservation>>
       \/ /\ m \notin cache /\ Cardinality(cache) >= Cap \* evict an unprotected entry
          /\ \E e \in cache \ Protected:
               cache' = (cache \ {e}) \cup {m}
          /\ Pin(w, m) /\ UNCHANGED <<pc, outcome, refusedWithReservation>>
       \/ /\ m \notin cache /\ Cardinality(cache) >= Cap \* refused: everything is protected
          /\ cache \subseteq Protected
          /\ UNCHANGED cache
          /\ refusedWithReservation' = (Fix \/ refusedWithReservation)
          /\ pins' = [pins EXCEPT ![w] = {}]
          /\ IF \E v \in Workers \ {w}: pc[v] = "fetch"
               THEN /\ pc' = [pc EXCEPT ![w] = "paused"]   \* tile_paused_cache: release and retry
                    /\ UNCHANGED outcome
               ELSE /\ pc' = [pc EXCEPT ![w] = "next"]     \* alone: capacity receipt
                    /\ outcome' = [outcome EXCEPT ![w] = Append(@, "capacity")]
  /\ UNCHANGED <<job, reserved, queue>>

\* All members pinned: compute, package, unpin.
Finish(w) == /\ pc[w] = "fetch" /\ pins[w] = Need(w)
             /\ pc' = [pc EXCEPT ![w] = "next"]
             /\ pins' = [pins EXCEPT ![w] = {}]
             /\ reserved' = [reserved EXCEPT ![w] = {}]
             /\ outcome' = [outcome EXCEPT ![w] = Append(@, "done")]
             /\ UNCHANGED <<job, queue, cache, refusedWithReservation>>

\* Paused tile goes back to the queue; the worker retries it.
Retry(w) == /\ pc[w] = "paused"
            /\ pc' = [pc EXCEPT ![w] = "fetch"]
            /\ UNCHANGED <<job, pins, reserved, queue, cache, outcome, refusedWithReservation>>

Advance(w) == /\ pc[w] = "next"
              /\ reserved' = [reserved EXCEPT ![w] = {}]
              /\ IF job[w] < Len(Jobs[w])
                   THEN /\ job' = [job EXCEPT ![w] = @ + 1] /\ pc' = [pc EXCEPT ![w] = "idle"]
                   ELSE /\ pc' = [pc EXCEPT ![w] = "finished"] /\ UNCHANGED job
              /\ UNCHANGED <<pins, queue, cache, outcome, refusedWithReservation>>

Done == /\ \A w \in Workers: pc[w] = "finished"
        /\ UNCHANGED vars

Worker(w) == Start(w) \/ Reserve(w) \/ Fetch(w) \/ Finish(w) \/ Retry(w) \/ Advance(w)

Next == (\E w \in Workers: Worker(w)) \/ Done

Spec == Init /\ [][Next]_vars /\ \A w \in Workers: WF_vars(Worker(w))

-----------------------------------------------------------------------------
TypeOK == /\ pc \in [Workers -> {"idle", "waiting", "fetch", "paused", "next", "finished"}]
          /\ cache \subseteq Members
          /\ \A w \in Workers: pins[w] \subseteq Members /\ reserved[w] \subseteq Members

CacheWithinCap == Cardinality(cache) <= Cap
PinnedWithinCap == Cardinality(AllPinned) <= Cap
NoLostPins == AllPinned \subseteq cache           \* a pinned member is never evicted
PinsWithinReservation == Fix => \A w \in Workers: pins[w] \subseteq reserved[w]
ReservedFetchNeverRefused == ~refusedWithReservation

\* Liveness: every tile ends with a receipt (done or capacity); no livelock.
AllTilesReceipted == <>(\A w \in Workers: Len(outcome[w]) = Len(Jobs[w]))
EachTileProgresses == \A w \in Workers: \A k \in 1..Len(Jobs[w]):
                        (pc[w] # "finished" /\ job[w] = k) ~> (Len(outcome[w]) >= k)
=============================================================================
