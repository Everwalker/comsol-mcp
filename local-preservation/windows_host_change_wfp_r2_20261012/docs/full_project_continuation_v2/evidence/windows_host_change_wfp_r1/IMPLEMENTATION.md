# Windows host-change WFP subset (software implementation)

This unit implements one bounded operation: create one persistent outbound IPv4
`ALE_AUTH_CONNECT` permit filter bound to one exact executable app ID, its
AppContainer package SID, protocol, and configured local/remote IPv4/port
selectors; restore deletes only that exact filter when the receipt hash and
live row still match. The API reuses an existing provider and sublayer. It does
not create/delete provider or sublayer objects, install callouts, configure
SCM/service identities, or evaluate effective WFP arbitration.

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

The adapter performs bounded read-only dependency preflight, a transactional
no-clobber add, exact in-transaction and fresh-session readback, and drift-
protected delete. It records a durable intent before either native mutation.
Failures after intent remain `RECOVERY_REQUIRED` / `UNKNOWN`; automatic replay
is forbidden. These software tests exercise the protocol and ABI marshaling
fixtures only. No Windows kernel/API mutation was run. A successful filter
readback is not proof that the filter is effective or that T037 isolation is
complete: WFP arbitration, third-party callouts and complete policy proof
remain `UNVERIFIED`.
