# Windows host-change WFP subset (software implementation)

The supported resource plan is a persistent WFP provider, its persistent
sublayer, and optionally one persistent outbound IPv4 `ALE_AUTH_CONNECT`
PERMIT filter bound to one exact executable app ID, AppContainer package SID,
protocol, and configured local/remote IPv4/port selectors. Provider/sublayer
plans are admitted as one dependency-bound ordered operation. Each resource
has its own durable intent and exact readback event in the existing
`OperationStore`; restore deletes in reverse dependency order and only when the
receipt hash, live row, and complete child enumeration match. Unknown provider
contexts or foreign children preserve the parent resources. Filter-only V1
journaling remains compatible. Unsupported resource kinds are refused before
authorization consumption or native effects.

This is not effective WFP arbitration proof. The observer retains opaque
provider-context rows as unclassified, and unknown callouts, conditions,
layers, arbitration, ACL semantics, or external filters remain
`UNVERIFIED`. No Windows kernel, API mutation, or policy apply/restore was run;
the native platform status is `NOT_RUN`.

The Windows SDK condition identifier for the executable is
`FWPM_CONDITION_ALE_APP_ID`; the AppContainer identity is
`FWPM_CONDITION_ALE_PACKAGE_ID` (`FWP_SID`). The source pins their SDK GUIDs to
`{D78E1E87-8644-4EA5-9437-D809ECEFC971}` and
`{71BC78FA-F17C-4997-A602-6ABB261F351C}`, with literal assertions in the
condition-marshaling test. Microsoft documents the condition
identifiers and their types in [Filtering condition identifiers](https://learn.microsoft.com/en-us/windows/win32/fwp/filtering-condition-identifiers-)
and condition availability at ALE_AUTH_CONNECT in [Filtering conditions available at each filtering layer](https://learn.microsoft.com/en-us/windows/win32/fwp/filtering-conditions-available-at-each-filtering-layer).
Microsoft's [Permitting and blocking applications and users](https://learn.microsoft.com/en-us/windows/win32/fwp/permitting-and-blocking-applications-and-users)
example uses `FwpmGetAppIdFromFileName0` and places its returned byte blob in
`FWPM_CONDITION_ALE_APP_ID`. The SDK GUID literals are kept in
`comsol_mcp/_windows_t037_wfp.py` and the app-ID GUID has a literal regression
assertion in `tests/test_windows_host_change.py`.

The adapter performs bounded read-only dependency preflight, transactional
no-clobber adds, exact in-transaction and fresh-session readback, and
drift-protected deletes. Restore first requires full provider, sublayer,
filter, and provider-context enumeration. WFP allocations are released through
their owning API; `LocalFree` is bound through Kernel32. Filter semantic
readback binds the configured application-ID blob and package SID and compares
exact condition cardinality, values, and equality operators.

Microsoft documents provider/sublayer creation and deletion through
[`FwpmProviderAdd0`](https://learn.microsoft.com/en-us/windows/win32/api/fwpmu/nf-fwpmu-fwpmprovideradd0),
[`FwpmSubLayerAdd0`](https://learn.microsoft.com/en-us/windows/win32/api/fwpmu/nf-fwpmu-fwpmsublayeradd0),
and the [provider uninstall order](https://learn.microsoft.com/en-us/windows/win32/fwp/uninstalling-a-provider).
Microsoft documents [`LocalFree`](https://learn.microsoft.com/en-us/windows/win32/api/winbase/nf-winbase-localfree)
as a Kernel32 API. Software fixtures test the transaction protocol and
readback comparisons only; they do not establish native execution or policy
effectiveness. Failures after a committed intent remain
`RECOVERY_REQUIRED` / `UNKNOWN`; automatic replay is forbidden.
