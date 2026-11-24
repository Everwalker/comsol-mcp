# R18 interim offline candidate review

The R18 application wheel incorporates the scoped T13 source-probe exception sanitization, T18 diagnostics, and T26 component-free global-evaluation changes. Independent review matched 213 source inputs, 210 application files across source/wheel/installed macOS ARM environment, and 41 locked dependencies. Offline install, pip check, and import-only receipts passed on macOS ARM.

Independent archive reviews accepted the Windows x64 and macOS Intel interim kits for CRC, member identities, locks and byte continuity of the 34 and 41 dependencies, respectively. Both kits contain the same R18 application wheel.

| Artifact | SHA-256 |
| --- | --- |
| Application wheel | `2bfe29ddf5e31da6d5fbfe1d117b84d566e8f3542f28fd65d41f8d2a0ef2e825` |
| macOS ARM lock | `f018c66bdb743bd619255519e9e36a2adef1fdbd70aebd4a15e54821790011af` |
| Windows x64 kit | `6883733bafb8466e2e67d5cb1dc85ce9b0a6350c4916b7d9fff2e7f5d03d6b84` |
| macOS Intel kit | `a474cf7688072a86ba837327537de2b1ab238954985e96655f7ec58301e5aa36` |
| macOS ARM independent review | `4046cf1a79dfa0ea713b47a2834180c553496d64fd6a24da37639405df410a1f` |
| Windows/Intel independent review | `ca7e23fcf9b4c8a8874fca2c4f9d18f2441d0e51ae006952d72dd443b3fcc9c3` |

These artifacts are interim offline candidates, not releases. Windows and Intel installation, live MCP, COMSOL/Worker/Java behavior, licenses, complete release notices, and native acceptance are unverified. R18 predates later documentation and unreviewed fixes; the original task remains incomplete.
