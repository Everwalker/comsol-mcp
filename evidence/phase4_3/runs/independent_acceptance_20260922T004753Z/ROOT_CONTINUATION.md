# Active acceptance continuation (2026-09-22)

Goal is ACTIVE, not complete/blocked. User authorizes cleaning existing Server and old repair residues; preserve all evidence/history. Work only this recovered repository and generated evidence. No global config/license/firewall changes. No commit/push yet; branch acceptance/independent-20260922, HEAD e14c946616f1..., PIN 2cb4627924d1a3240818ea7cd00453d4bd2d2da8.

Fresh Python: evidence/phase4_3/runs/software_validation_20260922T004801Z/software_venv/bin/python (3.14.7). Do NOT use old .venv. Baseline 1648 passed/1 skipped. Root commands live via tools/g3_3_protocol_acceptance.py and require escalation; it starts/cleans owned COMSOL Server/Worker, preserves source snapshots, checks quiescence before close. Never concurrent engine runs on same Server.

## Current agents
- acceptance_coverage_review: core numeric _g3_results, bindings, measure, complex, tests. C13 budget integrated, native explicit outer getters fixed, strict Dataset binding all types fixed. Latest blocking numeric defect: pending_field_selectors selects old mean FieldArray after std computation, overwrites transformed std with mean; root identified and sent precise fix. Legacy test_g3_results 52 regressions now 113 passed, fresh venv. Next required: axisymmetric_applied_count still boolean-derived; replace with actual setting readback evidence (intvolume/intsurface), fail closed on missing proof. Root has requested this. Also targeted w17/wiring tests and later full suite.
- fresh_software_validation: artifact store/export/helper/catalog. Must return readiness for fixes after public_export_0421Z: expected_sha256/expected_chunk_sha256 supported internally but missing public catalog; pure unknown-format needs PreWriteRefusal; UNKNOWN needs model_inspect(refresh=True) after original job quiescence. Public chunk failed only schema mismatch initially. Root found unsafe opt-in timeout no-publish assertion checks response path, not requested path; disable/correct, never call timeout a no-publish proof. Budget live rejection test design pending. New constraints-macos-arm64-py314.txt and docs/handoff_g3_3/PY314_INSTALL.md delivered. Their automated AST audit was rejected as semantic review, relabeled AUTOMATED_INVENTORY_ONLY; true tests/tools semantic review incomplete.
- restore_audit: typed nodes/probes/helper, production semantic review. C14 29 actual PASS after public study.run (genResult alone leaves table empty), sentinel preserved and cascade table cleanup correct. First 10 additional production modules substantively reviewed; next batch requested. Do not treat AST/keywords as semantic review.

## Fresh live evidence highlights (all under evidence/phase4_3/runs)
- public_control_20260922T0334Z: C15 subset 5PASS, real ACTIVE controls, quiescent terminal and same-key no replay, no source drift.
- public_m1_20260922T0359Z: M1/C04 actual PASS, no source drift.
- public_cutplane_20260922T0405Z: C11 CutPlane 7PASS, native create/readback/binding/area/cleanup, no drift.
- public_probe_20260922T0424Z: C14 29PASS, 2 scope NOT_RUN (C11 excluded/steady time axis absent), actual table T=323.15 after public study.run, no drift.
- public_numeric_20260922T0407Z: C04/C05 (0/1/2/3D, body/lineg)/C06/C07/C09 actual PASS; C10 units valid plus negatives; C08 point axes 4outer*5inner valid but aggregate used outer1.
- native_outer_aggregate_20260922T0415Z: native proves getReal(false,outer), getImag(outer), isComplex(outer) correct, default getters stay outer1. Explicit selection.all and copied native matrices. Earlier0410 compile signature failure;0412 empty-selection zero not valid evidence.
- public_numeric_20260922T0426Z: average outer fixed; std failed because getReal returns all5inner despite solnum; now label-based selection fixed.
- public_numeric_20260922T0431Z: isolation socket proof failure BEFORE numeric. No numeric evidence. Driver now saves sanitized ProbeFailure.proof for future failures.
- public_numeric_20260922T0434Z: prior numeric cases pass; std returns MEAN due old FieldArray selector overwrite identified above. Isolated failure, protected evidence. Source drift _dataset_binding.
- public_export_20260922T0421Z: actual JSON/CSV evaluation/export passes (JSON262627bytes), 10PASS but 3FAIL1BLOCKED; chunk public schema expected_sha256 missing, unknownformat misclassified and subsequent revision conflict. Not accepted.

## Root-owned changes since prior handoff
- Added --probe-only (include_c11=False), current helper records C11 NOT_RUN.
- numeric helper now records successful case checkpoints as assertions.json; after UNKNOWN queries original job_status/job_result/job_reconcile, requires quiescent, then public model_inspect(refresh=True), dirty=False, adopts revision before continuation.
- Added native outer getter probe into --axis-probe-only --outer-probe --chains b.
- _domain_outcome recognizes modelNode() / modelNode(String) pure getters after local javap proof; 26 tests passed. Javap/log in this root run.
- .gitignore protects run private/, control-private/, software_venv/, wheel_venv/, java_classes/, javac_classes/, release/.
- Saved development observation ledgers, overall_acceptance remains false.

## Remaining
1. Finish numeric stale FieldArray fix, real axisym evidence, full fresh numeric incl C08 all/first/last/subset/invalid.
2. Full C11 nodes/Join/mid-property failure plus C14; current root run public_nodes_20260922T0437Z launched (exec session22117 when note written), poll before next engine run.
3. Export public schema/helper fixes, real chunk/hash/memory/budget refusal; clarify CONTROL fault cases versus actual engine.
4. M3 all3 saved-file fresh-worker reopen plus actual negative controls/stale ref and no-replay. Earlier partial chainC not final acceptance (cleanup issue fixed).
5. Final source freeze, G2/G3 affected regressions, full software, rebuilt wheel outside source, py314 lock validation, true per-module semantic audit. Root reviewed first new production entries and they have substantive findings.
6. Final ledgers/docs/case mapping, all-file/hash manifests/public scrub, source workpack ZIP, commit/nonforce sync with SHA. Protect original phase4_1/phase4_2/w17 600files exact against fresh network PIN. No W18.

## Later update
- public_nodes_20260922T0437Z finished FAIL (48PASS7FAIL). Join helper points arrays violate public object schema; typed wrongcomp/cycle/unknownproperty pure preflight still UNKNOWN; actual midproperty stop is correct but native string numeric readback helper miscompares; native cascade dataset cleanup needs inventory. restore_audit assigned these fixes before further audit.
- C08 stale FieldArray fix delivered core; targeted2passed. Root added C09 raw Interp preserve/real/imag/abs/phase 3point oracle with FieldArray output is_complex flag assertion. Core asked to distinguish output flag from engine flag and deliver before nextnumeric.
- Export minimal public-schema/prevalidation/helper fix delivered agent with28tests. Root currently runs public_export_20260922T0441Z, exec session34144; check completion before another engine. Raw timeout opt-in no-publish false proof disabled.
- C13 budget live refusal design requested from artifact agent; do not blindly evaluate huge mesh.
- Core asked to fix all affected test_g3_w17/wiring fake API regressions, not label them out-of-scope. Axisym bool-derived counter remains required repair; ask readiness.
- Evidence attempts must get unique run/subdirectory; no overwrite of prior manifests/logs.

## Latest checkpoint (after numeric0445/export0450)
- public_numeric_20260922T0445Z exit0: all numerical assertions C04-C10 including raw complex modes, actual axisym property proof, 2x4x5x3 C08 and per-axis statistics/selectors. Classified OBSERVATIONS_ONLY_SOURCE_CHANGED because _dataset_binding/tests/helper changed during run. C10 still PARTIAL label pending separately recorded CONTROL nonfinite/swapped readback tests. Final freeze rerun required.
- public_export_20260922T0441Z 14PASS1BLOCKED: all chunk/hash/atomic/preflight normal cases pass; real native invalid expression produced correct propagated UNKNOWN and no destination. Helper wrongly insisted nonUNKNOWN. Corrected strict branch now accepts original UNKNOWN only with actual evaluation method witness, quiescent originaljob, model clean/adopt, specified destination absent and complete no-new-artifact inventory.
- public_export_20260922T0450Z still BLOCKED solely because inventory scanned entire project root (10000entry limit hit). Agent tasked restrict inventory to actual artifact subtree g2_artifacts/results and explicit destination parent, not git/venv. Other public export cases 14PASS0FAIL. All runs cleaned.
- Core unsafe requested_solution-only binding fallback was rejected by root: solution tag membership is NOT dataset binding proof. Core instructed remove fallback and update old fakes to publish real solution property. Do not regress to fallback for green unit tests.
- Core has implemented actual native axisym property evidence and output FieldArray complex flag; helper asserts both. Native C07/rawC09 passed0445.
- C11 typed/helper fixes delivered14tests: points objects, PreWriteRefusal zero-write errors, native component.tags guard, numeric string readback, dataset cascade cleanup inventory. Added real CutLine average323.15 oracle. Root currently running public_nodes_20260922T0453Z (exec session47299); poll before nextengine.
- No active other engine sessions. All agents continue their existing bounded tasks. No final M3/M4/commit/push yet.

## Root continuation after M3_0503
- public_nodes_20260922T0453Z:58PASS3FAIL (Join missing dataset response field; partial setter UNKNOWN helper rejected despite correct partial state; derived rollup). restore agent fixed helper with11tests, core added actual binding metadata then root requested native Interp data getString readback (pending).
- public_m3_20260922T0500Z stopped at idempotency assertion because sanitized transcript original compared raw retry. Root fixed idempotency_check signature to receive original raw data; same job/operation/events checks retained.
- public_m3_20260922T0503Z exit0: A/B/C samehash freshworker directread PASS, seven negatives PASS, stale ref PASS, real fault UNKNOWN/reconciliation/samekey no replay PASS. Only source drift tests/test_g3_w17.py => observations-only, final frozen rerun needed. No root active engine sessions now.
- root audited checker and driver core in audit/root_reopen_driver_review.json; affected_regression_matrix.json defines required finaltests/live impact.
- G3_3_PLAN misleading 18/18PASS rewritten to current in-progress plan; prior exactbytes copied to rootrun/prior_claims/G3_3_PLAN_<sha>.md. Top PROGRESS correction already supersedes historical text.
- .gitignore now also excludes disposable runs/*/source_stage/ duplicate package sources; no files removed.
- restore agent tasked finish all unreviewed production inclJava semantic audit; artifact agent owns tests/tools semantic and bounded C13 live budget/helper+memory record, then final fullsuite/wheel once numericfreeze ready.

## Latest numeric/nodes/export and review followups
- Core at_points now enforces native feature.getString(data)==requested dataset; returns feature_readback plus resolved binding provenance; ignored setter negative added, all208relatedtests pass. Production comment stalefallback corrected byroot. Core frozen.
- public_nodes_20260922T0510Z exit0 61PASS0FAIL2NOTRUN (CutPlane separatelypassed, nonempty transient history pending). Source drift exporthelper/tests so observations-only.
- Root tasked restore agent implement separate run_transient_probe_case (constant2 onChainB,>=3native times,tablevalues/cleanup), productionsemantic audit includingJava remainsits task. Root will adddriver --probe-transient-only whenready.
- Rootdriver --export-transient-budget added: --export-only using chainB same mesh15savedtimes range(0,.1,1.4). First21times exported4.67MB>helper4MiB => metadata FAIL in public_export_0516; preserved.
- public_export_20260922T0519Z exit0 no source drift:19PASS0FAIL1NOTRUN(livecleanupfault noinjector). Budget29expr*15inner*2305points=1,002,675,8,021,400theoreticalnumericbytes >1melements; native getCoordinatesShape proof, no rawgetData, UNKNOWN preserved+quiescentrefreshclean, destinationabsent/fullartifactinventorynonew. Driver RSS~89.5MB highwater recorded asdriver-only,engineinternalcacheUNMEASURED.
- Root found final artifact_read TOCTOU: separatewholehashopen vschunkopen can return oldSHAwithnewbytes same-size swap. Artifact agent assigned samefdhash+seek/fstatstableidentity+O_NOFOLLOW and race regression. Export overwriteFalse should atomicnoclobber protect concurrentnewtarget. Afterfix finalfullsuite/wheel.
- Coreagent now assigned semantic tests/test_g3*.py unreviewed; artifactagent othertests/tools; restore allproductioninclJava. No repeatedASTassemantic.
- git fetch origin main fresh succeeded; HEAD...origin/main left/right=20/0; final ordinarynonforcepush possible afterdelivery. No commits/push yet.
- All rootexecsessions complete; no activeengine at this checkpoint.

## After automatic continuation / transient0525
- All3agents were interrupted by turnaborted; explicitly resumed viafollowup. No rootlive sessions now.
- driver --probe-transient-only wired to run_transient_probe_case on freshChainB. public_transient_probe_0525=22PASS2FAIL2NOTRUN: GlobalVariable root probe create/genResult worked, actualstudy.run failed nativeNPE ProbeFeature.getParentModel null. Preservedlogs, quiescent/reconciled/cleaned.
- Root read comsol-local-kb skill and officialProgrammingReferenceManual p181/182; model.probe(tag).model(ctag) sets component evenGlobalVariable. Reference metadata/no corpus copied rootrun/probe_api_reference.json; documentSHA5b7f23ad...bbc105. Root sentrestore agent repair GlobalVariable component rejection inprobe_create; allow explicitcomp, bind+readback, missingcomp only uniqueactualcomponent defaultrecorded orrefuseambiguous. Keeptransientglobalconstant2case notdowngradeDomain.
- TableBaseFeature javap rootrun/table_base_feature_api.javap.txt has no timegetters. Time metadata needs actualtableheader/column nativeproof, notfakegetTimeValues.
- Root found _g3_w16._completion/describe_engine_failure flatten innernativeunknown toknownpartial iftimestampreadable. Coreagent nowowns bounded fix structuredunknownpropagation+tests, plusdomain effectgetter classification (Study lastcomputationgetter, model0read/1setter). Sourcecurrentlynotfrozen.
- Rootadded --study-fault-only driver: deliberateinvalidnativeunboundGlobalVariable via publictrustedfixture, thenpublicstudy.run must UNKNOWN, originaljobquiescence/samekeyjob+op/logeventidentity + cleanrefresh. Runaftercorefix.
- Corealsoasked strengthen g33_control_cases samekey: existinghelper onlycountedowncalls/hash, must actualsamejob/op+publicjoblog before/after unchanged; testsupdate. Semantic test_g3*.py ongoing.
- Artifactagent repairedsamefdhash/read+fstat/devino/ctime/mtime/pathidentity andatomicno-clobberos.link. Rootreviewedimplementationcorrect. New55targetedPASS inartifact_immutable_20260922T051143Z. Fullsuite firstsandbox attemptinterrupted532PASS2FAIL4ERROR localhost/Java EPERM+GUARD_T038; NOTfullPASS. Root tasked rerunentirefullpytest require_escalated (initialbaselineoutside1648/1), keepfulllogsnotjustsummary. Noactiveagenttestsessions atitslastreport. Finalwheel/semanticothertests/tools stillpending.
- Rootupdatedfindings withtransientprobe/docsrootcause andartifactrace. Needfinalrefreshplan/docs/ledgers onlyafterfrozenvalidation, noW18.
