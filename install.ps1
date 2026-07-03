# Jarvis installer — Windows wrapper (PowerShell 5.1+).
# Forwards to scripts/install/installer.py. Default is dry-run.
#
# Examples:
#   .\install.ps1                      # dry-run plan
#   .\install.ps1 -Apply               # perform install  (NOT --apply)
#   .\install.ps1 -Rollback <path>     # restore from backup
#
# Epic #335 / M1 #336.

[CmdletBinding()]
param(
    [switch]$Apply,
    [string]$Target,
    [string]$Manifest,
    [string]$Rollback,
    [switch]$SkipEnv,
    [switch]$SkipHealthCheck,
    [switch]$FixEncoding
)

# Note: NOT setting $ErrorActionPreference = "Stop" globally.
# Under "Stop", a native exe writing to stderr (e.g. installer.py's
# "quarantined orphan ..." messages) triggers a NativeCommandError that
# terminates the script *before* `exit $LASTEXITCODE` runs — leaving the
# install half-applied (.jarvis-version not written). The python child
# process owns its own exit code; we propagate it explicitly below.
# Get-Command calls below pass -ErrorAction Stop locally where needed.

# Guard: detect GNU-style double-dash args (e.g. --apply) which PowerShell won't
# bind correctly and will silently mis-route to $Target as a positional string.
foreach ($a in @($Apply, $Target, $Manifest, $Rollback)) {
    if ($a -is [string] -and $a -match '^--') {
        Write-Error "PowerShell uses single-dash params. Replace '$a' with '-$($a.TrimStart('-'))'. Example: -Apply instead of --apply"
        exit 1
    }
}

$repoRoot = Split-Path -Parent $MyInvocation.MyCommand.Path

$pyArgs = @("$repoRoot\scripts\install\installer.py")
if ($Apply)            { $pyArgs += "--apply" }
if ($SkipEnv)          { $pyArgs += "--skip-env" }
if ($SkipHealthCheck)  { $pyArgs += "--skip-health-check" }
if ($FixEncoding)      { $pyArgs += "--fix-env-encoding" }
if ($Target)           { $pyArgs += @("--target", $Target) }
if ($Manifest)         { $pyArgs += @("--manifest", $Manifest) }
if ($Rollback)         { $pyArgs += @("--rollback", $Rollback) }

$py = (Get-Command python -ErrorAction SilentlyContinue)
if (-not $py) { $py = Get-Command python3 -ErrorAction Stop }

& $py.Source @pyArgs
exit $LASTEXITCODE
