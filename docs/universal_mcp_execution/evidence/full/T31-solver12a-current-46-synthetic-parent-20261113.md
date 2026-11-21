# T31 current solver 12a: 46 selected synthetic-ID regression

**Later incident qualification:** the 11:26–11:27 CST runs below occurred after a separate T20 review's lifecycle-guard refusal, then unknown to the parent. The wrapper's additional live T20 guard SHA checks overlap part of the refused identity outcome and are **not accepted** as verification of that guard's current identity or correspondence to its saved candidate. The 46 recorded synthetic test results are retained; do not rerun the refused guard verification or infer a fixed guard pin from it. See `ENV-t20-qwen-review-lifecycle-guard-incident-20261113.md`. The original description below is retained as a historical account of what the wrapper did, not an accepted guard-identity conclusion.

Status: **SCOPED SYNTHETIC REGRESSION, NOT T31/U07 OR FINAL ACCEPTANCE.** The current T19 prerequisite, real scheduling and authorization, native COMSOL, actual MCP wire and restart/crash-durability evidence remain open. All database connections in the selected cases were new guarded `:memory:` SQLite connections; they are not evidence about persisted production state, OS-level isolation, real principals, workers, or COMSOL. Python hooks are not an OS sandbox.

## Candidate, provenance, and precautions

The prior 46-P set on solver `66182697252f031dd97ad96a3f07d7a060661f69c60b923abf4fd374506b3f1a` was independently rerun against solver `12a205f5c2fa54c7f19a49ab0769e7ea0c98e4386ca551598415c4f5683b8b8c`; this is new execution, not transferred evidence. The twelve inner before/after pin maps are unchanged except for the solver when compared to their respective old receipts. **This statement concerns only files pinned by those receipts**: the T20 guard was not included in the old inner pin sets, so its prior value cannot be inferred. The new wrapper verifies the current T20 guard `52043779f665ce1fc10bd5feeba4cc018df8a39686cb1d04a21e50627f6f4e8a` only at the outer pre/post boundaries. Other relevant pins: daemon `80d40e42dcb06a9f2001a1a0de8bea46fac16ea0be8148736a93b0de6acd4c3d`, store `9872e35de66ebb492c887b857fffdd0e8a6156a3d573dec033c57ae538cedbf6`, parser `495baac50eb5985832fefd6381e1c630af42c821cc033a0512a2693052f71562`.

Qwen `deleg_24b55b18` wrote the preparation-only wrapper `_state/t31-stage-queue-lifecycle-20261112/run_t31_combination_solver12a_parent_20261113.py`, SHA `267658ea166f0962ec8e58babb43f50a2a51559222a1c04dcde1147341f5bc2c`, without running tests. It loads the unchanged earlier wrapper `b36897f60ec817a7892f622c58d28e733e118ef92b016e36b02ff0ee9ebf94ef`, checks its previous solver constant, replaces it in that loaded function's globals and invokes its original `main`. The parent inspected the child-visible trace and complete wrapper before use; the loaded wrapper retains its original 12 selection tables and inner guard chain. Child's visible trace includes a `read_file` existence probe of its target beyond the two specifically authorized source/evidence reads; it returned not-found without content, followed by one successful new-file write. It is an out-of-read-list metadata probe, not a guard refusal, and is not evidence of access to or modification of an old model. The host-reported requested model was `Qwen3.8-Flash-Next-GSQ-RCO-IQ3_S`; served identity and reasoning effort are not independently attested.

The parent made 12 new private scratch/home/pycache paths and case directories under fresh label `t31g2`; all 24 target paths were checked for prior existence before launch, and the batch was set to stop on first nonzero outcome. Each run used the pinned private venv, `env -i`, `-I -B -X pycache_prefix`, disabled pytest plugin autoload and the existing Python hooks. The launcher exited zero for all 12 groups; per-group full stdout is under `/Users/everwalker/.hermes/cache/scratch/t31-cand6cur-{e,p}-t31g2-<mode>/launcher.log`. No guard refusal occurred. This is not a rerun of the five fenced `test_execution_contract.py` outcomes or the other fenced T13/T14/T19 results; the selection preflight limits and unresolved transitive-analysis caveat are documented in `T31-solver12a-preflight-parent-20261113.md`.

## Actual receipts

Each receipt is `_state/t31-stage-queue-lifecycle-20261112/t31g2-current<group>-<mode>-case/green.log`. The table hashes are SHA-256 of its complete bytes.

| Group | Mode | Distinct passed | In-memory connections | Receipt SHA-256 |
|---|---|---:|---:|---|
| existing | define | 5 | 5 | `c82074119334b8bc335bfe51ad6b664807f960697fc874079b8809a02bdd9979` |
| existing | registration | 2 | 2 | `c840bef9d6bc785b768369f180d20b62931d2df63b9536f29691b9b3028620d9` |
| existing | nested | 3 | 3 | `75b77af7f687dfe9b2c925f424adaad3dcc9ad8b19f719de22b006573e8cac2e` |
| existing | missing | 2 | 2 | `7c35c006704fbb8eb1a9f07f2498e472cfec5770f80661729a877d36ffbbc187` |
| existing | matched | 3 | 3 | `5d5b5f7b858d967c5ed7852bdadb58cadb5c31bf3211626c73dc4c43878e0a08` |
| existing | readfault | 4 | 4 | `2926695c74699f8d481009a25948f7ee03119dc63e1a60e4a17c816e38ff2a99` |
| parent | faults | 2 | 2 | `4fa0242c82585c0665b3c41ca94332cb2c2c2cb8a40b1a34468a7dc98ae163f8` |
| parent | owner | 3 | 4 | `e190ef8bc032aa1c299b6aea5f57ce19825e1753335716c156ffaf68fdd8c395` |
| parent | replay | 3 | 3 | `b319e74ee142ba92378d986a88894aa0c9bc7975da3ae927787784ae4afff773` |
| parent | edges | 10 | 10 | `a01ea98a109dc4157774d2d97cd61ea46cb195c838f6f1e317f5d4c5609e46b2` |
| parent | cancel | 5 | 5 | `a9be98ca83bcf3e8118ff20b9c11d7e981f64358af028bcffb7608ffc201717a` |
| parent | timeout | 4 | 4 | `87c54a23b8e20da5b8dcbf25569c98887b09e6d352d87e0b87c681cabefe1675` |
| **Total** | | **46** | **47** | |

Parent parsed all 24 old/new receipts, compared group-selected ID sets and call IDs, and deduplicated them programmatically: exactly the same **46 distinct IDs**, 46 passes, zero skips, zero `forbidden_calls`, and 47 in-memory connections in the new runs. Every new inner pre/post input identity map matched; old vs new maps differed only in the solver pin. A separate check validated each old and new solver digest and the exact current outer wrapper pre/post markers. These receipt and hook checks do not prove that unspecified transitive paths or OS operations cannot happen.

T10's boolean schema-version bug remains open (`T10-schema-only-current-combination-red-parent-20261113.md`), and T35 typed-identity regression evidence remains synthetic; a future product change changes the tested combination and requires impact-based reassessment. The provisional T42 wheel is still not an installed or native-verified release. No broad claim of product or project acceptance follows from this result.
