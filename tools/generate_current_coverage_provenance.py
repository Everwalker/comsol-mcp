import ast, collections, hashlib, json, pathlib, sys

PRODUCT_SOURCE = "958970b605b746d2593dd5a3c65965b960864d46"
COVERAGE_SHA256 = "00af26d7ab492a49521109a4c4a9b2a79a36e3fe19cbbafbd268e3922b095858"
SOURCE_SHA256 = {'comsol_mcp/_artifact_store.py': 'c7ee8c61036176dad499769fa3274f610d956e34d53d8a6bffd61e3afff253bd', 'comsol_mcp/_control_daemon.py': '4459e1e790ea4f47e1ee532dd71d3b08887e74d55ede4e559ddccf50a6feaa4c', 'comsol_mcp/_desktop_macos.py': 'f1f3b3bebf6029e00d6f05ec40285ed9dbba0fb16e0b28198231841128ae84e6', 'comsol_mcp/_desktop_platforms.py': '4c50c4596cbc8d79dd610d66cada1deb38e400c607a5bb04b59f1766a9c76059', 'comsol_mcp/_desktop_selector_profile.py': 'c29d6a439e3d2fb1e2b0e55b04486335d3ec22c15527e16d3e72ef67855b6c6c', 'comsol_mcp/_desktop_service.py': '00691307ad6b2c780f7086b4a306b4adbb5360e986754c3e9e97e2a4ea38dcf1', 'comsol_mcp/_desktop_windows.py': '10f8105e9b93aff5a717e294931d8b36680bf8560a0d21e43e1fb9c295ee5256', 'comsol_mcp/_g2_checkpoint_diff.py': 'a331bae7b145f5d35194ab543e13d594641d5323bae97d3a3e33d0b522c77cb7', 'comsol_mcp/_g2_checkpoint_ops.py': 'b566d9132fd87c103e1179811eb4c554af2389741b91064f223faeaf10d4c08d', 'comsol_mcp/_g2_engine.py': '71f1e21bb8219d4be9f61a6a850adeb1af5161de294ff17ed486798c37abcbb9', 'comsol_mcp/_g2_model_compare.py': '96a5750d8c7760d9a20244585e0a9b0d97a5b04840e690a8283d25dfd4e83597', 'comsol_mcp/_g2_public_api.py': 'a9b1a2a7dcaf94860d424b66fb01d468f5bd2f9a50201bfe2befc40e9b243505', 'comsol_mcp/_g2_registry.py': 'c8480d773315881da15f2ee1787f951db421b0cb0a2133c9645e0d09d6c56040', 'comsol_mcp/_g2_tools.py': 'd62451b60a529d146c91849f47b9f51be6d19788962131e3ea24386e7a2a04f9', 'comsol_mcp/_g2_transactions.py': 'd8c5d84d58f205627df5e31489360b4ef2e7759d7ef67b916dc4fc5318cfba80', 'comsol_mcp/_g3_definition_validation.py': '914756c51449d605fe98d1e6602793fa1b9a0f99eaef529f0097ed38dfb885ce', 'comsol_mcp/_g3_exports.py': '519625eb060ab847fc76ec2b44c51679dfcdfe697a30bb82faba61bd3841cd7c', 'comsol_mcp/_g3_managed_models.py': 'ed9ef412f42a050f70fb492ad835bdf8244b76d8738a11b7331393dae95a31a9', 'comsol_mcp/_g3_metrics.py': '89e9297c13ac8f42c5260c61e723ffbdb24d3a46858b0d9abf8729ac62241d87', 'comsol_mcp/_g3_model_delivery.py': '9c6aa7c1de31e325dca21f49bec1df9351a642952869b215f47e50b092a65e80', 'comsol_mcp/_g3_model_domains.py': 'ea789f9d2e48e1e32254d2f2c346d2c27440bfb6f6e2f8af6a54469172210df8', 'comsol_mcp/_g3_ops.py': 'd456796622636eeb6eab7b0176901892effc84261ae0d699a6dd801dbb31287a', 'comsol_mcp/_g3_parameter_io.py': '6a447e14bd3ae5b03b268562769593d0e530000284463729ddb95e4487937369', 'comsol_mcp/_g3_results.py': 'f37353ee6419cffc45bb940cdcc0321a714e05727b4a4dbdd0eff520e707706f', 'comsol_mcp/_g3_runtime.py': '663af8ffdd7b6d99a915acfb4bad768a5c2227e18088511a375dcb7d7e75ae52', 'comsol_mcp/_g3_solver_ops.py': 'bf8188154c68d08dcc64c23a11004d9360267951ccabcafe608cd0cdbcd0ee27', 'comsol_mcp/_g3_w13.py': '9556b74bf44e5b0b0849e732c34f407210fa60e7c6a95b486c2a5e1b40111928', 'comsol_mcp/_g3_w14.py': 'a6cfbf3cbd330701f04f05341bec0b62e08bb9c459a4b9e4ed57fbeb504aa8d6', 'comsol_mcp/_g3_w15.py': 'c267190f280c8f712ed177d0f3be2382e0dd8250479476ba4ebb9bd0a2091ff1', 'comsol_mcp/_g3_w16.py': '1242cd85660cb59baf1d34b7d5bd08a025ef9cbd92f34bf9264c188d6dbc0708', 'comsol_mcp/_g3_w18.py': '378376fe50e4dcf14a55a7e1b03ed807ef29b1472ce342475044b83893ed09fd', 'comsol_mcp/_g3_w20_validation.py': '77ec0f38f2da8b3d7cb53f2fe72bcb247d3d5568e027c19cda515e98a67aafc3', 'comsol_mcp/_g3_w21.py': 'bf567f34257d58e931f457d211182fddf04f1d94c6c0771fb4c69833fd2886a6', 'comsol_mcp/_java_worker.py': '50b6cc49e566d642035f3fc091133eea36ea1ed4ae0dd0ef9a588c81360bb286', 'comsol_mcp/_managed_backend.py': '23ace5c66e545786b0ee3ea2cde3832ec346c2e8c5d82ad6c14ce50ac36af24a', 'comsol_mcp/_mcp_gateway.py': 'ff40a6f0f4a472a4950bb698c3d8a0b05330ed4ffa186538cffcb3edccade16f', 'comsol_mcp/_operation_store.py': 'e5113c45da1ebbfbe9a52d77b8bf0635c4347bea76ca43b865a969c4146e486a', 'comsol_mcp/_probe_manage.py': '447b45d5bc3fdcee6a2ec3935f32a140d18fa1b02af23814fb92ef0027adcf1e', 'comsol_mcp/_w23_basis_v2_results.py': 'ef06db7a645dc79ec6a9898d47952fbec75ea27b046221b026a5825135ea6eef', 'comsol_mcp/_w23_results.py': 'fae3ddcc4abab9866eaa3a667b418ef2fe8a70c4fba651bc62ab323316c991ca', 'comsol_mcp/data/g2/02_ACTION_CATALOG.json': 'c49201c72932ac87b799da7bdbea9a982912e24bde9c3c9887c2916a1fea3b10', 'comsol_mcp/mcp_server.py': 'daa3972ca26b8e1ba72b07f3d346a1523b390bb556197aede0b8d58f4231a0a4', 'docs/comsol_mcp_design_v1/03_ACCEPTANCE.md': 'd6de6f68e2f7faf208522ebfb4faa7e0d2b564ee909ec02ff216ea3f1acdfa5e', 'docs/full_project_execution/USER_SCOPE_DECISIONS.json': '072b40bf06e0984a1b590977fd318e7a519a731f468ab85eed41a9caa191cf5f', 'docs/full_project_execution/state/ACCEPTANCE_MATRIX.json': 'e51cf517d55c9af9797d8bbeaa0615048167af0bad0ed80a3e5888578aa7b3a4', 'docs/full_project_execution/state/ACTION_COVERAGE.json': '7767bea658ffe76b6f6513c608c38d711e8c00d24b19104d037fb89f2e42b236', 'docs/universal_mcp_execution/ARCHITECTURE.md': '077fd2c15d081096d4368c647b9b937669d22002ced4b8e54eb9bc712e653060', 'docs/universal_mcp_execution/MASTER_GOAL.md': '4ff2c75ffc2a0478e8ddeb2d7b672623d394b88109e3445ba5773b2b9e0eecaa', 'docs/universal_mcp_execution/PIN.json': '963b9672988e7c397f730b0f37e6cce1ba1557f8c4a04592f0bff1e4d27d533e', 'docs/universal_mcp_execution/RELEASE_DOD.md': '939294d0dd0623b305b9e564a8f113d86b169957b7333d70dc9856b44ca5bdef', 'docs/universal_mcp_execution/SCOPE_RESET.md': '7d2b7b14fded3426f9da1163e3d4378d66f52ea5da4fc003f54e0c80947041ee', 'docs/universal_mcp_execution/config/TARGETS.json': 'd0ce39de90834c4cddb58b1238a8b68ca2aa2f39ead1d851a68a962651d4ad53', 'docs/universal_mcp_execution/evidence/unit-default/affected-junit.xml': 'ad08847147916ba0dc26632fcf721380c157b9a9e9d94157f871439fd9819798', 'docs/universal_mcp_execution/evidence/unit-default/first-regression.json': '81f4b75ab16b3deb67548383c42f4175921deb38586d7bfd81a202838439bad7', 'docs/universal_mcp_execution/evidence/unit-default/initial.json': '61515d5441281d5d2bba809da6515c80dfc1dd97a55c65d5e9e305940c011f6f', 'docs/universal_mcp_execution/evidence/unit-default/junit.xml': 'ec2af314a00b71b62dc2f222d516c9bef2285b6161c86ba66b7d016cd48fed75', 'tests/test_g3_results.py': 'a1fe81d10cd583584eef37c15b3bbf6b45621b0327b2e91c29b913048ba2058c', 'tests/test_interp_native_default_units.py': 'e92a4719b86fff7b7245da5140dca8f822cf1103e7611856d55b8e9cd2fd0290'}
RESTORE_RECEIPT = "docs/universal_mcp_execution/RESTORE_RECEIPT.json"

def sha(b): return hashlib.sha256(b).hexdigest()
def index(path):
 tree = ast.parse(path.read_bytes(), filename=str(path)); result = collections.defaultdict(list)
 def visit(body, prefix=""):
  for node in body:
   if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
    key = prefix + node.name; result[key].append((node.lineno, node.end_lineno))
    if isinstance(node, ast.ClassDef): visit(node.body, key + ".")
   elif isinstance(node, (ast.Assign, ast.AnnAssign)):
    for target in node.targets if isinstance(node, ast.Assign) else [node.target]:
     if isinstance(target, ast.Name): result[prefix + target.id].append((node.lineno, node.end_lineno))
 visit(tree.body); return result

def resolve(path, symbol, idx):
 module = pathlib.PurePosixPath(path).stem
 name = symbol[len(module)+1:] if symbol.startswith(module+".") else symbol
 spans = idx.get(name, [])
 if len(spans)==1: return spans[0], "EXACT_AST_SYMBOL"
 return None, "MISSING_AST_SYMBOL" if not spans else "AMBIGUOUS_AST_SYMBOL"

def generate(root):
 raw=(root/"docs/universal_mcp_execution/state/GENERAL_COVERAGE.json").read_bytes()
 if sha(raw)!=COVERAGE_SHA256: raise ValueError("historical coverage mismatch")
 ledger=json.loads(raw); rows=ledger["source_fingerprints"]
 if len(rows)!=59 or len(ledger["actions"])!=273 or len(ledger["tests_mapping"]["tests"])!=60 or len(ledger["tests_mapping"]["assertions"])!=427: raise ValueError("frozen contract mismatch")
 if {z["path"] for z in rows}-{RESTORE_RECEIPT}!=set(SOURCE_SHA256): raise ValueError("source roster mismatch")
 changes=[]; indices={}
 for row in rows:
  path=row["path"]
  if path==RESTORE_RECEIPT: continue
  b=(root/path).read_bytes(); digest=sha(b)
  if digest!=SOURCE_SHA256[path]: raise ValueError("product source mismatch: "+path)
  if digest!=row["sha256"]:
   changes.append({"path":path,"historical_sha256":row["sha256"],"current_sha256":digest,"current_bytes":len(b),"historical_overlay_bytes":"UNAVAILABLE_IN_PUBLIC_INPUTS"})
  if path.endswith(".py"): indices[path]=index(root/path)
 locations=collections.defaultdict(set)
 def walk(value, operation):
  if isinstance(value, dict):
   path=value.get("path"); symbol=value.get("symbol")
   if isinstance(path,str) and path in indices and isinstance(symbol,str) and ("line" in value or "end_line" in value):
    span,status=resolve(path,symbol,indices[path]); before=(value.get("line"),value.get("end_line")); after=span if span else (None,None)
    if not span or before[0]!=after[0] or (before[1] is not None and before[1]!=after[1]): locations[(path,symbol,before[0],before[1],after[0],after[1],status)].add(operation)
   if isinstance(path,str) and path in indices and isinstance(value.get("handler"),str) and "handler_line" in value:
    span,status=resolve(path,value["handler"],indices[path]); old=value.get("handler_line"); new=span[0] if span else None
    if old!=new or not span: locations[(path,value["handler"],old,None,new,span[1] if span else None,"HANDLER_"+status)].add(operation)
   for child in value.values(): walk(child,operation)
  elif isinstance(value,list):
   for child in value: walk(child,operation)
 for action in ledger["actions"]: walk(action,action["operation_id"])
 result=[]
 for key,operations in sorted(locations.items(),key=lambda item:repr(item[0])):
  path,symbol,oldline,oldend,newline,newend,status=key
  result.append({"path":path,"symbol":symbol,"historical_line":oldline,"historical_end_line":oldend,"current_line":newline,"current_end_line":newend,"resolution":status,"operation_ids":sorted(operations)})
 return {"schema":"general-coverage-portable-current-provenance/1","product_source_commit":PRODUCT_SOURCE,"historical_coverage_sha256":COVERAGE_SHA256,"tracked_source_count":58,"source_hashes":SOURCE_SHA256,"nonproduct_fingerprint":{"path":RESTORE_RECEIPT,"status":"NOT_IN_PRODUCT_NOT_READ"},"fingerprint_changes":changes,"handler_location_differences":result,"scope":"Pure source view, not software or native acceptance. AST current definition spans are not replacements for historical interior callsites. Historical overlay bytes unavailable in public inputs; no old diff claimed. No route, status or frozen count changes.","frozen_contract":{"actions":273,"tests":60,"subassertions":427,"status_changes":False}}

def main():
 if len(sys.argv)!=3: raise SystemExit("usage: generator.py ROOT OUTPUT")
 root=pathlib.Path(sys.argv[1]);output=pathlib.Path(sys.argv[2]);payload=(json.dumps(generate(root),indent=2,ensure_ascii=False)+"\n").encode()
 with output.open("xb") as f: f.write(payload)
 print(json.dumps({"bytes":len(payload),"sha256":sha(payload)},sort_keys=True))
if __name__=="__main__": main()
