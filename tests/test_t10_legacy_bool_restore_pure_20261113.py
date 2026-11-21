"""Synthetic same-worker legacy schema recovery, without real model/worker IO.

Only two fresh in-memory mappings are used. There is no old fixture-file
restoration, real persistent store, model, worker, SSH, operation or UNKNOWN.
This is separate from fenced boolean-generation, non-identity/hash and rebind
outcomes. The legacy bool case asserts internal canonical integer version while
preserving identity and counters, not blanket acceptance of external bool refs.
"""
from copy import deepcopy

import pytest  # pyright: ignore[reportMissingImports]

from comsol_mcp._runtime_state import restore_runtime_state


class ReadOnlyState:
    def __init__(self, value):
        self.value = value
        self.reads = []

    def get_metadata(self, table, key):
        self.reads.append((table, key))
        assert (table, key) == ("sessions", "synthetic-pure-session")
        return self.value


def make_state(schema_version):
    identity = {
        "worker_instance_id": "synthetic-pure-worker",
        "connection_epoch": 14,
        "server_instance_id": "synthetic-pure-server",
    }
    state = {
        "schema_version": 1,
        "worker_identity": dict(identity),
        "session_id": "synthetic-pure-session",
        "server_instance_id": "synthetic-pure-server",
        "server_ownership": "shared",
        "permissions": ["inspect", "project_write"],
        "generations": {"synthetic-pure-tag": 7},
        "models": {
            "synthetic-pure-tag": {
                "ref": {
                    "session_id": "synthetic-pure-session",
                    "server_instance_id": "synthetic-pure-server",
                    "model_tag": "synthetic-pure-tag",
                    "generation": 7,
                    "schema_version": schema_version,
                },
                "revision": 21,
                "fingerprint": "synthetic-pure-fingerprint",
                "external_event_counter": 11,
                "observed_external_event_counter": 9,
                "dirty": True,
                "retired": False,
                "active_operation_id": None,
                "ownership": "user_owned",
            }
        },
        "active_tickets": [],
    }
    return identity, state


@pytest.mark.parametrize("schema_version", [1, True], ids=["int1", "legacy-true"])
def test_same_worker_restore_canonical_version_and_preserves_state(schema_version):
    identity, source = make_state(schema_version)
    before = deepcopy(source)
    store = ReadOnlyState(source)

    ledger, unknown = restore_runtime_state(store, "synthetic-pure-session", identity)

    assert store.reads == [("sessions", "synthetic-pure-session")]
    assert source == before
    assert type(source["models"]["synthetic-pure-tag"]["ref"]["schema_version"]) is type(schema_version)
    model = ledger._models["synthetic-pure-tag"]
    assert ledger.session_id == "synthetic-pure-session"
    assert ledger.server_instance_id == "synthetic-pure-server"
    assert ledger._generations == {"synthetic-pure-tag": 7}
    assert ledger.model_ownership == {"synthetic-pure-tag": "user_owned"}
    assert model.ref.session_id == "synthetic-pure-session"
    assert model.ref.server_instance_id == "synthetic-pure-server"
    assert model.ref.model_tag == "synthetic-pure-tag"
    assert model.ref.generation == 7
    assert model.revision == 21
    assert model.external_event_counter == 11
    assert model.observed_external_event_counter == 9
    assert model.fingerprint == "synthetic-pure-fingerprint"
    assert model.dirty is True and model.retired is False
    assert model.active_operation_id is None and unknown == []
    assert type(model.ref.schema_version) is int
    assert model.ref.schema_version == 1
