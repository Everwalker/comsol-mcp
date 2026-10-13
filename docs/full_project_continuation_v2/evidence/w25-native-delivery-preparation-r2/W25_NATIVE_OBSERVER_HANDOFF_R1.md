# W25 native Desktop observer handoff R1

Status: **PREPARED / NOT LAUNCHED / WAITING FOR SERIAL NATIVE LANE**.

This is a read-only handoff packet. No COMSOL GUI, CUA, TCC permission API,
model API, solver, or study was invoked while creating it. The full bounded
observation plan remains in `FIRST_GUI_OBSERVATION_PLAN_R2.md`.

## Frozen launch candidate

- Candidate: `DESKTOP_LAUNCH_CANDIDATE_R2.json`, SHA-256
  `8462164d716c482df24eae0fddc2e87fb6954727373f025e1543aa4d918b7278`.
- Task copy: `/private/var/folders/4d/6pphmzkx5szf8dyqx7zhqh1r0000gn/T/comsol-w25-arm64-runtime-r2-37bd820ee138424b8946684619d5bb24/gui-candidate/native_resume_child.mph`; SHA-256
  `9999608757cff08cfe052b398c3de24b1e2e0603dc375db3d0cc5928f77c6346`; 617,475 bytes; device/inode
  `16777231/15412982`.
- Bundle path/ID: `/Applications/COMSOL64/Multiphysics/COMSOL Multiphysics.app` /
  `com.comsol.comsol`. Installed bundle version is
  6.4.0; runtime build 293 still needs verification after an admitted launch.
- Documented argv (not executed): `/Applications/COMSOL64/Multiphysics/bin/macarm64/comsol -open /private/var/folders/4d/6pphmzkx5szf8dyqx7zhqh1r0000gn/T/comsol-w25-arm64-runtime-r2-37bd820ee138424b8946684619d5bb24/gui-candidate/native_resume_child.mph`.
- The candidate retains one study, one solver sequence, and an embedded
  solution. The observation has zero study submissions, solver calls, model
  mutations, and permission prompts.

## Fresh preflight evidence and refusal rule

This agent's `ps` and `pgrep` process inventory requests returned
`operation not permitted`; global COMSOL presence is therefore **UNKNOWN**.
This agent started no child operation and holds no active PID, COMSOL handle,
or GUI session. A future owner must obtain a fresh process inventory and
refuse launch if it is unavailable, ambiguous, or finds any existing COMSOL
GUI/server that conflicts with the serialized native lane.

After the native lane is handed off, require exact PID, process birth time,
UID, executable identity, bundle ID, login/session, and verified launcher
ancestry. A launcher may create a Desktop/JVM process tree; record the exact
owned tree from observed parent/child and identity evidence rather than
assuming one operating-system PID. Bound the entire task and cleanup to the
existing 600-second window. Do not terminate a shared Server.

Check Accessibility and Screen Recording permissions without prompting. A
false/unknown result is a typed refusal. Do not use CUA. Read at most 4,096 AX
nodes from the uniquely matched COMSOL window and pair Quartz window owner/ID
with ScreenCaptureKit window/owner identity. Permit at most one optional
window-only capture after both permissions pass; retain it locally only.
Do not read document text, inspect unrelated windows, focus/click/type, save,
close, mutate, run a study, or call a solver. Do not create the selector
profile until observations from the exact build are reviewed.

## Current frozen evidence boundaries

- ARM64 runtime install receipt remains `d8166ea89a6a12803103d72b04c78b0b44201d5ef39d026692c65833aec13ef7`; the already installed task-local site and its 886-file inventory were independently accepted within the recorded software-only scope. No reinstall or import/test rerun is requested.
- Intel R3 freeze/manifest are in the sibling `INTEL_X86_BUILDER_INPUT_FREEZE_R3.json` and `INTEL_X86_BUILDER_INPUT_MANIFEST_R3.json`; archive acquisition is not approved by this packet and remains **NOT STARTED**.
- GUI observation, runtime build confirmation, selector profile, Desktop-to-Worker binding, native permissions, and scientific acceptance remain **NOT RUN**.
