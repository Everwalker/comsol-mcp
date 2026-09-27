# T037 managed-code OS isolation

Status: the macOS synthetic candidate controls pass, while T037 remains `UNVERIFIED`. This candidate is a runnable harness and policy experiment; it is not production process isolation acceptance.

## Candidate evidence

The final checkpoint is [t037_os_isolation_candidate_20260927T0342Z_final](../evidence/luna_w26_release_foundation/t037_os_isolation_candidate_20260927T0342Z_final/). Its receipt records separate `worker` and `owned_server` profile runs. Both used the same compiled synthetic C probe, not Java Worker or COMSOL Server processes. Both profiles were deny-default and contained no `allow default` rule.

For each run, the probe successfully read and wrote its task scratch file and connected to one exact task-owned loopback listener. It received explicit `EPERM`/`EACCES` results for protected-file read/write, a second loopback port, an unrelated process query and signal, and a `posix_spawn` attempt. The protected file's SHA-256 stayed unchanged; the unrelated `/bin/sleep` sentinel remained alive through the probe and the runner's exact cleanup was confirmed absent by `ps` exit 1 with empty output and error. Focused tests: 16 passed with Python 3.12.13 and pytest 9.1.1. The manifest binds the runner, C probe, test, receipt, profiles, and raw test output.

This candidate has important limits. Both role labels ran under interactive UID 501/GID 20; there was no isolated service account or ACL boundary. The role labels do not turn the C probe into a Worker or a server-side external-execution probe. Descendant policy inheritance, a real server's external execution, Java/COMSOL startup, license-manager traffic, and Windows behavior were not tested. The installed `sandbox-exec` is deprecated, and no supported production policy has been approved. No COMSOL process, model, GUI, study, solver, host policy, account, ACL, firewall, or launchd configuration was changed. Therefore T037 remains `UNVERIFIED`.

The ordinary MCP permission gate and `trusted_code.os_sandbox=false` disclosure remain necessary, but do not constitute OS isolation. A shared or externally owned COMSOL Server cannot satisfy the owned-server boundary.

## Candidate profile behavior

The experimental macOS SBPL profile uses `(deny default)`, a small observed system bootstrap subset, and only these probe-specific allowances: the exact compiled probe executable, one role-specific task scratch directory, and one exact localhost TCP port. The positive and negative controls are observed at runtime; aborts, unknown errors, malformed records, identity mismatches, or cleanup uncertainty do not pass. This policy is only evidence about the synthetic probe on this host and is not a recommended production policy.

The read-only mechanism inventory is recorded at [t037_os_mechanism_inventory.json](../evidence/luna_w26_release_foundation/t035_t039_source_negatives_20260927T0249Z/t037_os_mechanism_inventory.json). It found `sandbox-exec` and `launchctl` and recorded the installed deprecation notice. It did not change accounts, ACLs, firewall rules, launchd, or sandbox policy.

## Work still required for T037

- Integrate an approved, supported OS policy with the actual managed Java Worker and each task-owned COMSOL Server. Test descendant inheritance and a harmless server-side external execution operation without model load/save, study, or solver work.
- Establish and verify a service identity narrower than the interactive account, with reviewed read-only access to required COMSOL/JRE/license files and a private writable task root. Do not silently fall back to the interactive identity or an unisolated/shared server.
- Implement and execute the corresponding Windows restricted-identity and process-policy path. Current read-only inventory found 17 process rows with missing executable/command fields beyond exact kernel identities; those rows cannot be exempted by name.
- Verify license discovery, host identifiers, temporary directories, Java child processes, and exact allowed network destinations under the approved production policy before native COMSOL validation.

Account, ACL, firewall, and host security-policy changes need a concrete reviewed deployment profile and their own authorization. This checkpoint made none of them.
