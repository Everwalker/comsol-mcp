# Original SCM endpoint publisher — source and software scope

The two new modules implement transport publication for the existing original
same-process SCM runtime. They do not grant host mutation, AppContainer creation,
qualification, Resume, COMSOL execution, or native isolation authority.

Main accepted the bounded source/software unit after the 1788-test coherent
integration and mechanical input review. The shared Controller now contains the
exact tested two-function hook (`c0e6c679294c0255ecf0e21f7a9587adb90b14358329ee450c54b6336281cc5d`).
The current compact is [WINDOWS_SCM_PUBLISHER_INTEGRATION.json](evidence/WINDOWS_SCM_PUBLISHER_INTEGRATION.json).
Its projection excludes later configuration/typed-authoring changes; a combined
current-tree or native acceptance is not claimed. The original 282-test packet,
its older base `830690db5a46a364d5a32b6f54fffcca4c8db6b2`, and its provenance limits
remain historical evidence. Windows deployment and SDK behavior remain unqualified.

## Interface and original ownership

`_windows_scm_publisher.prepare_home(home, runtime)` requires the original live
START_PENDING callback/status object. The native path independently observes the
current SYSTEM token, no impersonation, liveness/birth/image, configured own-process
service argv and restricted service SID. It checks existing pinned ancestry/home,
DACL, actual MIC and persistent ACL support before any singleton/daemon writes.
Native mode requires the real SCM SDK adapter; the private software adapter is
explicitly a test boundary.

The helper acquires the original `ProcessLock` itself. The actual published class
locks in `__init__`, not `__enter__`. The original instance is rooted before that
constructor is entered. The same original TextIOWrapper, fd, borrowed CRT HANDLE,
file identity/security and original constructor/closer remain correlated. No
CloseHandle owns the borrowed CRT HANDLE. No additional startup config or CLI is
introduced, and the ordinary console route keeps its original behavior.

`bind_original(daemon, source_sha256)` correlates the actual Controller/profile,
source path/hash, service name/SID/SYSTEM account, and configured launcher image/
argv digest. `listener(handler)` owns one bound loopback listener; `publish(server)`
uses only its port, original token generator, current PID and native birth.
`before_running()` verifies the original writer/delete handles have confirmed
closes and rechecks the final name against the original published object. Only
then does the Controller hook execute its existing RUNNING/binding.

No SDK argument, PID, boolean, JSON, SID string or caller-supplied file object can
substitute for that acquisition path. These are finite transport checks within
the SYSTEM/admin/service TCB, not a global file/DLL/process-generation theorem.

## Closed provisioned policy

The home must already exist: SYSTEM owner, SYSTEM/admin group, protected basic
DACL, no reparse/hardlinks, and persistent filesystem ACLs. The closed home DACL
contains SY/BA full control with OI|CI, exact observed service SID data rights
bounded by `0x1301BF` with OI|CI, and zero or one explicit noninheriting user ACE
`0x1200A0`. The selected SID must actually resolve to SidTypeUser. Unknown/group/
multiple/inherited/foreign/World/AppContainer/security-rights policy refuses.
There is no fallback that repairs a home or relaxes the default private helper.

The final stage uses explicit SYSTEM owner, protected DACL, SY/BA full control,
exact service SID `0x1301BF`, optional same user `0x120089`, all flags zero, and
explicit Medium NW label in its creation SD. The actual label is queried
separately: Medium or higher, NW only, no NR/NX or absent-label inference. The
home user ACE does not inherit to database/WAL/lock/log/config files. Existing
lock security is checked before opening; its original stream is checked after
acquisition. Existing unrelated state ACLs are not repaired or certified.

**Actual restricted SYSTEM creation of that SD/LABEL and cross-user access are
UNQUALIFIED.** Native Windows/SCM/HTTP, provisioning, enforcement and host changes
are NOT_RUN in this packet.

## Publish, ambiguity and withdrawal

The private payload has exactly port/token/pid/process_start_epoch_ms and a 16KiB
bound. It is never printed, hashed for evidence, or included in errors/repr.
The helper creates one exclusive random relative stage with final SD/LABEL,
writes completely, flushes and compares bytes/security/identity through the
original HANDLE. A validated ordinary stale endpoint can be replaced without
using its PID for process control. Corrupt/foreign/drifted data is preserved.

Rename is same-volume and relative to the original correlated home handle, with
explicit replace and captured original SDK call/buffer/IO_STATUS. Incompatible
inspection handles close before rename. Consumer no-DELETE sharing causes safe
refusal; there is no force/POSIX/truncate fallback or automatic retry. Writable
handles close before RUNNING; the retained final pin is compatible readonly.

Stop closes the original listener before comparing this exact final identity,
bytes/DACL/LABEL. It deletes via the original acquired DELETE HANDLE, confirms
close and bounded named absence, then drains original resources and closes the
original singleton once. Foreign drift and any possibly effective unknown
create/rename/delete/close retain originals without replay or replacement owner.
Successful native effects with a lost metadata acknowledgement may complete only
the original cached acknowledgement and remaining original lifecycle steps;
`recover_publish_ack()` / `recover_withdraw_ack()` never issue another rename/
delete. No old bearer is restored. The private roots intentionally retain UNKNOWN
owners through GC. Process loss remains UNKNOWN.

## Software validation and reproduction

The packet runner uses `git archive` of exact830690 regular source/tests/support,
only the leased new files and the private Controller patch. It records each Git
blob, SHA256 before/after, actual SourceFileLoader and Python module execution,
imports, JUnit and denied boundary attempts. TEMP/provider fixtures use shallow
`/private/tmp`; HOME/home/CODEX_HOME are unchanged. Run from the repository with
its existing pytest environment:

```
.venv/bin/python -I -B _state/universal-windows-scm-publisher/run_publisher.py final-NEW_LABEL
```

The output label must be fresh. Earlier frozen failures/runs are preserved.
Software SDK/HTTP seams use actual ctypes output storage and genuine original
broker/Controller/consumer functions. The full flow tests include the original
ProcessLock and local file descriptors. Teardown of intentionally ambiguous
SOFTWARE cases closes only their test-owned macOS stream; it does not report a
production UNKNOWN as confirmed cleanup. No real Windows SDK/HTTP/service,
COMSOL/JVM or child is run. Historical 108/173/1366 results are not added to this
unit. The original packet predates the separately reviewed integration and its publication.

## Documented APIs

- [NtCreateFile](https://learn.microsoft.com/en-us/windows-hardware/drivers/ddi/ntifs/nf-ntifs-ntcreatefile): captured original output HANDLE/IO_STATUS, exclusive create and synchronous I/O.
- [NtSetInformationFile](https://learn.microsoft.com/en-us/windows-hardware/drivers/ddi/ntifs/nf-ntifs-ntsetinformationfile): user-mode Nt entry, FileRenameInformation 10 and FileDispositionInformation 13 require DELETE; marked-for-deletion HANDLE is only closed afterward.
- [LookupAccountSidW](https://learn.microsoft.com/en-us/windows/win32/api/winbase/nf-winbase-lookupaccountsidw): bounded name/domain sizing and actual SID_NAME_USE. No inferred SID type or undocumented success-count requirement.
- [msvcrt](https://docs.python.org/3.12/library/msvcrt.html): get_osfhandle borrows the CRT descriptor handle; the original Python stream retains ownership.
