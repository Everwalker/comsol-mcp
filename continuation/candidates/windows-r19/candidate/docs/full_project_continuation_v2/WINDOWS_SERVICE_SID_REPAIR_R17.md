# R17 narrow corrections to the existing-service SID leaf

R16 remains an immutable CHANGES_REQUIRED baseline. R17 preserves its whole 406
source closure and repairs the two concentrated P2 findings. Source/software
checks do not constitute Windows-kernel, current token or full T037 acceptance.

The complete actual closed backup document and the proposed baseline are compared
as canonical bytes to the exact expected current subject. Exact keys and the
original canonical subject hash remain required. Python dictionary equality can
coerce false/true to 0/1 and finite floats to integers; those byte-distinct inputs
are now refused before admission, one-use authorization consumption or any SDK
configuration. No invalid data is normalized. Original restore SID types must be
actual integers 0 or 1. The original valid integer apply and original-receipt,
current-exact, pinned-backup restore behavior is preserved.

The new private SCM reader no longer treats successful pcbBytesNeeded as actual
returned length. QueryServiceConfigW, QueryServiceConfig2W and
QueryServiceObjectSecurity retain the first real ERROR_INSUFFICIENT_BUFFER size,
validate its documented bounds, allocate and retain that original storage before
the successful call, and validate the successful structure and actual in-buffer
pointers against that allocation. QueryServiceStatusEx uses its fixed original
SERVICE_STATUS_PROCESS buffer. The successful size output may remain zero or be
otherwise unspecified. The private security query is capped at the documented
8192 bytes. False/malformed BOOLs, malformed first sizes, payload pointers and
security descriptors remain refused. ReadFile returned-byte counts and
LookupAccountNameW in/out sizes keep their original stricter contracts. There is
no extra query or automatic SDK retry. Public observe_service, other SDK families,
registry, ownership, nonce, journal, receipt and native-proof predicates remain
byte unchanged from R16.

Controls use the actual original sealed SCM issuer → Controller → Executor →
OperationStore / HostScopedProvider → real private subject/backup functions with
public synthetic SDK data only. They cover bool/finite-float mismatches in the
whole backup and proposed baseline, legal success/no-needed-write behavior of all
four APIs, invalid first sizes and actual pointers, and simultaneous four-API
unwritten-size integer-0/1 apply plus original restore. Existing ownership,
UNKNOWN, first-cause, GC, original-closer and explicit-cleanup controls remain.
The corrected old status_false and needed_truncated negative fixtures test real
false return / malformed first size, rather than unspecified success outputs.
Old R16 and independent failed attempts and R17 failed attempt evidence remain.

The original macOS ten memory-runtime FAILs are inherited byte exactly with raw
R16 observations and older R13/R14 baseline/diagnostic references. They were not
rerun in R17 or reclassified; the final source regression explicitly deselects
the same ten names. Windows-only native test skips remain NOT_RUN. Existing
service SID setup remains IMPLEMENTED_SOURCE_UNVERIFIED; the next system start
and effective token are untested. First account/service bootstrap, AppContainer
and staged root DACL, loopback/vendor, storage-backed quota, effective whole-tree
filesystem/network policy and native child issuer/Factory resume remain original
OPEN requirements. R17 introduces no new authority framework or OS proof.
