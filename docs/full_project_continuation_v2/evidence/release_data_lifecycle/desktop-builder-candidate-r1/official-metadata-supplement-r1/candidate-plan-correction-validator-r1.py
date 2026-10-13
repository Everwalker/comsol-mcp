#!/usr/bin/env python3
"""Validate official PyPI metadata correction and candidate plan offline."""
import hashlib,json
from pathlib import Path
HERE=Path(__file__).resolve().parent
PLAN=HERE/"candidate-plan-corrected-view-r1.json"
ORIGINAL=HERE.parent/"candidate-plan-r1.json"
PROJECTION=HERE/"official-pypi-used-fields-r1.json"
def load(p): return json.loads(p.read_text())
def aid(url,sha): return hashlib.sha256((url+"\n"+sha).encode()).hexdigest()
def normalize_dist(value): return value or []
def main():
 p=load(PLAN); old=load(ORIGINAL); official=load(PROJECTION); corr=load(HERE/"URL_CORRECTION.json")
 assert p["schema"]==old["schema"] and p["status"]==old["status"]
 ofiles=[(pkg,f) for pkg in official["packages"] for f in pkg["selected_files"]]
 assert len(ofiles)==10
 byfile={r["filename"]:r for r in p["unique_download_set"]}
 assert len(byfile)==61
 mismatch=[]
 for pkg,f in ofiles:
  row=byfile[f["filename"]]
  for k,official_key in (("url","url"),("sha256","sha256"),("size_bytes","size_bytes"),("requires_python","requires_python")):
   assert row[k]==f[official_key], (f["filename"],k,row.get(k),f.get(official_key))
  assert normalize_dist(row.get("requires_dist"))==normalize_dist(pkg["package"].get("requires_dist")), f["filename"]
  assert row.get("wheel_tags")==["-".join(f["wheel_tags"])], f["filename"]
  if not all(f["candidate_comparison"].values()): mismatch.append(f["filename"])
 assert mismatch==["psutil-5.9.8-cp36-abi3-macosx_10_9_x86_64.whl"], mismatch
 target=mismatch[0]
 oldrow=next(x for x in old["unique_download_set"] if x["filename"]==target)
 row=byfile[target]
 assert oldrow["url"]!=row["url"] and row["url"]==corr["official_url"]
 assert corr["original_candidate_url"]==oldrow["url"] and corr["corrected_artifact_id"]==row["artifact_id"]
 assert row["artifact_id"]==aid(row["url"],row["sha256"])
 assert corr["sha256"]==row["sha256"] and corr["size_bytes"]==row["size_bytes"]
 def remap(v):
  if isinstance(v,dict): return {k:remap(x) for k,x in v.items()}
  if isinstance(v,list): return [remap(x) for x in v]
  return corr["corrected_artifact_id"] if v==corr["old_artifact_id"] else v
 assert p["target_closures"]==remap(old["target_closures"])
 assert {k:v for k,v in p["download_totals"].items() if k not in {"bytes_basis","unique_artifact_bytes_status"}}=={k:v for k,v in old["download_totals"].items() if k!="bytes_basis"}
 assert "UNVERIFIED_PRIMARY_SOURCE" in p["download_totals"]["bytes_basis"]
 assert p["download_totals"]["unique_artifact_bytes_status"]=="UNVERIFIED_TOTAL_DUE_TO_OPENSSL_EXACT_SIZE"
 assert sum(x["size_bytes"] for x in p["unique_download_set"])==143586224
 assert all(x["downloaded"] is False for x in p["unique_download_set"])
 openssl=load(HERE/"openssl-4.0.2-official-fields.json")["artifact"]
 assert openssl["exact_size_status"]=="UNVERIFIED_PRIMARY_SOURCE_API_ENDPOINT_INACCESSIBLE"
 open_row=next(x for x in p["unique_download_set"] if x["filename"]=="openssl-4.0.2.tar.gz")
 assert open_row["size_bytes_primary_verified"] is False
 assert open_row["size_bytes_evidence_status"]=="NON_PRIMARY_ONLY_OFFICIAL_API_ENDPOINT_INACCESSIBLE"
 print(json.dumps({"status":"PASS_OFFLINE_CORRECTED_CANDIDATE_VIEW","official_pypi_files":10,"corrected_candidate_url_mismatches":1,"mismatch_explicitly_detected_in_immutable_candidate":True,"unique_download_pairs":61,"unique_download_bytes":143586224,"target_totals_unchanged":True,"downloaded":False,"installed":False,"built":False,"network_accessed":False,"openssl_exact_bytes":"UNVERIFIED_PRIMARY_SOURCE"},indent=2))
if __name__=="__main__": main()
