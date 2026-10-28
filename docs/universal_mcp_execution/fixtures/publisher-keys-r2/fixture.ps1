$ErrorActionPreference='Stop';Set-StrictMode -Version Latest
function OldKeys($Obj,[string[]]$Expected) {
 if ($null -eq $Obj -or $Obj -is [array] -or $Obj -is [string] -or $Obj -is [bool] -or $Obj -is [ValueType]) { throw 'object required' }
 $Names=@($Obj.PSObject.Properties.Name)
 if ($Names.Count -ne $Expected.Count) { throw 'field count' }
 foreach($N in $Expected){if (@($Names | Where-Object { $_ -ceq $N }).Count -ne 1) { throw 'field mismatch' }}
}
function NewKeys($Obj,[string[]]$Expected) {
 if ($null -eq $Obj -or $Obj -is [array] -or $Obj -is [string] -or $Obj -is [bool] -or $Obj -is [ValueType]) { throw 'object required' }
 $Names=@(foreach ($Property in $Obj.PSObject.Properties) { $Property.Name })
 if ($Names.Count -ne $Expected.Count) { throw 'field count' }
 foreach($N in $Expected){if (@($Names | Where-Object { $_ -ceq $N }).Count -ne 1) { throw 'field mismatch' }}
}

function Yes([scriptblock]$F) { & $F; [void]0 }
function No([scriptblock]$F) { $Failed=$false; try { & $F } catch { $Failed=$true }; if (-not $Failed) { throw 'expected refusal' } }
$Empty=[pscustomobject]@{};$One=[pscustomobject]@{a=1};$Two=[pscustomobject]@{a=1;b=2}
$OldId=$null;try { OldKeys $Empty @() } catch { $OldId=$_.FullyQualifiedErrorId }
if ($null -eq $OldId -or -not $OldId.Contains('PropertyNotFoundStrict')) { throw 'old empty error mismatch' }
Yes { OldKeys $One @('a') }; Yes { NewKeys $Empty @() }; Yes { NewKeys $One @('a') }; Yes { NewKeys $Two @('a','b') }
No { NewKeys $One @('a','b') }; No { NewKeys $Two @('a') }; No { NewKeys $null @() }; No { NewKeys @(1,2) @() }; No { NewKeys 'x' @() }; No { NewKeys $One @('A') }
[Console]::Out.WriteLine('FIXTURE_OK')
