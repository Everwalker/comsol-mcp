# Portable regression runbook

This runbook applies to `tools/portable_engine_regression.py` and the source
handoff made by `tools/windows_handoff_snapshot.py`. It describes how to
resume a failed run without changing the product contract or rerunning phases
whose inputs are still identical.

## Before starting another run

1. Read the existing run directory first. The first diagnostic source is the
   saved structured result, followed by the raw child stdout, raw child
   stderr, and the recorded exit code. Read the private engine/transport log
   named by that result. An outer shell or PowerShell error is only a wrapper
   symptom until the child exit code and raw logs have been checked.
2. Keep the source snapshot and dependency tuple fixed while diagnosing:
   `snapshot_id`, archive SHA-256, source manifest SHA-256, selected Python,
   pytest/MCP versions, and the exact argument list. A recorded PASS is
   reusable only when those values and the relevant runtime preconditions are
   unchanged. A source or dependency change creates a new snapshot/checkpoint;
   it does not rewrite the old evidence.
3. Run the offline preflight before opening COMSOL or starting a transport:
   verify the extracted snapshot and working directory, runner argument
   syntax, output-directory permissions, JSON output locations, and redaction
   destinations. Confirm that `run_dir` and model paths are outside protected
   private-home/control directories and that the intended output directory is
   private and run-specific. Syntax and CLI checks are sufficient here, for
   example:

   ```text
   <verified-python> -m py_compile tools/portable_engine_regression.py
   <verified-python> tools/portable_engine_regression.py --help
   <verified-python> tools/windows_handoff_snapshot.py --help
   ```

   These checks must not connect to COMSOL, attach to a user model, or remove
   an existing artifact.

## Choose the smallest useful rerun

Classify the first failure before choosing a command:

- **Runner/preflight:** fix only the command, path, schema, or redaction
  preparation and repeat the offline check.
- **Wrapper/transport:** preserve the product and fixture results already
  passed; rerun the smallest transport or teardown check that exercises the
  changed boundary.
- **Harness/fixture:** use the focused test or isolated fixture case against
  the same snapshot. Do not turn a harness failure into a product failure.
- **Product/engine:** retain the failure evidence, change only the authorized
  product area, and rerun the focused regression before the necessary final
  integration set.

Do not rerun because an outer PowerShell command printed `NativeCommandError`,
and do not infer a new root cause from a truncated wrapper message. First save
the child process return code, bounded stderr, stdout/stderr hashes, and the
phase/operation that produced them. Do not change an acceptance threshold to
make a rerun pass.

Each stage checkpoint should identify the snapshot and dependency tuple,
command shape, exit code, raw-output hashes, and the next stage it authorizes.
Reuse an unchanged PASS checkpoint; invalidate only the stages whose inputs or
preconditions changed. Keep PASS, FAIL, BLOCKED, and NOT_RUN distinct.

## Preserve the primary failure through finalization

Capture the primary exception and phase before `finally` begins. Worker release,
control-daemon cleanup, PowerShell diagnostics, and evidence-write failures
are additional observations. They must be recorded in separate fields or
files and must never replace the primary error, phase, or exit status. A
cleanup failure after a product failure remains a product failure with a
cleanup error attached; it is not a new root cause and it is not a PASS.

The finalizer must still attempt only the explicitly owned, authenticated
resources. If a release/idle/identity check is unavailable, record BLOCKED
and preserve the original failure. Ensure a result or crash marker is written
even when finalization itself fails, and verify its presence before interpreting
the run. Never kill an unknown Worker, Server, or active computation to make
cleanup look successful.

For a PowerShell/CIM helper, keep the helper return code and stderr alongside
the Python driver's result. Distinguish “process not found” from helper access
failure and from a Python operation failure. An outer `NativeCommandError`
does not establish which of these occurred.

## Evidence handoff

Private raw logs may retain the exact command and diagnostics needed to
reproduce the run. Public evidence should contain only the snapshot/checkpoint
identity, hashes, counts, phase status, exit code, and redacted command
placeholders. Do not publish account paths, credentials, tokens, raw model
metadata, or unrestricted process command lines. Record the command used to
produce a public record separately from any private full-path command, and
state when an earlier human summary is not the raw test output.

## Approval and execution workflow

Put the exact payload in one immutable handoff record before execution: the
pinned host and destination root, versioned target names, source/archive/
manifest hashes, included data categories, explicit exclusions, and the
current repair iteration. Include the redacted return fields and the rollback
boundary in the same record: only the owned process identity may be stopped,
the original configuration and ACL hashes must be restored, and the listener,
client, and unknown-model gates must pass before cleanup.

An existing approval can be reused for the same host, destination scope, data
categories, and necessary repair iterations in the same round. Ask for
additional approval only when the host or external target, data category,
secret/model scope, or irreversible action changes. Keep raw logs private and
return only the approved status, count, hash, revision, and redacted failure
fields. A review rejection is a stop condition: do not reroute the payload,
weaken the acceptance gate, or assume that automatic review will pass.
