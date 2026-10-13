# T037 R2 page-allocation cleanup handoff

R2 is a narrow correction to R1. The production WFP _enum call now places Enum0, status validation, the post-call time-budget check, and all page decoding inside the allocation try/finally. Any non-null page returned on success or error is released once. If release raises, the result is typed T037_NATIVE_WFP_HANDLE_RELEASE_UNKNOWN and includes the primary failure code/type. Enum-handle destruction, read-only transaction abort, and engine close retain their established cleanup path.

Three production _enum API-shim controls cover (1) a successful non-empty page returned as the time budget expires, (2) an error status with a non-null page allocation, and (3) page-free failure while an Enum0 query error is primary. The first two assert one free, one enum-handle destroy, transaction abort, and engine close; the third additionally checks typed UNKNOWN retains the original query code.

Focused software evidence: test_windows_t037_native.py + test_windows_t037.py: 50 passed, 0 failed/errors/skips. py_compile passed. Strict guard: one active event, zero deny events. Windows kernel/SCM/WFP native tests remain NOT_RUN. No OS or COMSOL action occurred.

R1 is preserved unchanged at local-preservation/w26_t037_native_policy_20261012_r1/ and its original freeze/evidence are untouched. R2 copies all eight R1 source-scope files; exactly two source files differ. This is a scoped cleanup fix only: native policy proof issuer, complete effective WFP arbitration, host applier/restore, trusted SCM broker entrypoint/launcher binding, and overall T037 acceptance remain OPEN.
