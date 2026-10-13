set -e
setopt noclobber
test ! -e /private/tmp/comsol-mcp-w24-cure-setup-current11-20261012-r1
test ! -L /private/tmp/comsol-mcp-w24-cure-setup-current11-20261012-r1
test ! -e /Volumes/SSD/COMSOL_MCP_FULL_PROJECT_CONTINUATION_V2/repository/docs/full_project_continuation_v2/evidence/w24_cure_preflight_current11/native-run-current11-001
test ! -L /Volumes/SSD/COMSOL_MCP_FULL_PROJECT_CONTINUATION_V2/repository/docs/full_project_continuation_v2/evidence/w24_cure_preflight_current11/native-run-current11-001
test ! -e /Volumes/SSD/COMSOL_MCP_FULL_PROJECT_CONTINUATION_V2/repository/docs/full_project_continuation_v2/evidence/w24_cure_preflight_current11/native-launch-capture-current11-001
test ! -L /Volumes/SSD/COMSOL_MCP_FULL_PROJECT_CONTINUATION_V2/repository/docs/full_project_continuation_v2/evidence/w24_cure_preflight_current11/native-launch-capture-current11-001
mkdir /Volumes/SSD/COMSOL_MCP_FULL_PROJECT_CONTINUATION_V2/repository/docs/full_project_continuation_v2/evidence/w24_cure_preflight_current11/native-launch-capture-current11-001
cd /Volumes/SSD/COMSOL_MCP_FULL_PROJECT_CONTINUATION_V2/local-preservation/w24-cure-preflight-20261012-current11-config-projection/repository
export PYTHONPATH=/Volumes/SSD/COMSOL_MCP_FULL_PROJECT_CONTINUATION_V2/local-preservation/w24-cure-preflight-20261012-current11-config-projection/repository
export TMPDIR=/private/tmp
unset CODEX_NETWORK_GUARD_LOG CODEX_NETWORK_GUARD_LABEL CODEX_NETWORK_GUARD_RUN_ID
exec /private/tmp/comsol-v2-runtime-20261011-new/comsol-mcp-recovery-runtime-20261004-001/python312/bin/python /Volumes/SSD/COMSOL_MCP_FULL_PROJECT_CONTINUATION_V2/local-preservation/w24-cure-preflight-20261012-current11-config-projection/repository/tools/run_native_w24_cure_preflight.py --work /private/tmp/comsol-mcp-w24-cure-setup-current11-20261012-r1 --evidence /Volumes/SSD/COMSOL_MCP_FULL_PROJECT_CONTINUATION_V2/repository/docs/full_project_continuation_v2/evidence/w24_cure_preflight_current11/native-run-current11-001 --candidate-freeze /Volumes/SSD/COMSOL_MCP_FULL_PROJECT_CONTINUATION_V2/local-preservation/w24-cure-preflight-20261012-current11-config-projection/repository/docs/full_project_execution/w24/evidence/setup_candidate_20261012_current11/offline_candidate_freeze_current11.json --expected-candidate-sha256 7c329a6bc49556548c3a1939a87392547f0a9b84602cee33a60f6261e3d3b5d7 > /Volumes/SSD/COMSOL_MCP_FULL_PROJECT_CONTINUATION_V2/repository/docs/full_project_continuation_v2/evidence/w24_cure_preflight_current11/native-launch-capture-current11-001/launcher.stdout.bin 2> /Volumes/SSD/COMSOL_MCP_FULL_PROJECT_CONTINUATION_V2/repository/docs/full_project_continuation_v2/evidence/w24_cure_preflight_current11/native-launch-capture-current11-001/launcher.stderr.bin
