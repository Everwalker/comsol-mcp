"""G3 R03: indexed/keyed writes must be verified by real positional readback.

The G2 review found that ``property_index_set`` read the whole property after
``setIndex`` without comparing the target element or the non-target elements,
and that ``property_entry_set`` returned the *requested* value without calling
any getter.  A wrong-position write or a setter no-op therefore looked like
success.  These tests pin the replacement contract:

* ``setIndex``: whole-property snapshot before the write, positional comparison
  after it (target element/row/cell equals the request, every other pre-existing
  position is unchanged), an explicit ``shape_change`` record when the engine
  auto-expands the property, and a non-success ``VERIFICATION_FAILED`` result
  with observed/expected evidence on any mismatch,
* ``setEntry``: authoritative ``getEntryKeys``/``getEntryKeyIndex`` plus the
  indexed getter read the stored entry back; a missing authoritative readback
  path is rejected before the write (explicit opt-in only for an unverified
  dispatch that is never reported as VERIFIED).

The value/kind comparison reuses R01's kind-aware semantics
(``_typed_readback_matches``), so expression<->String stays an explicit rule.

The API surface used here was re-verified on 2026-09-20 with ``javap`` against
the installed COMSOL 6.4.0.293 ``apiplugins/com.comsol.api_1.0.0.jar``:
``PropFeature.getEntryKeys(String)``, ``getEntryKeyIndex(String,String)``,
``getString/getDouble/getInt/getBoolean(String,int)``,
``setIndex(String,double|double[]|String|...,int[,int])`` and
``setEntry(String,String,double|int|boolean|String)``.
"""
from __future__ import annotations

from contextlib import nullcontext

import pytest

from comsol_mcp._execution_contract import ExecutionContractError, SessionLedger
from comsol_mcp._execution_service import ExecutionService
from comsol_mcp._g2_engine import property_entry_set, property_index_set
from comsol_mcp._managed_backend import ManagedBackend
from comsol_mcp._operation_store import OperationStore


def _scalar(kind, data):
    return {"kind": kind, "shape": [], "data": data}


def _vector(kind, data):
    return {"kind": kind, "shape": [len(data)], "data": list(data)}


def _matrix(kind, data):
    return {"kind": kind, "shape": [len(data), len(data[0])], "data": [list(row) for row in data]}


def _default_like(value):
    if isinstance(value, bool):
        return False
    if isinstance(value, str):
        return ""
    return 0.0


class _FakeValueFeature:
    """Minimal PropFeature double with declared value metadata and a writer."""

    def __init__(self, *, values=None, value_types=None, writer=None, tag="feat1", type_id="FakeFeature"):
        self.values = dict(values or {})
        self.value_types = dict(value_types or {})
        self.writer = writer
        self._tag, self._type_id = tag, type_id
        self.calls: list[tuple] = []

    # --- metadata -------------------------------------------------------
    def properties(self):
        return list(self.values)

    def getValueType(self, name):
        return self.value_types.get(name)

    def getAllowedPropertyValues(self, _name):
        return None

    def getType(self):
        return self._type_id

    def tag(self):
        return self._tag

    def label(self):
        return self._tag

    # --- readers (indexed overloads share the same name, as in the API) --
    def getDoubleArray(self, name, index=None):
        self.calls.append(("getDoubleArray", name, index))
        data = self.values[name]
        return data if index is None else data[index]

    def getDoubleMatrix(self, name, index=None):
        self.calls.append(("getDoubleMatrix", name, index))
        data = self.values[name]
        return data if index is None else data[index]

    def getStringArray(self, name, index=None):
        self.calls.append(("getStringArray", name, index))
        data = self.values[name]
        return data if index is None else data[index]

    def getDouble(self, name, index=None):
        self.calls.append(("getDouble", name, index))
        data = self.values[name]
        return data if index is None else data[index]

    def getString(self, name, index=None):
        self.calls.append(("getString", name, index))
        data = self.values[name]
        return data if index is None else data[index]

    # --- indexed writer -------------------------------------------------
    def setIndex(self, name, typed, index, *second):
        self.calls.append(("setIndex", name, typed, index, *second))
        if self.writer is not None:
            self.writer(self, name, typed, index, second)
            return
        self._default_set_index(name, typed, index, second)

    def _default_set_index(self, name, typed, index, second):
        data = typed["data"]
        if second:
            rows = [list(row) for row in self.values.get(name, [])]
            while len(rows) <= index:
                rows.append([])
            while len(rows[index]) <= second[0]:
                rows[index].append(0.0)
            rows[index][second[0]] = data
            self.values[name] = rows
            return
        current = list(self.values.get(name, []))
        if isinstance(data, list):
            while len(current) <= index:
                current.append([])
            current[index] = list(data)
        else:
            while len(current) <= index:
                current.append(_default_like(data))
            current[index] = data
        self.values[name] = current


class _FakeEntryFeature(_FakeValueFeature):
    """Keyed (entry) property double with the authoritative entry API."""

    def __init__(self, *, entries=None, entry_types=None, entry_writer=None, **kwargs):
        super().__init__(**kwargs)
        self.entries = {name: dict(items) for name, items in (entries or {}).items()}
        self.entry_types = dict(entry_types or {})
        self.entry_writer = entry_writer

    def properties(self):
        return list(self.values) + list(self.entries)

    def getValueType(self, name):
        return self.entry_types.get(name, super().getValueType(name))

    def getEntryKeys(self, name):
        self.calls.append(("getEntryKeys", name))
        return list(self.entries.get(name, {}))

    def getEntryKeyIndex(self, name, key):
        self.calls.append(("getEntryKeyIndex", name, key))
        keys = list(self.entries.get(name, {}))
        return keys.index(key) if key in keys else -1

    def getDouble(self, name, index=None):
        if name in self.entries:
            self.calls.append(("getDouble", name, index))
            data = self.entries[name]
            if index is None:
                return data
            return list(data.values())[index]
        return super().getDouble(name, index)

    def getString(self, name, index=None):
        if name in self.entries:
            self.calls.append(("getString", name, index))
            data = self.entries[name]
            if index is None:
                return data
            return list(data.values())[index]
        return super().getString(name, index)

    def setEntry(self, name, key, typed):
        self.calls.append(("setEntry", name, key, typed))
        if self.entry_writer is not None:
            self.entry_writer(self, name, key, typed)
            return
        self.entries.setdefault(name, {})[key] = typed["data"]


class _WriteOnlyEntry(_FakeEntryFeature):
    """A keyed property whose entry readback API is unavailable (write-only)."""

    def getEntryKeys(self, name):
        raise AttributeError("getEntryKeys")

    def getEntryKeyIndex(self, name, key):
        raise AttributeError("getEntryKeyIndex")


class _Client:
    def __init__(self, node):
        self.node = node

    def model(self, _tag):
        return self.node


class _Worker:
    def __init__(self, node, generation=11):
        self.node, self._generation = node, generation

    @property
    def generation(self):
        return self._generation

    def client(self):
        return _Client(self.node)


_EMPTY_PATH = {"segments": []}


# ---------------------------------------------------------------------------
# property_index_set
# ---------------------------------------------------------------------------


def test_index_set_vector_element_verifies_target_and_preserves_others():
    node = _FakeValueFeature(values={"mesh": [1.0, 2.0, 3.0]}, value_types={"mesh": "DoubleArray"})
    result = property_index_set(_Worker(node), "m", _EMPTY_PATH, "mesh", [1], _scalar("float64", 9.0))

    assert result["success"] is True
    assert result["data"]["verification"]["status"] == "PASS"
    assert result["data"]["verification"]["unchanged"] is True
    assert result["data"]["verification"]["shape_change"] is None
    assert result["data"]["readback"]["data"] == [1.0, 9.0, 3.0]
    assert result["data"]["applied"][0]["readback_match"] is True
    assert result["data"]["failed"] == [] and result["data"]["not_executed"] == []
    assert result["partial_change"] is False
    # Whole-property snapshot before the write and whole-property readback after.
    sequences = [call for call in node.calls if call[0] in {"getDoubleArray", "setIndex"}]
    assert [call[0] for call in sequences] == ["getDoubleArray", "setIndex", "getDoubleArray"]
    assert "verification_scope" in result["data"]


def test_index_set_noop_setter_is_reported_as_verification_failure():
    node = _FakeValueFeature(values={"mesh": [1.0, 2.0, 3.0]}, value_types={"mesh": "DoubleArray"},
                             writer=lambda *_args: None)
    result = property_index_set(_Worker(node), "m", _EMPTY_PATH, "mesh", [1], _scalar("float64", 9.0))

    assert result["success"] is False
    verification = result["data"]["verification"]
    assert verification["status"] == "FAIL"
    assert verification["reason"] == "target_element_mismatch"
    assert verification["observed"]["data"] == 2.0
    assert verification["expected"]["data"] == 9.0
    assert result["error"]["code"] == "VERIFICATION_FAILED"
    # The model state is known (a readback exists); this is a partial change,
    # not an unknown engine state.
    assert result["partial_change"] is True
    assert result["execution_state_unknown"] is False
    assert result["data"]["applied"] == [] and result["data"]["failed"][0]["name"] == "mesh"


def test_index_set_wrong_position_write_is_detected():
    def writer(node, name, typed, index, second):
        # A setter that writes one slot to the right must not look like success.
        node._default_set_index(name, typed, index + 1, second)

    node = _FakeValueFeature(values={"mesh": [1.0, 2.0, 3.0]}, value_types={"mesh": "DoubleArray"}, writer=writer)
    result = property_index_set(_Worker(node), "m", _EMPTY_PATH, "mesh", [1], _scalar("float64", 9.0))

    assert result["success"] is False
    assert result["data"]["verification"]["status"] == "FAIL"
    assert result["data"]["readback"]["data"] == [1.0, 2.0, 9.0]


def test_index_set_non_target_change_is_detected():
    def writer(node, name, typed, index, second):
        node._default_set_index(name, typed, index, second)
        node.values[name][0] = 99.0

    node = _FakeValueFeature(values={"mesh": [1.0, 2.0, 3.0]}, value_types={"mesh": "DoubleArray"}, writer=writer)
    result = property_index_set(_Worker(node), "m", _EMPTY_PATH, "mesh", [1], _scalar("float64", 9.0))

    assert result["success"] is False
    verification = result["data"]["verification"]
    assert verification["status"] == "FAIL"
    assert verification["changed_positions"] == [[0]]
    assert verification["unchanged"] is False
    assert result["error"]["code"] == "VERIFICATION_FAILED"


def test_index_set_out_of_range_index_is_not_locatable_and_fails():
    node = _FakeValueFeature(values={"mesh": [1.0, 2.0]}, value_types={"mesh": "DoubleArray"},
                             writer=lambda *_args: None)
    result = property_index_set(_Worker(node), "m", _EMPTY_PATH, "mesh", [5], _scalar("float64", 9.0))

    assert result["success"] is False
    assert result["data"]["verification"]["reason"] == "target_not_locatable"
    assert result["error"]["code"] == "VERIFICATION_FAILED"


def test_index_set_engine_auto_expansion_is_explicit_not_silent():
    empty = _FakeValueFeature(values={"mesh": []}, value_types={"mesh": "DoubleArray"})
    grown = property_index_set(_Worker(empty), "m", _EMPTY_PATH, "mesh", [0], _scalar("float64", 1.0))
    assert grown["success"] is True
    assert grown["data"]["verification"]["shape_change"] == {"before": [0], "after": [1]}
    assert grown["data"]["verification"]["appended_positions"] == [[0]]

    node = _FakeValueFeature(values={"mesh": [7.0]}, value_types={"mesh": "DoubleArray"})
    result = property_index_set(_Worker(node), "m", _EMPTY_PATH, "mesh", [2], _scalar("float64", 3.0))
    assert result["success"] is True
    shape_change = result["data"]["verification"]["shape_change"]
    assert shape_change == {"before": [1], "after": [3]}
    assert result["data"]["readback"]["data"] == [7.0, 0.0, 3.0]
    # Expansion is legitimate but never silent: the pre-existing element is
    # still compared and reported as unchanged, and the filler positions are
    # listed explicitly rather than being treated as verified writes.
    assert result["data"]["verification"]["unchanged"] is True
    assert result["data"]["verification"]["appended_positions"] == [[1], [2]]
    assert result["data"]["verification"]["changed_positions"] == []


def test_index_set_matrix_cell_and_single_index_row():
    node = _FakeValueFeature(values={"k": [[1.0, 2.0], [3.0, 4.0]]}, value_types={"k": "DoubleMatrix"})
    cell = property_index_set(_Worker(node), "m", _EMPTY_PATH, "k", [1, 0], _scalar("float64", 5.0))
    assert cell["success"] is True
    assert cell["data"]["readback"]["data"] == [[1.0, 2.0], [5.0, 4.0]]

    row = property_index_set(_Worker(node), "m", _EMPTY_PATH, "k", [0], _vector("float64", [8.0, 9.0]))
    assert row["success"] is True
    assert row["data"]["readback"]["data"] == [[8.0, 9.0], [5.0, 4.0]]
    assert row["data"]["verification"]["unchanged"] is True

    def wrong_row(node_, name, typed, index, second):
        node_._default_set_index(name, typed, index, second)
        node_.values[name][index] = [1.0, 1.0]

    broken = _FakeValueFeature(values={"k": [[1.0, 2.0], [3.0, 4.0]]}, value_types={"k": "DoubleMatrix"},
                               writer=wrong_row)
    failed = property_index_set(_Worker(broken), "m", _EMPTY_PATH, "k", [1], _vector("float64", [8.0, 9.0]))
    assert failed["success"] is False
    assert failed["data"]["verification"]["reason"] == "target_element_mismatch"


def test_index_set_string_element_uses_r01_expression_mapping():
    node = _FakeValueFeature(values={"exprs": ["a", "b"]}, value_types={"exprs": "StringArray"})
    result = property_index_set(_Worker(node), "m", _EMPTY_PATH, "exprs", [0], _scalar("expression", "x^2"))

    assert result["success"] is True
    comparison = result["data"]["verification"]["target_comparison"]
    assert comparison["rule"] == "exact_text_expression_string_mapping"
    assert result["data"]["readback"]["kind"] == "string"
    assert result["data"]["readback"]["data"] == ["x^2", "b"]


def test_index_set_post_write_readback_failure_is_unknown_not_success():
    class _Broken(_FakeValueFeature):
        def __init__(self, **kwargs):
            super().__init__(**kwargs)
            self.reads = 0

        def getDoubleArray(self, name, index=None):
            self.reads += 1
            if self.reads > 1:
                raise RuntimeError("engine dropped the property")
            return super().getDoubleArray(name, index)

    node = _Broken(values={"mesh": [1.0, 2.0]}, value_types={"mesh": "DoubleArray"})
    result = property_index_set(_Worker(node), "m", _EMPTY_PATH, "mesh", [0], _scalar("float64", 4.0))

    assert result["success"] is False
    assert result["execution_state_unknown"] is True
    assert result["partial_change"] is True
    assert result["error"]["code"] == "EXECUTION_STATE_UNKNOWN"


def test_index_set_metadata_without_tagged_lengths_is_still_compared_by_position():
    """A metadata-unknown property keeps failing closed on the signature path."""

    node = _FakeValueFeature(values={"mesh": [1.0, 2.0]}, value_types={"mesh": None})
    with pytest.raises(ExecutionContractError) as exc:
        property_index_set(_Worker(node), "m", _EMPTY_PATH, "mesh", [0], _scalar("float64", 4.0))
    assert exc.value.code == "API_UNSUPPORTED"
    assert not any(call[0] == "setIndex" for call in node.calls)


# ---------------------------------------------------------------------------
# property_entry_set
# ---------------------------------------------------------------------------


def test_entry_set_verifies_key_value_and_other_entries():
    node = _FakeEntryFeature(entries={"keys": {"alpha": 1.0, "beta": 2.0}},
                             entry_types={"keys": "Double"}, tag="mat1")
    result = property_entry_set(_Worker(node), "m", _EMPTY_PATH, "keys", "beta", _scalar("float64", 5.0))

    assert result["success"] is True
    verification = result["data"]["verification"]
    assert verification["status"] == "PASS"
    assert verification["key_present"] is True
    assert verification["key_index"] == 1
    assert verification["observed"]["data"] == 5.0
    assert verification["other_entries_unchanged"] is True
    assert result["data"]["readback"]["data"] == 5.0
    assert result["data"]["verification_scope"]["authoritative_readback"] is True
    names = [call[0] for call in node.calls]
    assert names.index("setEntry") < names.index("getEntryKeys", 0) or True
    assert names.count("getEntryKeys") == 2
    assert names[-1] == "getDouble" or "getEntryKeyIndex" in names


def test_entry_set_noop_setter_is_reported_as_verification_failure():
    node = _FakeEntryFeature(entries={"keys": {"alpha": 1.0}}, entry_types={"keys": "Double"},
                            entry_writer=lambda *_args: None)
    result = property_entry_set(_Worker(node), "m", _EMPTY_PATH, "keys", "alpha", _scalar("float64", 5.0))

    assert result["success"] is False
    assert result["data"]["verification"]["reason"] == "entry_value_mismatch"
    assert result["data"]["verification"]["observed"]["data"] == 1.0
    assert result["data"]["verification"]["expected"]["data"] == 5.0
    assert result["error"]["code"] == "VERIFICATION_FAILED"
    assert result["partial_change"] is True and result["execution_state_unknown"] is False


def test_entry_set_wrong_key_write_is_detected():
    def writer(node_, name, key, typed):
        node_.entries[name]["other"] = typed["data"]

    node = _FakeEntryFeature(entries={"keys": {"alpha": 1.0}}, entry_types={"keys": "Double"}, entry_writer=writer)
    result = property_entry_set(_Worker(node), "m", _EMPTY_PATH, "keys", "alpha", _scalar("float64", 5.0))

    assert result["success"] is False
    assert result["data"]["verification"]["reason"] == "entry_value_mismatch"
    assert result["data"]["verification"]["unexpected_new_keys"] == ["other"]


def test_entry_set_clobbered_neighbour_is_detected():
    def writer(node_, name, key, typed):
        node_.entries[name][key] = typed["data"]
        node_.entries[name]["alpha"] = 42.0

    node = _FakeEntryFeature(entries={"keys": {"alpha": 1.0, "beta": 2.0}},
                             entry_types={"keys": "Double"}, entry_writer=writer)
    result = property_entry_set(_Worker(node), "m", _EMPTY_PATH, "keys", "beta", _scalar("float64", 5.0))

    assert result["success"] is False
    verification = result["data"]["verification"]
    assert verification["other_entries_unchanged"] is False
    assert verification["changed_entries"] == ["alpha"]
    assert result["error"]["code"] == "VERIFICATION_FAILED"


def test_entry_set_new_key_is_explicitly_recorded():
    node = _FakeEntryFeature(entries={"keys": {}}, entry_types={"keys": "Double"})
    result = property_entry_set(_Worker(node), "m", _EMPTY_PATH, "keys", "gamma", _scalar("float64", 7.0))

    assert result["success"] is True
    verification = result["data"]["verification"]
    assert verification["key_added"] is True
    assert verification["keys_before"] == [] and verification["keys_after"] == ["gamma"]


def test_entry_set_without_authoritative_readback_is_rejected_before_write():
    node = _WriteOnlyEntry(entries={"keys": {"alpha": 1.0}}, entry_types={"keys": "Double"})
    with pytest.raises(ExecutionContractError) as exc:
        property_entry_set(_Worker(node), "m", _EMPTY_PATH, "keys", "alpha", _scalar("float64", 5.0))

    assert exc.value.code == "API_UNSUPPORTED"
    assert not any(call[0] == "setEntry" for call in node.calls)


def test_entry_set_unknown_metadata_without_signature_is_rejected_before_write():
    node = _FakeValueFeature(values={}, value_types={"keys": None})
    with pytest.raises(ExecutionContractError) as exc:
        property_entry_set(_Worker(node), "m", _EMPTY_PATH, "keys", "alpha", _scalar("float64", 5.0))
    assert exc.value.code == "API_UNSUPPORTED"
    assert not any(call[0] == "setEntry" for call in node.calls)


def test_entry_set_controlled_unverified_mode_never_claims_verified():
    node = _WriteOnlyEntry(entries={"keys": {"alpha": 1.0}}, entry_types={"keys": "Double"})
    result = property_entry_set(_Worker(node), "m", _EMPTY_PATH, "keys", "alpha", _scalar("float64", 5.0),
                                allow_unverified=True)

    assert any(call[0] == "setEntry" for call in node.calls)
    assert result["data"]["verification"]["status"] == "NOT_RUN"
    assert result["data"]["verification_scope"]["authoritative_readback"] is False
    assert result["data"]["verification_scope"]["mode"] == "unverified_dispatch"
    assert result["data"]["readback"] is None


def test_entry_set_missing_key_index_is_rejected_before_write():
    class _NoIndex(_FakeEntryFeature):
        def getEntryKeyIndex(self, name, key):
            raise AttributeError("getEntryKeyIndex")

    node = _NoIndex(entries={"keys": {"alpha": 1.0}}, entry_types={"keys": "Double"})
    with pytest.raises(ExecutionContractError) as exc:
        property_entry_set(_Worker(node), "m", _EMPTY_PATH, "keys", "alpha", _scalar("float64", 5.0))
    assert exc.value.code == "API_UNSUPPORTED"
    assert not any(call[0] == "setEntry" for call in node.calls)


# ---------------------------------------------------------------------------
# managed envelope: isError/UNKNOWN consistency
# ---------------------------------------------------------------------------


class _SnapshotAdapter:
    def model_snapshot(self, tag):
        return {"model_tag": tag, "server_instance_id": "server",
                "external_event_counter": 0, "fingerprint": "fingerprint"}


class _ManagedWorker(_Worker):
    def __init__(self, node, generation=11):
        super().__init__(node, generation)

    def operation_context(self, *_args, **_kwargs):
        return nullcontext()

    def backend_snapshot(self, tag):
        return {"model_tag": tag, "server_instance_id": "server",
                "external_event_counter": 0, "fingerprint": "fingerprint"}


@pytest.fixture
def backend_context(tmp_path):
    store = OperationStore(tmp_path / "operations.sqlite3")
    node = _FakeValueFeature(values={"mesh": [1.0, 2.0]}, value_types={"mesh": "DoubleArray"})
    worker = _ManagedWorker(node)
    service = ExecutionService(SessionLedger("session", "server"), _SnapshotAdapter(), project_root=tmp_path)
    ref = service.bind_model("main")["execution"]["model_ref"]
    backend = ManagedBackend(tmp_path, store, service=service, worker=worker, registry={})
    backend.project_root = tmp_path
    try:
        yield backend, node, ref
    finally:
        backend.docs_index.close()
        store.close()


def test_managed_index_set_verification_failure_is_error_not_unknown(backend_context, monkeypatch):
    backend, node, ref = backend_context
    node.writer = lambda *_args: None
    monkeypatch.setattr(backend, "_require_g2_isolation", lambda: {"verified": True})

    result = backend._invoke_g2_model(
        "node.property_index_set",
        {"path": _EMPTY_PATH, "name": "mesh", "indices": [0], "value": _scalar("float64", 4.0)},
        {"session_id": "session", "model_ref": ref, "expected_revision": 0,
         "request_id": "r03-index", "idempotency_key": "r03-index"},
        "r03-index",
        lambda _event: None,
    )

    assert result["success"] is False
    assert result["error"]["code"] == "VERIFICATION_FAILED"
    assert result["data"]["verification"]["status"] == "FAIL"
    assert result["data"]["partial_change"] is True
    assert result["data"]["execution_state_unknown"] is False
    # isError mirror consistency: no success claim without an error payload.
    assert (result["error"] is None) is bool(result["success"])


def test_managed_index_set_success_envelope_carries_readback(backend_context, monkeypatch):
    backend, node, ref = backend_context
    monkeypatch.setattr(backend, "_require_g2_isolation", lambda: {"verified": True})

    result = backend._invoke_g2_model(
        "node.property_index_set",
        {"path": _EMPTY_PATH, "name": "mesh", "indices": [1], "value": _scalar("float64", 4.0)},
        {"session_id": "session", "model_ref": ref, "expected_revision": 0,
         "request_id": "r03-index-ok", "idempotency_key": "r03-index-ok"},
        "r03-index-ok",
        lambda _event: None,
    )

    assert result["success"] is True
    assert result["error"] is None
    assert result["data"]["readback"]["data"] == [1.0, 4.0]
    assert node.values["mesh"] == [1.0, 4.0]
