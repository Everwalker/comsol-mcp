# T037 read-only native policy unit handoff

Status: **SCOPED SOFTWARE ONLY; native Windows and overall T037 acceptance remain NOT_RUN/OPEN.**

This unit adds a closed v2 Windows isolation profile bridge and a late-bound read-only Windows SCM/WFP inventory adapter. The adapter checks fixed-width Win32 ctypes layouts, pages providers/sublayers/filters under explicit page/row/time/byte bounds, hashes canonical service/WFP ACL material, samples SCM process identity on both sides of WFP enumeration, and rejects incomplete/unknown observations. It never issues a trusted policy binding. Injected observers are software fixtures.

The closed HostChangePlan/journal contract remains plan-only. No OS mutation, account, service, ACL, WFP, firewall, quota, launch, COMSOL, or GUI action occurred. Host apply/restore and trusted policy proof issuer are still unimplemented and typed-refused.

Software evidence: 47 T037 native/profile tests passed; 6 plan/journal focused tests passed; 59 resource-file tests passed (the focused 6 are included in the 59, so run totals overlap). Python compile passed. Strict guards recorded one activation event and no deny events per final test run. See `T037_SOURCE_FREEZE.json` for exact hashes and artifact inventory.

Windows kernel/SCM/WFP native tests: **NOT_RUN**. Effective WFP arbitration, fully interpreted layer/condition/provider-context/callout semantics, ACL-to-profile comparison, exact process tree/Job binding, COMSOL/JDK loopback compatibility, native proof issuer, and apply/restore are open implementation work.

SCM launcher gap: a future production broker must implement or use a verified `StartServiceCtrlDispatcher` → `ServiceMain` → control-handler/status-report protocol. A service registration name or an observed `mphserver.exe`/`python.exe` is insufficient evidence that the SCM PID is the trusted broker. The service PID/birth/image/argv must bind to that trusted launcher separately from Server and Worker witnesses. The exact Microsoft reference used for this follow-on is preserved in `evidence/w26_t037_scm_entrypoint_reference_20261012_r1.json`.

This receipt is a unit handoff only. It does not mark MASTER_GOAL, W01–W26, T037, native acceptance, or independent review complete.
