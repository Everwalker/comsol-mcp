# W22 archive independent read-only review 001

**Result:** PASS for archive bytes and clean-extraction consistency. This review does not establish current native replay or scientific acceptance.

## Frozen evidence reviewed

- Plan: `/Volumes/SSD/Comsol-MCP/execution-scratch/replay-delivery-gap-inventory-20261004-001/W22_BOUNDED_ARCHIVE_PLAN001.json` — SHA-256 `d7002d61c0f92a44e9898efc8f5b20cb2bc90d979d232ab9f0d6944618e2ef46`.
- Archive: `/Volumes/SSD/Comsol-MCP/COMSOL_MCP_FULL_PROJECT_WORKPACK/repository/docs/full_project_execution/release/w22_scientific_replay/W22_WINDOWS_NATIVE_REPLAY001.zip` — 78,370,539 bytes; SHA-256 `41b9229d71f3b5d6baa20b59557bf8ef7d6503a1a295344c33df5038c878a7ce`.
- Handoff: `/Volumes/SSD/Comsol-MCP/execution-scratch/replay-delivery-gap-inventory-20261004-001/W22_ARCHIVE_FINAL_HANDOFF001.md` — SHA-256 `7fccaeb23149d9c75d09df9ae034fd0a134d60c5d32f1a89699dd155a6e3e422`.
- Build receipt: `/Volumes/SSD/Comsol-MCP/execution-scratch/replay-delivery-gap-inventory-20261004-001/W22_ARCHIVE_BUILD_RECEIPT004.json` — SHA-256 `e72eed881de2b612bccd3ae6d9f0769c986a897fc49d212097488ec4c0adde4c`.
- Extraction verification and per-sidecar rows: `/Volumes/SSD/Comsol-MCP/execution-scratch/replay-delivery-gap-inventory-20261004-001/W22_ARCHIVE_EXTRACTION_VERIFY004.json` — SHA-256 `b0b7e5d9e0371567e69a38f6a645a6793f2de1defa58f3597a4ec9c352c3ae08`.
- Failure denominator: `/Volumes/SSD/Comsol-MCP/execution-scratch/replay-delivery-gap-inventory-20261004-001/W22_ARCHIVE_FAILURE_DENOMINATOR004.json` — SHA-256 `295b195ff4b6787a9038632f35070ce741448eb9b99a391dd5f8eb48d104839a`.
- Root archive readback: `/Volumes/SSD/Comsol-MCP/execution-scratch/replay-delivery-gap-inventory-20261004-001/ROOT_W22_ARCHIVE_BYTES_READBACK001.json` — SHA-256 `ee50e0054eadbcefa64868e3e08acba0f5ba5d18a6cc3eb3db07240b7326bc40` (root byte/CRC/path checks recorded PASS).
- Root historical-object readback: `/Volumes/SSD/Comsol-MCP/execution-scratch/replay-delivery-gap-inventory-20261004-001/ROOT_HISTORICAL_SOURCE_OBJECT_READBACK001.json` — SHA-256 `998a210e68534f5681290bec3709fe908fbccdddfe2c83c13121d8d4afe86a6e`.

## Independent checks

- ZIP CRC test passed. The archive has exactly 422 unique safe relative paths, all 422 regular-file entries: 420 frozen payload members plus `README.md` and `MEMBER_INDEX.json`. Its member names exactly match the frozen plan; no unsafe paths, symlinks, nonregular entries, or other members were found.
- For all 422 members, streamed ZIP content SHA-256 and uncompressed size match `W22_clean_extraction003`. Its regular-file and directory sets match the archive exactly, with no extra files, directories, or symlinks.
- All 420 payload files (180,605,833 source bytes) match the frozen plan’s path, size, and SHA-256 in the current repository and in the clean extraction; source-to-extraction byte comparisons also passed. The 420 build-receipt before/after refs are identical, and current device/inode/size values match the recorded after refs.
- Generated root members match their recorded and extracted values: `README.md` 1,665 bytes, SHA-256 `461e9e98e8fe6cfbdf875ef62074dcfc1bb429bf9d69dfca3f90b6f02ecdfe5e`; `MEMBER_INDEX.json` 103,705 bytes, SHA-256 `1420c0a3af1fa1d130cd61ffc291ba23cdfd5be2343a1fec18cf3ab067ffa19c`.
- The clean-extraction filesystem contains 480 paired AppleDouble sidecars (1,966,080 bytes total): 422 paired files and 58 paired directories. Every recorded sidecar was present and independently matched its recorded SHA-256 and size; actual magic/version were `00051607` / `00020000`, and each recorded file/directory pair exists. All 480 extraction-verification rows report valid headers and entries. The sidecars remain intact and are not ZIP members.

## Preserved failures and acceptance limits

The three preserved verifier/build-check failures are not scientific failures and were not suppressed: attempt 1 checked the final path before `.partial001` was renamed (failure receipt SHA-256 `6a9d85fd1fe262cfd06ddcb8f4508638453cb4dfee63ac555d4ef23db485543a`; retained partial ZIP SHA-256 `a3c4b00d7d77eff2803d36b52e60f9373bea18276c4260cff2cf298638054bfb`); attempt 2 applied an out-of-scope chmod assertion to generated `MEMBER_INDEX.json` (receipt SHA-256 `b312e8d797750002d5c22e16fd2414bbcb362396b6511477ba95e3caf5bbb0b8`); attempt 3 counted 480 filesystem AppleDouble sidecars as extra files after all 422 archive members passed content checks (receipt SHA-256 `8aa8fde8999108d5d2c3508bc4a3577dbcc5e53dbf9a15213ce146535fb2efba`). The denominator records all three and the final successful readback.

The documented full source tar is missing, and the local readback reports historical commit `44b8e1931b47da564edb600d2127b28859d07b36` unavailable in this repository. The frozen plan records the current shared `_vcsel_source.py` reference as 9,535 bytes with SHA-256 `7a4a79426cd14c426099f07535b70566cdd1c16bbf2a4b9261814610b514d963`; equivalence to that historical commit remains **UNVERIFIED**. Historical scope remains the Windows COMSOL 6.3 build 290 and 6.4 build 293 cases. No current native/scientific replay was run; full release acceptance and measured-device calibration remain open.

Review activity was read-only against the archive and source evidence. This Markdown is the intentional review output under `independent-w22-archive-review001/`; filesystem-generated metadata beside it was preserved.
