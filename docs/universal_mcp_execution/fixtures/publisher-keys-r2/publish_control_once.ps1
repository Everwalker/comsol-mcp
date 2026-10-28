# Main must externally pin this source and bind these data literals before use.
param([Parameter(Mandatory=$true)][ValidateSet('stage1','stage2')][string]$Phase)
$ErrorActionPreference='Stop'
Set-StrictMode -Version Latest
$ExpectedSpecPath=$null
$ExpectedSpecSha256=$null
$ExpectedPhase=$null
if ($null -eq $ExpectedSpecPath -or $null -eq $ExpectedSpecSha256 -or $null -eq $ExpectedPhase) { throw 'UNBOUND publisher' }
if ($Phase -cne $ExpectedPhase -or $ExpectedSpecSha256 -cnotmatch '^[0-9a-f]{64}$') { throw 'phase or spec pin' }
function Keys($Obj,[string[]]$Expected) {
 if ($null -eq $Obj -or $Obj -is [array] -or $Obj -is [string] -or $Obj -is [bool] -or $Obj -is [ValueType]) { throw 'object required' }
 $Names=@(foreach ($Property in $Obj.PSObject.Properties) { $Property.Name })
 if ($Names.Count -ne $Expected.Count) { throw 'field count' }
 foreach($N in $Expected){if (@($Names | Where-Object { $_ -ceq $N }).Count -ne 1) { throw 'field mismatch' }}
}
function Sha([byte[]]$Bytes) {
 $H=[Security.Cryptography.SHA256]::Create()
 try { return ([BitConverter]::ToString($H.ComputeHash($Bytes))).Replace('-','').ToLowerInvariant() } finally { $H.Dispose() }
}
function WinPath([string]$Path) {
 if ([string]::IsNullOrEmpty($Path) -or $Path -notmatch '^[A-Za-z]:\\' -or $Path.Substring(2).Contains(':') -or $Path.Contains([char]0)) { throw 'absolute local path required' }
 $Full=[IO.Path]::GetFullPath($Path)
 if (-not [string]::Equals($Full,$Path,[StringComparison]::OrdinalIgnoreCase)) { throw 'noncanonical path' }
 return $Full
}
function Under([string]$Root,[string]$Path) {
 $R=(WinPath $Root).TrimEnd([char]'\')+'\';$P=WinPath $Path
 if (-not $P.StartsWith($R,[StringComparison]::OrdinalIgnoreCase)) { throw 'outside scope' }
}
function Ref($R) {
 Keys $R @('path','bytes','sha256')
 if ($R.path -isnot [string] -or $R.sha256 -isnot [string] -or $R.sha256 -cnotmatch '^[0-9a-f]{64}$' -or -not ($R.bytes -is [int] -or $R.bytes -is [long]) -or [long]$R.bytes -le 0 -or [long]$R.bytes -gt 1073741824) { throw 'bad ref' }
 [void](WinPath $R.path)
}
function EqualRef($A,$B) { Ref $A;Ref $B;return ($A.path -ceq $B.path -and [long]$A.bytes -eq [long]$B.bytes -and $A.sha256 -ceq $B.sha256) }
function RequireOrdinaryParents([string]$P) {
 $D=[IO.Path]::GetDirectoryName((WinPath $P))
 while ($null -ne $D) {
  $Attr=[IO.File]::GetAttributes($D)
  if (($Attr -band [IO.FileAttributes]::Directory) -eq 0 -or ($Attr -band [IO.FileAttributes]::ReparsePoint) -ne 0) { throw 'unsafe parent' }
  $Next=[IO.Path]::GetDirectoryName($D);if ($Next -eq $D) { break };$D=$Next
 }
}
function ReadPinned($R) {
 Ref $R;RequireOrdinaryParents $R.path
 $Attr=[IO.File]::GetAttributes($R.path)
 if (($Attr -band ([IO.FileAttributes]::Directory -bor [IO.FileAttributes]::ReparsePoint)) -ne 0) { throw 'unsafe input' }
 $F=[IO.File]::Open($R.path,[IO.FileMode]::Open,[IO.FileAccess]::Read,[IO.FileShare]::Read)
 try {
  if ($F.Length -ne [long]$R.bytes) { throw 'input size' }
  $B=New-Object byte[] ([int]$R.bytes);$I=0
  while($I -lt $B.Length){$N=$F.Read($B,$I,$B.Length-$I);if ($N -le 0) { throw 'short input' };$I+=$N}
  if ($F.ReadByte() -ne -1 -or $F.Length -ne $B.Length -or (Sha $B) -cne $R.sha256) { throw 'input drift' }
  return ,$B
 } finally { $F.Dispose() }
}
function ParseStrict([byte[]]$Bytes) {
 $Text=(New-Object Text.UTF8Encoding($false,$true)).GetString($Bytes)
 $Tokens=[regex]::Matches($Text,'"(?:\\.|[^"\\])*"|[{}\[\]:,]')
 $Stack=New-Object 'System.Collections.Generic.Stack[object]'
 for($I=0;$I -lt $Tokens.Count;$I++) {
  $T=$Tokens[$I].Value
  if ($T -ceq '{') { $Stack.Push((New-Object 'System.Collections.Generic.HashSet[string]' ([StringComparer]::Ordinal))) }
  elseif ($T -ceq '[') { $Stack.Push('array') }
  elseif ($T -ceq '}' -or $T -ceq ']') { if ($Stack.Count -eq 0) { throw 'JSON nesting' };[void]$Stack.Pop() }
  elseif ($T.StartsWith('"') -and $I+1 -lt $Tokens.Count -and $Tokens[$I+1].Value -ceq ':') {
   if ($Stack.Count -eq 0 -or $Stack.Peek() -isnot [System.Collections.Generic.HashSet[string]]) { throw 'JSON key position' }
   $K=$T.Substring(1,$T.Length-2)
   if ($K -cnotmatch '^[A-Za-z_][A-Za-z0-9_]*$' -or -not $Stack.Peek().Add($K)) { throw 'duplicate or noncanonical JSON key' }
  }
 }
 if ($Stack.Count -ne 0) { throw 'JSON nesting' }
 return ($Text | ConvertFrom-Json)
}
function AssertMissing([string]$Path) {
 try { [void][IO.File]::GetAttributes($Path) } catch {
  $E=$_.Exception
  while ($null -ne $E.InnerException) { $E=$E.InnerException }
  if ($E -is [IO.FileNotFoundException] -or $E -is [IO.DirectoryNotFoundException]) { return }
  throw
 }
 throw 'existing target or temporary'
}
function SameDirectory([string]$A,[string]$B) { return [string]::Equals([IO.Path]::GetDirectoryName($A),[IO.Path]::GetDirectoryName($B),[StringComparison]::OrdinalIgnoreCase) }
$SpecInfo=[IO.FileInfo]::new((WinPath $ExpectedSpecPath))
if (-not $SpecInfo.Exists -or $SpecInfo.Length -le 0 -or $SpecInfo.Length -gt 1048576) { throw 'spec missing or oversized' }
$SpecRef=[pscustomobject]@{path=$ExpectedSpecPath;bytes=$SpecInfo.Length;sha256=$ExpectedSpecSha256}
$SpecRaw=ReadPinned $SpecRef
$Spec=ParseStrict $SpecRaw
Keys $Spec @('schema','phase','control_root','nonce','task','ready','static_refs','files')
if ($Spec.schema -cne 'normal-control-publication/1' -or $Spec.phase -cne $Phase -or $Spec.nonce -isnot [string] -or $Spec.nonce -cnotmatch '^[0-9a-f]{32}$') { throw 'spec identity' }
$Root=WinPath $Spec.control_root;Ref $Spec.task;Ref $Spec.ready;Under $Root $Spec.task.path;Under $Root $Spec.ready.path
$TaskRaw=ReadPinned $Spec.task;$ReadyRaw=ReadPinned $Spec.ready
$Task=ParseStrict $TaskRaw;$Ready=ParseStrict $ReadyRaw
if ($Task.nonce -cne $Spec.nonce -or $Ready.nonce -cne $Spec.nonce) { throw 'task or READY nonce' }
if ($Phase -ceq 'stage1') {
 Keys $Ready @('schema','nonce','task_sha256','actor')
 if ($Ready.schema -cne 'stage1-ready/1' -or $Ready.task_sha256 -cne $Spec.task.sha256 -or $Task.schema -cnotin @('normal-source-creator-task/1','normal-source-creator-task/2') -or $Task.parent -cne $Root -or $Task.ready_path -cne $Spec.ready.path) { throw 'stage1 READY' }
 Keys $Spec.static_refs @()
 $Roles=@('go')
} else {
 Keys $Ready @('schema','nonce','actor')
 if ($Ready.schema -cne 'normal-materialization-ready/1' -or $Task.schema -cne 'normal-materialization-task/1' -or $Task.root -cne $Root -or $Task.version -cne '0.1.15' -or $Task.scope -cne 'ORDINARY_USER_NORMAL_PREFIX' -or $Task.one_child -isnot [bool] -or $Task.one_child -ne $true -or $Task.no_breakaway -isnot [bool] -or $Task.no_breakaway -ne $true -or -not ($Task.stdout_limit -is [int] -or $Task.stdout_limit -is [long]) -or -not ($Task.stderr_limit -is [int] -or $Task.stderr_limit -is [long]) -or -not ($Task.runtime_seconds -is [int] -or $Task.runtime_seconds -is [long]) -or $Task.stdout_limit -ne 2097152 -or $Task.stderr_limit -ne 2097152 -or $Task.runtime_seconds -ne 120) { throw 'stage2 READY' }
 Keys $Spec.static_refs @('task','intent','driver','lock','wheel_manifest','bundle')
 if (-not (EqualRef $Spec.static_refs.task $Spec.task)) { throw 'stage2 static task' }
 foreach($Name in @('task','intent','driver','lock','wheel_manifest','bundle')) { Ref $Spec.static_refs.$Name;Under $Root $Spec.static_refs.$Name.path;[void](ReadPinned $Spec.static_refs.$Name) }
 $Roles=@('activation','go','descriptor')
}
if ($null -eq $Ready.actor -or $Ready.actor -is [string] -or $Ready.actor -is [array] -or $Ready.actor -is [ValueType]) { throw 'READY actor' }
if ($Spec.files -isnot [array] -or $Spec.files.Count -ne $Roles.Count) { throw 'publication count' }
$Seen=New-Object 'System.Collections.Generic.HashSet[string]' ([StringComparer]::OrdinalIgnoreCase)
foreach($Existing in @($Spec.task.path,$Spec.ready.path,$ExpectedSpecPath)){if (-not $Seen.Add((WinPath $Existing))) { throw 'input collision' }}
$Payload=@{}
for($I=0;$I -lt $Roles.Count;$I++) {
 $X=$Spec.files[$I];Keys $X @('role','target','temporary','bytes','sha256','payload_base64')
 if ($X.role -cne $Roles[$I] -or $X.target -isnot [string] -or $X.temporary -isnot [string] -or $X.payload_base64 -isnot [string] -or $X.sha256 -isnot [string] -or $X.sha256 -cnotmatch '^[0-9a-f]{64}$' -or -not ($X.bytes -is [int] -or $X.bytes -is [long]) -or [long]$X.bytes -le 0 -or [long]$X.bytes -gt 1048576) { throw 'publication fields' }
 Under $Root $X.target;Under $Root $X.temporary
 if (-not (SameDirectory $X.target $X.temporary)) { throw 'different-directory temp' }
 RequireOrdinaryParents $X.target;RequireOrdinaryParents $X.temporary
 if (-not $Seen.Add((WinPath $X.target)) -or -not $Seen.Add((WinPath $X.temporary))) { throw 'publication collision' }
 AssertMissing $X.target;AssertMissing $X.temporary
 $B=[Convert]::FromBase64String($X.payload_base64)
 if ($B.Length -ne [long]$X.bytes -or (Sha $B) -cne $X.sha256) { throw 'payload mismatch' }
 $Payload[$X.role]=$B
}
if ($Phase -ceq 'stage1' -and $Spec.files[0].target -cne $Task.go_path) { throw 'stage1 GO target' }
$Go=ParseStrict ($Payload['go'])
Keys $Go @('schema','nonce','task_sha256','ready_sha256','status')
$GoSchema=if ($Phase -ceq 'stage1') {'stage1-go/1'} else {'normal-materialization-go/1'}
if ($Go.schema -cne $GoSchema -or $Go.nonce -cne $Spec.nonce -or $Go.task_sha256 -cne $Spec.task.sha256 -or $Go.ready_sha256 -cne $Spec.ready.sha256 -or $Go.status -cne 'GO_ONCE') { throw 'GO mismatch' }
if ($Phase -ceq 'stage2') {
 $A=ParseStrict ($Payload['activation'])
 Keys $A @('schema','task','intent','ready','go','driver','lock','wheel_manifest','bundle','issued_utc','deadline_utc','permissions')
 if ($A.schema -cne 'normal-materialization-activation/1') { throw 'activation schema' }
 foreach($Name in @('task','intent','driver','lock','wheel_manifest','bundle')) { if (-not (EqualRef $A.$Name $Spec.static_refs.$Name)) { throw 'static ref drift' } }
 if (-not (EqualRef $A.ready $Spec.ready)) { throw 'activation READY' }
 $GoFile=$Spec.files[1];$GoRef=[pscustomobject]@{path=$GoFile.target;bytes=$GoFile.bytes;sha256=$GoFile.sha256}
 if (-not (EqualRef $A.go $GoRef)) { throw 'activation GO' }
 Keys $A.permissions @('one_child','pip_once','network','native_product')
 if ($A.permissions.one_child -isnot [bool] -or $A.permissions.one_child -ne $true -or $A.permissions.pip_once -isnot [bool] -or $A.permissions.pip_once -ne $true -or $A.permissions.network -isnot [bool] -or $A.permissions.network -ne $false -or $A.permissions.native_product -isnot [bool] -or $A.permissions.native_product -ne $false) { throw 'permission scope' }
 $D=ParseStrict ($Payload['descriptor']);Keys $D @('schema','activation')
 $Af=$Spec.files[0];$Ar=[pscustomobject]@{path=$Af.target;bytes=$Af.bytes;sha256=$Af.sha256}
 if ($D.schema -cne 'normal-stage2-activation-descriptor/1' -or -not (EqualRef $D.activation $Ar)) { throw 'descriptor mismatch' }
}
# Recheck original control inputs immediately before the first publication effect.
[void](ReadPinned $SpecRef);[void](ReadPinned $Spec.task);[void](ReadPinned $Spec.ready)
if ($Phase -ceq 'stage2') { foreach($Name in @('intent','driver','lock','wheel_manifest','bundle')) { [void](ReadPinned $Spec.static_refs.$Name) } }
# No later file is opened if an earlier operation fails. No delete, overwrite or retry.
foreach($X in $Spec.files) {
 [void](ReadPinned $SpecRef);[void](ReadPinned $Spec.task);[void](ReadPinned $Spec.ready)
 if ($Phase -ceq 'stage2') { foreach($Name in @('intent','driver','lock','wheel_manifest','bundle')) { [void](ReadPinned $Spec.static_refs.$Name) } }
 $F=[IO.File]::Open($X.temporary,[IO.FileMode]::CreateNew,[IO.FileAccess]::Write,[IO.FileShare]::None)
 try { $B=$Payload[$X.role];$F.Write($B,0,$B.Length);if ($F.Position -ne $B.Length -or $F.Length -ne $B.Length) { throw 'short write' };$F.Flush($true) } finally { $F.Dispose() }
 [IO.File]::Move($X.temporary,$X.target)
}
