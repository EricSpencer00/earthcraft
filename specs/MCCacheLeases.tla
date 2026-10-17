--------------------------- MODULE MCCacheLeases ---------------------------
(* Small model: 3 workers, cap of 3 strip members, 2-member tiles (neighbouring
   tiles share a strip), and one tile larger than the cap. *)
EXTENDS CacheLeases
MCWorkers == {"w1", "w2", "w3"}
MCMembers == {"a", "b", "c", "d", "e"}
MCJobs == [w \in MCWorkers |->
             CASE w = "w1" -> << {"a", "b"}, {"b", "c"} >>
               [] w = "w2" -> << {"c", "d"} >>
               [] w = "w3" -> << {"e", "a"}, {"a", "b", "c", "d"} >>]
=============================================================================
