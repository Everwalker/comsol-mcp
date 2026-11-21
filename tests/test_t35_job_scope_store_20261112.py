"""T35 — synthetic contract suite for project-scoped OperationStore reads.

Authored 2026-11-12 and deliberately NOT executed by the authoring child; the
parent agent binds source identity, runs this suite, and integrates the
daemon side.  Every control below exercises the real ``OperationStore``
methods (``project_job_read_snapshot``, ``list_jobs`` with
``enforce_project_binding``, ``job``/``events``/``begin``/``add_event``)
against a fresh in-memory store with synthetic rows only.  There are no real
projects, databases, COMSOL, Worker, SSH, network, credentials, historical
jobs, or UNKNOWN state involved, and no global monkeypatching.

Like the frozen T35 job-read parent suite, this file imports no test
framework: plain top-level ``test_*`` functions with explicit store
construction/closing and explicit expectation helpers, so any runner
(pytest-collected or plain callable) can drive it.

Honesty limits (mirroring the T35 brief):
- ``project_job_read_snapshot`` and strict ``list_jobs`` are store-consistency
  primitives, not authentication.  A same-snapshot read proves coherent
  visibility for one snapshot, not metadata immutability, future revocation
  behaviour, or any principal identity.  In-memory tests prove nothing about
  WAL, inter-process, or crash durability.
- Denial semantics asserted here are metadata-equality semantics via the pure
  ``consistent_project_id`` helper (live, parent-owned; not re-specified in
  detail — in particular whitespace-claim acceptance is NOT asserted either
  way).  Only exact project-ID mismatch negatives are pinned.
- The strict-mode scan cost (O(total job rows) SQLite scan for a same-snapshot
  total) is intentional and asserted only through results, not timing.
"""

from __future__ import annotations

import json
import sqlite3
import sys
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Callable, Iterator

_REPO_ROOT = Path(__file__).resolve().parents[1]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from comsol_mcp._operation_store import JobList, OperationStore

PROJECT_A = "t35jss-proj-alpha-20261112"
PROJECT_B = "t35jss-proj-beta-20261112"


@contextmanager
def _fresh_store() -> Iterator[OperationStore]:
    """One fresh memory store per test, always closed afterwards."""
    created = OperationStore(":memory:")
    try:
        yield created
    finally:
        created.close()


def _expect_call_raises(
    exc_type: type[BaseException], label: str, call: Callable[[], Any]
) -> None:
    """Require a plain call to fail with exactly ``exc_type``."""
    try:
        call()
    except exc_type:
        return
    except BaseException as exc:
        raise AssertionError(
            f"{label}: expected {exc_type.__name__}, got {type(exc).__name__}: {exc}"
        ) from exc
    raise AssertionError(f"{label}: expected {exc_type.__name__}, but the call succeeded")


def _expect_snapshot_entry_raises(
    store: OperationStore,
    job_id: Any,
    project_id: Any,
    exc_type: type[BaseException],
    label: str,
) -> None:
    """Enter the snapshot context like ``with`` and require ``exc_type``.

    If entry unexpectedly succeeds, the context is exited cleanly before the
    assertion failure so an entered lock/transaction cannot deadlock the run.
    """
    context = store.project_job_read_snapshot(job_id, project_id)
    entered = False
    try:
        context.__enter__()
        entered = True
    except exc_type:
        return
    finally:
        if entered:
            context.__exit__(None, None, None)
    raise AssertionError(f"{label}: expected {exc_type.__name__} at context entry")


def _seed_pair(
    store: OperationStore,
    name: str,
    *,
    job_metadata: dict[str, Any] | None = None,
    operation_metadata: dict[str, Any] | None = None,
    job_metadata_raw: str | None = None,
    operation_metadata_raw: str | None = None,
    job_status: str = "QUEUED",
    create_operation: bool = True,
) -> str:
    """Insert one synthetic job/operation pair through raw store SQL.

    Raw (``*_raw``) metadata strings fabricate malformed durable rows; this is
    a fabricated record shape inside a memory database, not a claim that real
    durable metadata is tamperable.
    """
    job_id = f"t35jss-job-{name}"
    operation_id = f"t35jss-op-{name}"
    with store.lock:
        if create_operation:
            store.db.execute(
                "INSERT INTO operations(operation_id,operation,status,metadata) VALUES(?,?,?,?)",
                (
                    operation_id,
                    "study.synthetic-producer",
                    "QUEUED",
                    operation_metadata_raw
                    if operation_metadata_raw is not None
                    else json.dumps(operation_metadata or {}, sort_keys=True),
                ),
            )
        store.db.execute(
            "INSERT INTO jobs(job_id,operation_id,status,metadata) VALUES(?,?,?,?)",
            (
                job_id,
                operation_id,
                job_status,
                job_metadata_raw
                if job_metadata_raw is not None
                else json.dumps(job_metadata or {}, sort_keys=True),
            ),
        )
    return job_id


def _begin_pair(store: OperationStore, tag: str, metadata: dict[str, Any]) -> str:
    """Create a job/operation pair via the real ``begin`` path (same metadata)."""
    record, reused = store.begin(
        request_id=f"t35jss-req-{tag}",
        idempotency_key=f"t35jss-idem-{tag}",
        request_hash="a" * 64,
        operation="study.synthetic-producer",
        metadata=metadata,
    )
    assert reused is False
    job_id = record["job_id"]
    assert isinstance(job_id, str) and job_id
    return job_id


def _seed_catalog(store: OperationStore, *, include_malformed: bool) -> dict[str, str]:
    """Insert one deterministic, interleaved synthetic catalogue.

    Returns the synthetic job IDs keyed by name in insertion order; ``rowid``
    DESC order is therefore exactly this sequence reversed.
    """
    ids: dict[str, str] = {}

    def seed(name: str, **kwargs: Any) -> None:
        ids[name] = _seed_pair(store, name, **kwargs)

    seed("flat-a", job_metadata={"t35": True, "project_id": PROJECT_A},
         operation_metadata={"project_id": PROJECT_A})
    seed("unbound", job_metadata={"t35": True}, operation_metadata={"t35": True})
    seed("nested-a", job_metadata={"execution": {"project_id": PROJECT_A}},
         operation_metadata={"execution": {"project_id": PROJECT_A}}, job_status="SUCCEEDED")
    seed("cross-a-b", job_metadata={"project_id": PROJECT_A},
         operation_metadata={"project_id": PROJECT_B})
    seed("split-a", job_metadata={"project_id": PROJECT_A},
         operation_metadata={"execution": {"project_id": PROJECT_A}}, job_status="RUNNING")
    seed("root-claim-a",
         job_metadata={"project_root": PROJECT_A, "arguments": {"project_id": PROJECT_A}},
         operation_metadata={"project_root": PROJECT_A, "arguments": {"project_id": PROJECT_A}})
    if include_malformed:
        seed("malformed-a", job_metadata_raw="t35jss-not-json{{",
             operation_metadata_raw="t35jss-not-json{{")
    seed("flat-b", job_metadata={"project_id": PROJECT_B},
         operation_metadata={"project_id": PROJECT_B})
    seed("flat-a-done", job_metadata={"project_id": PROJECT_A},
         operation_metadata={"project_id": PROJECT_A}, job_status="SUCCEEDED")
    seed("divergent-a", job_metadata={"project_id": PROJECT_A,
                                      "execution": {"project_id": PROJECT_B}},
         operation_metadata={"project_id": PROJECT_A})
    return ids


# ---------------------------------------------------------------------------
# (A) project_job_read_snapshot — positives
# ---------------------------------------------------------------------------

def test_t35jss_snapshot_flat_bound_pair_yields_true_with_protected_reads() -> None:
    """Flat top-level project_id on both durable rows authorizes and permits
    same-snapshot ``store.job``/``store.events`` reads with the exact events."""
    markers = ("T35JSS-EVT-FLATALPHA-4c17", "T35JSS-EVT-FLATBRAVO-8de2")
    with _fresh_store() as store:
        job_id = _begin_pair(store, "snap-flat", {"t35_synthetic": True, "project_id": PROJECT_A})
        for marker in markers:
            store.add_event(job_id, "T35JSSReadEvent", {"marker": marker})
        with store.project_job_read_snapshot(job_id, PROJECT_A) as bound:
            assert bound is True
            assert store.db.in_transaction is True
            job = store.job(job_id)
            assert job is not None and job["job_id"] == job_id
            assert job["metadata"]["project_id"] == PROJECT_A
            assert [event["metadata"]["marker"] for event in store.events(job_id)] == list(markers)
        assert store.db.in_transaction is False


def test_t35jss_snapshot_nested_execution_binding_yields_true() -> None:
    with _fresh_store() as store:
        job_id = _begin_pair(store, "snap-nested", {"execution": {"project_id": PROJECT_A}})
        with store.project_job_read_snapshot(job_id, PROJECT_A) as bound:
            assert bound is True
            assert store.job(job_id) is not None
        assert store.db.in_transaction is False


def test_t35jss_snapshot_compacted_split_bindings_yield_true() -> None:
    """A job keeping the top-level ID while its operation keeps the nested ID
    (and vice versa) is the compacted shape and must authorize."""
    with _fresh_store() as store:
        forward = _seed_pair(store, "snap-split-fwd",
                             job_metadata={"project_id": PROJECT_A},
                             operation_metadata={"execution": {"project_id": PROJECT_A}})
        mirror = _seed_pair(store, "snap-split-mirror",
                            job_metadata={"execution": {"project_id": PROJECT_A}},
                            operation_metadata={"project_id": PROJECT_A})
        for job_id in (forward, mirror):
            with store.project_job_read_snapshot(job_id, PROJECT_A) as bound:
                assert bound is True


# ---------------------------------------------------------------------------
# (A) project_job_read_snapshot — fail-closed negatives
# ---------------------------------------------------------------------------

def test_t35jss_snapshot_unknown_job_denies_and_closes_transaction() -> None:
    with _fresh_store() as store:
        with store.project_job_read_snapshot("t35jss-job-absent", PROJECT_A) as bound:
            assert bound is False
        assert store.db.in_transaction is False


def test_t35jss_snapshot_job_without_operation_row_denies() -> None:
    """A job whose stored operation_id has no operations row has an absent
    binding and must deny — no guessed ownership."""
    with _fresh_store() as store:
        job_id = _seed_pair(store, "snap-dangling",
                            job_metadata={"project_id": PROJECT_A}, create_operation=False)
        with store.project_job_read_snapshot(job_id, PROJECT_A) as bound:
            assert bound is False


def test_t35jss_snapshot_metadata_without_project_claims_denies() -> None:
    with _fresh_store() as store:
        job_id = _begin_pair(store, "snap-unbound", {"t35_synthetic": True})
        with store.project_job_read_snapshot(job_id, PROJECT_A) as bound:
            assert bound is False
        with store.project_job_read_snapshot(job_id, PROJECT_B) as bound:
            assert bound is False


def test_t35jss_snapshot_cross_project_pair_denies_for_either_project() -> None:
    with _fresh_store() as store:
        job_id = _seed_pair(store, "snap-cross",
                            job_metadata={"project_id": PROJECT_A},
                            operation_metadata={"project_id": PROJECT_B})
        for probe in (PROJECT_A, PROJECT_B):
            with store.project_job_read_snapshot(job_id, probe) as bound:
                assert bound is False


def test_t35jss_snapshot_divergent_claims_within_one_side_denies() -> None:
    """One side independently claiming two different project IDs is ambiguous
    and denies even though the other side and the query agree with one of them."""
    with _fresh_store() as store:
        job_id = _seed_pair(store, "snap-divergent",
                            job_metadata={"project_id": PROJECT_A,
                                          "execution": {"project_id": PROJECT_B}},
                            operation_metadata={"project_id": PROJECT_A})
        for probe in (PROJECT_A, PROJECT_B):
            with store.project_job_read_snapshot(job_id, probe) as bound:
                assert bound is False


def test_t35jss_snapshot_malformed_metadata_json_denies() -> None:
    """An ordinary JSON decode error classifies only that row unbound; the
    store must not raise and must not treat the row as bound."""
    with _fresh_store() as store:
        broken_job = _seed_pair(store, "snap-broken-job",
                                job_metadata_raw="t35jss-not-json{{",
                                operation_metadata={"project_id": PROJECT_A})
        broken_op = _seed_pair(store, "snap-broken-op",
                               job_metadata={"project_id": PROJECT_A},
                               operation_metadata_raw="{oops")
        for job_id in (broken_job, broken_op):
            with store.project_job_read_snapshot(job_id, PROJECT_A) as bound:
                assert bound is False
        # The store stays usable after the decode-error classifications.
        assert store.db.in_transaction is False
        later = _begin_pair(store, "snap-after-broken", {"project_id": PROJECT_A})
        with store.project_job_read_snapshot(later, PROJECT_A) as bound:
            assert bound is True


def test_t35jss_snapshot_non_mapping_metadata_denies() -> None:
    with _fresh_store() as store:
        job_id = _seed_pair(store, "snap-nonmap",
                            job_metadata_raw="[]", operation_metadata_raw='"just-a-string"')
        with store.project_job_read_snapshot(job_id, PROJECT_A) as bound:
            assert bound is False


def test_t35jss_snapshot_project_root_and_arguments_are_not_binding() -> None:
    """A synthetic record forging project_root/arguments.project_id claims
    (never read by the binding helper) must deny despite an exact-name query."""
    with _fresh_store() as store:
        job_id = _seed_pair(
            store, "snap-spoof",
            job_metadata={"project_root": PROJECT_A, "arguments": {"project_id": PROJECT_A}},
            operation_metadata={"project_root": PROJECT_A, "arguments": {"project_id": PROJECT_A}},
        )
        with store.project_job_read_snapshot(job_id, PROJECT_A) as bound:
            assert bound is False


def test_t35jss_snapshot_project_id_is_exact_not_normalized() -> None:
    with _fresh_store() as store:
        job_id = _begin_pair(store, "snap-exact", {"project_id": PROJECT_A})
        for probe in (PROJECT_A.upper(), PROJECT_A + " ", " " + PROJECT_A):
            with store.project_job_read_snapshot(job_id, probe) as bound:
                assert bound is False, f"non-exact probe {probe!r} must not bind"


def test_t35jss_snapshot_rejects_empty_identity_arguments() -> None:
    with _fresh_store() as store:
        job_id = _begin_pair(store, "snap-emptyargs", {"project_id": PROJECT_A})
        _expect_snapshot_entry_raises(store, "", PROJECT_A, ValueError, "empty job_id")
        _expect_snapshot_entry_raises(store, job_id, "", ValueError, "empty project_id")


def test_t35jss_snapshot_caller_exception_rolls_back_and_store_recovers() -> None:
    """A caller raising inside the context concludes the read transaction; the
    connection stays usable and a later snapshot/read still works."""
    with _fresh_store() as store:
        job_id = _begin_pair(store, "snap-exc", {"project_id": PROJECT_A})
        raised: RuntimeError | None = None
        try:
            with store.project_job_read_snapshot(job_id, PROJECT_A) as bound:
                assert bound is True
                raise RuntimeError("synthetic caller failure")
        except RuntimeError as exc:
            raised = exc
        assert raised is not None and "synthetic caller failure" in str(raised)
        assert store.db.in_transaction is False
        store.add_event(job_id, "T35JSSReadEvent", {"marker": "T35JSS-EVT-RECOVERED-9f41"})
        with store.project_job_read_snapshot(job_id, PROJECT_A) as bound:
            assert bound is True
            markers = [event["metadata"]["marker"] for event in store.events(job_id)]
        assert "T35JSS-EVT-RECOVERED-9f41" in markers


def test_t35jss_snapshot_denial_path_leaves_no_open_transaction() -> None:
    with _fresh_store() as store:
        job_id = _seed_pair(store, "snap-denial-txn",
                            job_metadata={"project_id": PROJECT_B},
                            operation_metadata={"project_id": PROJECT_B})
        with store.project_job_read_snapshot(job_id, PROJECT_A) as bound:
            assert bound is False
        assert store.db.in_transaction is False


def test_t35jss_snapshot_operational_db_error_propagates_not_denial() -> None:
    """A closed connection must surface an operational sqlite3 error, never a
    swallowed False that could masquerade as a completed protected read."""
    closed = OperationStore(":memory:")
    try:
        job_id = _begin_pair(closed, "snap-closed", {"project_id": PROJECT_A})
    finally:
        closed.close()
    _expect_snapshot_entry_raises(closed, job_id, PROJECT_A, sqlite3.ProgrammingError,
                                  "closed-store snapshot entry")


# ---------------------------------------------------------------------------
# (B) list_jobs(enforce_project_binding=True) — strict paging
# ---------------------------------------------------------------------------

def test_t35jss_strict_list_totals_order_pages_and_reachability() -> None:
    with _fresh_store() as store:
        ids = _seed_catalog(store, include_malformed=True)
        expected_desc = [ids["flat-a-done"], ids["split-a"], ids["nested-a"], ids["flat-a"]]
        page1 = store.list_jobs(offset=0, limit=2, project_id=PROJECT_A, enforce_project_binding=True)
        assert isinstance(page1, JobList)
        assert page1.total == 4 and page1.offset == 0 and page1.limit == 2
        assert page1.has_more is True
        assert [row["job_id"] for row in page1] == expected_desc[:2]
        page2 = store.list_jobs(offset=2, limit=2, project_id=PROJECT_A, enforce_project_binding=True)
        assert page2.total == 4 and page2.has_more is False
        assert [row["job_id"] for row in page2] == expected_desc[2:]
        combined = [row["job_id"] for row in page1] + [row["job_id"] for row in page2]
        assert combined == expected_desc
        assert len(set(combined)) == page1.total
        for row in page1:
            metadata = row["metadata"]
            bound_id = metadata.get("project_id") or metadata["execution"]["project_id"]
            assert bound_id == PROJECT_A
        listing = page1.as_dict()
        assert set(listing) == {"jobs", "total", "offset", "limit", "has_more"}


def test_t35jss_strict_list_honours_status_filter() -> None:
    with _fresh_store() as store:
        ids = _seed_catalog(store, include_malformed=True)
        succeeded = store.list_jobs(status="SUCCEEDED", project_id=PROJECT_A,
                                    enforce_project_binding=True)
        assert succeeded.total == 2
        assert [row["job_id"] for row in succeeded] == [ids["flat-a-done"], ids["nested-a"]]
        running = store.list_jobs(status="RUNNING", project_id=PROJECT_A,
                                  enforce_project_binding=True)
        assert [row["job_id"] for row in running] == [ids["split-a"]]
        empty = store.list_jobs(status="CANCELLED", project_id=PROJECT_A,
                                enforce_project_binding=True)
        assert isinstance(empty, JobList) and empty.total == 0 and list(empty) == []
        assert empty.has_more is False


def test_t35jss_strict_list_excludes_spoof_mismatch_unbound_malformed_and_divergent() -> None:
    with _fresh_store() as store:
        ids = _seed_catalog(store, include_malformed=True)
        page = store.list_jobs(limit=1000, project_id=PROJECT_A, enforce_project_binding=True)
        got = {row["job_id"] for row in page}
        assert got == {ids["flat-a"], ids["nested-a"], ids["split-a"], ids["flat-a-done"]}
        for name in ("unbound", "cross-a-b", "root-claim-a", "malformed-a", "flat-b", "divergent-a"):
            assert ids[name] not in got
            assert ids[name] not in json.dumps(page.as_dict(), sort_keys=True)


def test_t35jss_strict_list_final_pages_are_exact_without_padding() -> None:
    with _fresh_store() as store:
        ids = _seed_catalog(store, include_malformed=True)
        tail = store.list_jobs(offset=3, limit=5, project_id=PROJECT_A, enforce_project_binding=True)
        assert tail.total == 4 and tail.has_more is False
        assert [row["job_id"] for row in tail] == [ids["flat-a"]]
        beyond = store.list_jobs(offset=4, limit=5, project_id=PROJECT_A, enforce_project_binding=True)
        assert beyond.total == 4 and list(beyond) == [] and beyond.has_more is False


def test_t35jss_strict_list_requires_nonempty_project_id() -> None:
    with _fresh_store() as store:
        _seed_catalog(store, include_malformed=True)
        _expect_call_raises(ValueError, "missing project_id",
                            lambda: store.list_jobs(project_id=None, enforce_project_binding=True))
        _expect_call_raises(ValueError, "empty project_id",
                            lambda: store.list_jobs(project_id="", enforce_project_binding=True))
        # A nonempty but unbound query ID is a legitimate empty result, not an
        # error and not a wildcard (whitespace-claim acceptance itself is not
        # asserted either way — that is the helper's live contract).
        blank = store.list_jobs(project_id="  ", enforce_project_binding=True)
        assert blank.total == 0 and list(blank) == []


def test_t35jss_strict_list_rejects_nonboolean_flag() -> None:
    with _fresh_store() as store:
        _seed_catalog(store, include_malformed=False)
        _expect_call_raises(
            TypeError,
            "non-boolean enforce flag",
            lambda: store.list_jobs(
                project_id=PROJECT_A, enforce_project_binding="yes"  # pyright: ignore[reportArgumentType]
            ),
        )


def test_t35jss_strict_list_skips_malformed_row_and_store_stays_consistent() -> None:
    with _fresh_store() as store:
        _seed_catalog(store, include_malformed=True)
        page = store.list_jobs(limit=1000, project_id=PROJECT_A, enforce_project_binding=True)
        assert page.total == 4
        assert store.db.in_transaction is False
        new_job = _begin_pair(store, "strict-after", {"project_id": PROJECT_A})
        after = store.list_jobs(limit=1000, project_id=PROJECT_A, enforce_project_binding=True)
        assert after.total == 5
        assert new_job in {row["job_id"] for row in after}


def test_t35jss_strict_and_legacy_diverge_on_malformed_rows() -> None:
    """The legacy metadata OR filter runs SQLite json_extract over every row
    and aborts on a malformed metadata row, while strict mode scans with
    Python-side parse-and-skip.  Both behaviors are pinned so the strict mode
    cannot silently redefine legacy behavior."""
    with _fresh_store() as store:
        _seed_catalog(store, include_malformed=True)
        _expect_call_raises(sqlite3.OperationalError, "legacy json_extract on malformed row",
                            lambda: store.list_jobs(project_id=PROJECT_A))
        strict = store.list_jobs(project_id=PROJECT_A, enforce_project_binding=True)
        assert strict.total == 4


# ---------------------------------------------------------------------------
# (C) Legacy compatibility — unscoped behavior must be byte-identical
# ---------------------------------------------------------------------------

def test_t35jss_legacy_list_project_filter_behavior_preserved() -> None:
    """Without enforce_project_binding the permissive durable-metadata OR
    (project_id OR execution.project_id OR project_root) is unchanged,
    including matching the synthetic forged project_root claim record."""
    with _fresh_store() as store:
        ids = _seed_catalog(store, include_malformed=False)
        legacy = store.list_jobs(project_id=PROJECT_A)
        assert legacy.total == 7
        assert [row["job_id"] for row in legacy] == [
            ids["divergent-a"], ids["flat-a-done"], ids["root-claim-a"],
            ids["split-a"], ids["cross-a-b"], ids["nested-a"], ids["flat-a"],
        ]
        assert ids["root-claim-a"] in {row["job_id"] for row in legacy}
        assert ids["flat-b"] not in {row["job_id"] for row in legacy}
        legacy_page = store.list_jobs(offset=1, limit=2, project_id=PROJECT_A)
        assert legacy_page.total == 7 and legacy_page.has_more is True
        assert [row["job_id"] for row in legacy_page] == [ids["flat-a-done"], ids["root-claim-a"]]


def test_t35jss_legacy_list_unscoped_defaults_preserved() -> None:
    with _fresh_store() as store:
        ids = _seed_catalog(store, include_malformed=False)
        unscoped = store.list_jobs()
        assert unscoped.total == 9 and unscoped.offset == 0 and unscoped.limit == 100
        assert unscoped.has_more is False
        assert [row["job_id"] for row in unscoped][0] == ids["divergent-a"]
        status_only = store.list_jobs(status="SUCCEEDED")
        assert status_only.total == 2
        assert {row["job_id"] for row in status_only} == {ids["nested-a"], ids["flat-a-done"]}


def test_t35jss_snapshot_positive_agrees_with_strict_list_membership() -> None:
    """The two store primitives agree on the same synthetic binding record."""
    with _fresh_store() as store:
        ids = _seed_catalog(store, include_malformed=False)
        with store.project_job_read_snapshot(ids["split-a"], PROJECT_A) as bound:
            assert bound is True
        strict = store.list_jobs(limit=1000, project_id=PROJECT_A, enforce_project_binding=True)
        assert ids["split-a"] in {row["job_id"] for row in strict}
        assert ids["root-claim-a"] not in {row["job_id"] for row in strict}
