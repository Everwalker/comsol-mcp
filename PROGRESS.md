# Progress — r409 local four-file materialization candidate

Main accepted fresh Stage1 materialization: creator CLOSED_KNOWN_ZERO plus original outer terminal0/noSession and original local wait0. Postimage: 1870 files, 209 directories, 436922428 bytes, all expected hashes aligned; 515 source-at-use reads include 504 JDK files, with 95 ancestor records. Close evidence is the source-backed retire_known success gate, without a serialized per-handle close table.

The original Stage1 owner is SPENT; no more receive, lease extension or replay. New phase2 is NOT_RUN: only LOCAL data preparation against this accepted input, with separate Main packet/dispatch/READY/GO authorization still required. Normal Windows installation remains FAIL; old pip entry and root postimage remain UNVERIFIED. Earlier 75965/40869 gaps and all known failures remain unchanged, without a causal conclusion from this success.

Publication 675d3471 is Main remote accepted. Product .15/95, accepted artifacts/scoped Mac .15 actual and the U12 SOURCE supplement are unchanged. Five fences/frozen readbacks/273-60-427/completed_groups=[]/heldouts NOT_SELECTED/final Reviewer NOT_STARTED and historical Mac Core block remain unchanged. Four-file candidate awaits Main gate; no commit/push.
