------------------------------- MODULE Deploy -------------------------------
(***************************************************************************)
(* The AWS box's update timer (`earthcraft-pipeline update`, every 30 min, *)
(* scripts/aws_pipeline_setup.sh): fetch -> decide -> fast-forward ->       *)
(* install requirements -> install launcher/units -> restart. Any step can *)
(* fail (set -e aborts the tick); the timer fires again later. GitHub may  *)
(* advance the branch a bounded number of times.                           *)
(*                                                                         *)
(* Mode "head":  compare FETCH_HEAD with HEAD (pre-#29). A tick that dies  *)
(*               after the fast-forward is skipped forever.                *)
(* Mode "pr29":  #29 as merged (de5c4cc): compare with the deployed marker *)
(*               (HEAD when absent), write the marker, then restart.      *)
(* Mode "fixed": the marker falls back to "none" (a box that never         *)
(*               recorded a deploy redeploys once) and is written only     *)
(*               after the restart succeeded.                              *)
(* Every commit is assumed to change requirements and the launcher (worst  *)
(* case), so "diff old new" is just old # new.                             *)
(***************************************************************************)
EXTENDS Integers

CONSTANTS Commits,      \* ordered commit ids, e.g. 0..2
          Mode,         \* "head" | "pr29" | "fixed"
          MaxFailures,  \* total failed steps (failures eventually stop)
          StartStuck    \* TRUE: start in the observed state (HEAD fast-forwarded, old launcher, no marker)

None == -1

VARIABLES remote,    \* tip of the branch on GitHub
          head,      \* checkout HEAD
          fetched,   \* FETCH_HEAD of the running tick
          old,       \* what the running tick compares against
          reqs,      \* commit whose requirements are installed
          launcher,  \* commit of /usr/local/sbin/earthcraft-pipeline
          running,   \* commit the pipeline service runs
          marker,    \* /var/lib/earthcraft/deployed (None = absent)
          step,      \* position inside the update tick
          failures

vars == <<remote, head, fetched, old, reqs, launcher, running, marker, step, failures>>

Init == /\ remote = IF StartStuck THEN 1 ELSE 0
        /\ head = remote
        /\ fetched = 0 /\ old = 0
        /\ reqs = 0 /\ launcher = 0 /\ running = 0
        /\ marker = None
        /\ step = "idle"
        /\ failures = 0

MaxCommit == CHOOSE c \in Commits: \A d \in Commits: d <= c

\* GitHub: a new commit lands on the branch (bounded).
Push == /\ remote < MaxCommit
        /\ remote' = remote + 1
        /\ UNCHANGED <<head, fetched, old, reqs, launcher, running, marker, step, failures>>

\* A step either succeeds (doing `ok`) or fails and aborts the tick.
Try(from, ok) == /\ step = from
                 /\ \/ ok
                    \/ /\ failures < MaxFailures
                       /\ failures' = failures + 1
                       /\ step' = "idle"
                       /\ UNCHANGED <<remote, head, fetched, old, reqs, launcher, running, marker>>

Fetch == Try("idle", /\ fetched' = remote
                     /\ step' = "decide"
                     /\ UNCHANGED <<remote, head, old, reqs, launcher, running, marker, failures>>)

Baseline == CASE Mode = "head"  -> head
              [] Mode = "pr29"  -> IF marker = None THEN head ELSE marker
              [] Mode = "fixed" -> marker   \* None never equals a commit: redeploy

Decide == /\ step = "decide"
          /\ old' = Baseline
          /\ step' = IF Baseline = fetched THEN "idle" ELSE "ff"
          /\ UNCHANGED <<remote, head, fetched, reqs, launcher, running, marker, failures>>

FastForward == Try("ff", /\ head' = fetched /\ step' = "reqs"
                         /\ UNCHANGED <<remote, fetched, old, reqs, launcher, running, marker, failures>>)

InstallReqs == Try("reqs", /\ reqs' = fetched /\ step' = "launcher"
                           /\ UNCHANGED <<remote, head, fetched, old, launcher, running, marker, failures>>)

\* #29 also reinstalls when the installed launcher differs from the checkout (cmp).
InstallLauncher == Try("launcher", /\ launcher' = fetched
                                   /\ step' = IF Mode = "pr29" THEN "mark" ELSE "restart"
                                   /\ UNCHANGED <<remote, head, fetched, old, reqs, running, marker, failures>>)

MarkBeforeRestart == /\ step = "mark"     \* #29: marker written before the restart
                     /\ marker' = fetched /\ step' = "restart"
                     /\ UNCHANGED <<remote, head, fetched, old, reqs, launcher, running, failures>>

Restart == Try("restart", /\ running' = head
                          /\ marker' = IF Mode = "fixed" THEN fetched ELSE marker
                          /\ step' = "idle"
                          /\ UNCHANGED <<remote, head, fetched, old, reqs, launcher, failures>>)

Tick == Fetch \/ Decide \/ FastForward \/ InstallReqs \/ InstallLauncher \/ MarkBeforeRestart \/ Restart

Next == Push \/ Tick

Spec == Init /\ [][Next]_vars /\ WF_vars(Tick)

-----------------------------------------------------------------------------
TypeOK == /\ {remote, head, fetched, reqs, launcher, running} \subseteq Commits
          /\ marker \in Commits \cup {None}
          /\ failures \in 0..MaxFailures

\* The marker never claims more than what is checked out.
MarkerNotAhead == marker # None => marker <= head

\* Eventually the deployed launcher, requirements and running code match the fetched (remote) commit, and stay.
Converges == <>[](launcher = remote /\ reqs = remote /\ running = remote)
=============================================================================
