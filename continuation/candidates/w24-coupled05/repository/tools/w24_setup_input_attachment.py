"""Authenticate immutable current11 setup inputs. No engine or Worker actions."""
from pathlib import Path
import hashlib,json,os,sqlite3,stat
ROOT_REVIEW_SHA = "2efd2dc6a53408d5868c43610ca3b082100cb5a7b1e6c9939e48eae935d1891a"
EVIDENCE = Path("/Volumes/SSD/COMSOL_MCP_FULL_PROJECT_CONTINUATION_V2/repository/docs/full_project_continuation_v2/evidence/w24_cure_preflight_current11")
WORK = Path("/private/tmp/comsol-mcp-w24-cure-setup-current11-20261012-r1")
SCHEMA = "W24_CURRENT11_INPUT_ATTACHMENT_V1"
ROLES = {"root_review", "template_receipt", "build_readback", "reopen_readback", "preservation_manifest", "operations_db"}
class AttachmentError(ValueError): pass
def _pairs(rows):
    result = {}
    for key,value in rows:
        if key in result: raise AttachmentError("duplicate JSON key")
        result[key] = value
    return result
def _json(raw): return json.loads(raw,object_pairs_hook=_pairs)
def _hash(raw): return hashlib.sha256(raw).hexdigest()
def _read(row, *, manifest=False):
    if not isinstance(row,dict) or set(row) != {"path","bytes","sha256","uid"}: raise AttachmentError("attachment row schema is not closed")
    p = Path(row["path"])
    if not p.is_absolute() or str(p) != str(p.resolve(strict=True)): raise AttachmentError("attachment path is noncanonical or symlink")
    if manifest:
        if Path(__file__).resolve().parents[2] not in p.parents: raise AttachmentError("manifest outside private candidate scope")
    elif not (EVIDENCE == p.parent or EVIDENCE in p.parents or WORK in p.parents): raise AttachmentError("attachment outside original task/preservation scope")
    before = p.lstat()
    if not stat.S_ISREG(before.st_mode) or before.st_uid != os.getuid() or type(row["uid"]) is not int or row["uid"] != before.st_uid: raise AttachmentError("attachment is not ordinary owner-bound file")
    if type(row["bytes"]) is not int or row["bytes"] < 0 or row["bytes"] != before.st_size: raise AttachmentError("attachment bytes mismatch")
    fd = os.open(p,os.O_RDONLY|os.O_NOFOLLOW)
    with os.fdopen(fd,"rb") as stream:
        opened = os.fstat(stream.fileno()); raw = stream.read(); after_open = os.fstat(stream.fileno())
    after = p.lstat()
    key = lambda s:(s.st_dev,s.st_ino,s.st_size,s.st_uid,s.st_mtime_ns,s.st_ctime_ns)
    if key(before)!=key(opened) or key(before)!=key(after_open) or key(before)!=key(after) or _hash(raw)!=row["sha256"]: raise AttachmentError("attachment changed identity or hash while read")
    return raw,key(before)
def validate_attachment(manifest_path, receipt_path):
    manifest_path = Path(manifest_path)
    if manifest_path.is_symlink() or manifest_path.resolve(strict=True)!=manifest_path or manifest_path.stat().st_uid != os.getuid(): raise AttachmentError("manifest must be canonical owner-bound ordinary file")
    manifest_row = {"path":str(manifest_path), "bytes":manifest_path.stat().st_size, "sha256":_hash(manifest_path.read_bytes()), "uid":os.getuid()}
    manifest_raw, manifest_identity = _read(manifest_row, manifest=True)
    m = _json(manifest_raw)
    if set(m) != {"schema","files","roles"} or m["schema"] != SCHEMA or set(m["roles"]) != ROLES or not isinstance(m["files"],list): raise AttachmentError("manifest schema/roles are not closed")
    files = {}; raw = {}; identities = {}
    for row in m["files"]:
        if not isinstance(row,dict): raise AttachmentError("attachment row must be object")
        path = row.get("path")
        if path in files: raise AttachmentError("duplicate attachment path")
        raw[path],identities[path] = _read(row); files[path] = row
    if any(path not in files for path in m["roles"].values()): raise AttachmentError("required role absent from closure")
    if len(set(m["roles"].values())) != len(ROLES): raise AttachmentError("role aliasing rejected")
    role = lambda name:_json(raw[m["roles"][name]])
    if str(Path(receipt_path).resolve(strict=True)) != m["roles"]["template_receipt"]: raise AttachmentError("manifest binds another original receipt")
    review = role("root_review")
    if files[m["roles"]["root_review"]]["sha256"] != ROOT_REVIEW_SHA: raise AttachmentError("exact Root review hash differs")
    reviewed = {x["path"]:(x["sha256"],x["bytes"]) for x in review["verified_references"]}
    for path,row in files.items():
        if path != m["roles"]["root_review"] and reviewed.get(path) != (row["sha256"],row["bytes"]): raise AttachmentError("file was not byte-bound by exact Root audit")
    receipt = role("template_receipt"); build = role("build_readback"); reopen = role("reopen_readback"); preservation = role("preservation_manifest")
    # The closure must include every original/preserved selected byte and every audited task artifact.
    required = {x["path"] for x in review["verified_references"] if str(x["path"]).startswith(str(EVIDENCE/"native-run-current11-001")+"/") or str(x["path"]).startswith(str(WORK)+"/")}
    if required - set(files): raise AttachmentError("audited original input closure is incomplete")
    for row in preservation["files"]:
        for key in ("source_path","preserved_path"):
            if row[key] not in files or files[row[key]]["sha256"] != row["sha256"] or files[row[key]]["bytes"] != row["bytes"]: raise AttachmentError("preservation original/copy binding missing")
    if str(receipt["path"]) not in files or files[receipt["path"]]["sha256"] != receipt["sha256"] or files[receipt["path"]]["bytes"] != receipt["size_bytes"]: raise AttachmentError("saved MPH binding differs")
    if receipt["reopen_readback"] != m["roles"]["reopen_readback"] or reopen["saved_template_sha256"] != receipt["sha256"] or reopen["study_run_submissions_so_far"] != [] or build["study_run_submissions_so_far"] != []: raise AttachmentError("native save/reopen/zero solve binding differs")
    db = Path(m["roles"]["operations_db"])
    if "private-control-store/work/control/operations.sqlite3" not in str(db): raise AttachmentError("operations DB must be preserved immutable copy")
    with sqlite3.connect(db.as_uri()+"?mode=ro&immutable=1",uri=True) as conn:
        if conn.execute("pragma integrity_check").fetchall() != [("ok",)]: raise AttachmentError("immutable DB integrity failed")
        rows = conn.execute("select status,result from operations").fetchall()
    if not rows or any(status != "SUCCEEDED" for status,_ in rows): raise AttachmentError("original operations not all terminal SUCCEEDED")
    results = [_json(value) for _,value in rows]
    native_origins = []
    for row, response_key in ((build,"build_response"),(reopen,"reopen_response")):
        observation = row["fixture_readback"]["mesh_volume_observation"]
        artifact_path = observation["evidence_artifact_path"]
        if artifact_path not in files: raise AttachmentError("raw mesh origin artifact absent from closure")
        origin = _json(raw[artifact_path])["native_response"]
        if origin["execution"] != row[response_key]["execution"]: raise AttachmentError("enriched response/original operation binding differs")
        native_origins.append(origin)
    for origin in (*native_origins,receipt["save_response"]):
        if sum(origin==result for result in results)!=1: raise AttachmentError("native origin is not one exact immutable operations.result")
    from tools.run_native_w24_cure_preflight import _compare_reopened_readback
    comparison = _compare_reopened_readback(build["fixture_readback"],reopen["fixture_readback"])
    if comparison.get("matches") is not True: raise AttachmentError("strict 21field/origin/epoch/revision/domain mesh recheck failed")
    inventories = [receipt["equation_view_inventory"],receipt["reopened_equation_view_inventory"]]
    for inv in inventories:
        for key in ("path","evidence_copy_path"):
            if inv[key] not in files or files[inv[key]]["sha256"]!=inv["sha256"] or files[inv[key]]["bytes"]!=inv["size_bytes"]: raise AttachmentError("complete EquationView artifact missing or mismatched")
    if raw[inventories[0]["path"]] != raw[inventories[1]["path"]]: raise AttachmentError("complete EquationView raw bytes differ")
    for path,row in files.items():
        current,key = _read(row)
        if key != identities[path] or current != raw[path]: raise AttachmentError("attachment changed after semantic checks")
    _, final_manifest_identity = _read(manifest_row, manifest=True)
    if final_manifest_identity != manifest_identity: raise AttachmentError("manifest changed during validation")
    return {"fixture_readback":build["fixture_readback"],"attachment_path":str(manifest_path),"attachment_sha256":_hash(manifest_path.read_bytes()),"root_review_sha256":ROOT_REVIEW_SHA,"verified_file_count":len(files),"comparison":comparison,"historical_refs":"PROVENANCE_ONLY_TERMINATED;newscienceLOAD_ADOPT_FRESH_EPOCH_REQUIRED"}
