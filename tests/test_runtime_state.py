import pytest
from comsol_mcp._execution_contract import SessionLedger
from comsol_mcp._runtime_state import RuntimeStateError, restore_runtime_state, save_runtime_state

class Store:
 def __init__(self): self.data={}
 def put_metadata(self,t,k,v): self.data[t,k]=v
 def get_metadata(self,t,k): return self.data.get((t,k))

def identity(epoch=1, server="srv-1"):
 return {"runtime_id":"rt-1","worker_instance_id":"worker-1","connection_epoch":epoch,"server_instance_id":server}

def test_roundtrip_preserves_full_model_state_and_active_ticket_unknown():
 store=Store(); ledger=SessionLedger("s","srv-1"); ref=ledger.bind_model("m",fingerprint="f")
 ticket=ledger.begin_write("set_parameters",{},ref,0,fingerprint="f")
 save_runtime_state(store,ledger,identity(),[ticket]); restored,unknown=restore_runtime_state(store,"s",identity())
 assert restored.revision(ref)==0 and restored._models["m"].dirty and unknown[0]["safe_retry"] is False

def test_worker_epoch_change_invalidates_ref_and_advances_generation():
 store=Store(); ledger=SessionLedger("s","srv-1"); old=ledger.bind_model("m",fingerprint="f")
 save_runtime_state(store,ledger,identity()); restored,_=restore_runtime_state(store,"s",identity(epoch=2,server="srv-2"))
 new=restored._models["m"].ref
 assert new.generation > old.generation and new.server_instance_id == "srv-2"
 with pytest.raises(Exception): restored.revision(old)

def test_future_schema_rejected():
 store=Store(); ledger=SessionLedger("s","srv-1"); save_runtime_state(store,ledger,identity())
 store.data["sessions","s"]["schema_version"]=99
 with pytest.raises(RuntimeStateError,match="schema"): restore_runtime_state(store,"s",identity())


def test_missing_worker_identity_never_preserves_old_ref():
    store=Store(); ledger=SessionLedger("s","srv-1"); old=ledger.bind_model("m",fingerprint="f")
    saved={"runtime_id":"rt-1","server_instance_id":"srv-1"}
    save_runtime_state(store,ledger,saved)
    restored,_=restore_runtime_state(store,"s",saved)
    assert restored._models["m"].ref.generation > old.generation
