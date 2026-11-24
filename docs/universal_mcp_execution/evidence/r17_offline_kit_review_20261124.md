# R17 Windows and Intel macOS offline kit review

Date: 2026-11-24  
Verdict: scoped accept for offline package identity and dependency locks only.

An independent reviewer checked ZIP member identities and CRCs, wheel metadata, lock hashes and byte continuity against R16. The Windows kit has 34 dependency wheels and 37 ZIP members; the Intel macOS kit has 41 dependency wheels and 44 ZIP members. All dependency wheels matched their R16 counterparts, and both kits contain the same R17 application wheel.

| Artifact | SHA-256 |
| --- | --- |
| Windows x64 kit | `70314afe80d44ce11ad7e150cc36605f717b6f6eab807a498f0dc4334c31c003` |
| macOS x86_64 kit | `a7bc62d6a4b3218272a8a2165225e65d4292523d0e73083c5d3dcba26d2feb39` |
| Application wheel | `efbc8d0a0b1b18562e7fc5046d3cf94283fb1ba1fde0bbf04795c255a9b7a5a3` |
| Windows lock | `eacca58fae1bb6b6e076be005702b006f1af280147c95c8551c14ed5ad053e8b` |
| Intel macOS lock | `5eeb6795102c838e0ee08ebe8064f5c797c4cae4233bb35567f17c8a9ece95fe` |
| Independent local review report | `eea5d049fec0a6a43a4de390718e13835d6882c79f5495eb9682d9dd376e22bd` |

These are interim offline kits, not release artifacts. This review did not install either kit or verify native COMSOL/Worker behavior, licensing or complete release notices. The reviewer read the complete task index beyond the delegated task metadata scope, but did not open separately fenced evidence or access unknown sessions or processes.
