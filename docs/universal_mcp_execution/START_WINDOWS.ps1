param([string]$PythonExe = "", [ValidateSet("auto","git","archive")][string]$Method="auto")
$ErrorActionPreference="Stop"
Set-Location -LiteralPath $PSScriptRoot
$Prefix=@()
if (!$PythonExe) {
  if (Get-Command py -ErrorAction SilentlyContinue) { $PythonExe="py"; $Prefix=@("-3") }
  elseif (Get-Command python -ErrorAction SilentlyContinue) { $PythonExe="python" }
  else { throw "A trusted Python 3.10+ interpreter is required. No installation or system change was performed." }
}
& $PythonExe @Prefix tools/verify_package.py
if ($LASTEXITCODE -ne 0) { throw "Workpack integrity failed" }
& $PythonExe @Prefix tools/bootstrap.py --method $Method
if ($LASTEXITCODE -ne 0) { throw "Pinned source recovery failed; do not use partial output" }
& $PythonExe @Prefix tools/restore_candidates.py --repo repository
if ($LASTEXITCODE -ne 0) { throw "Candidate recovery failed; no candidate was promoted" }
& $PythonExe @Prefix tools/init_state.py --repo repository
if ($LASTEXITCODE -ne 0) { throw "Continuation initialization failed; existing state was not replaced" }
Write-Output "Source restored only; no COMSOL started and no runtime authority restored. Follow MASTER_GOAL.md."
