<#
.SYNOPSIS
    Register (or re-register) a Windows Task Scheduler entry for the
    reactive-core wake_driver. Idempotent.

.DESCRIPTION
    Registers a task that runs `python -m agents.wake_driver` as a
    continuous daemon (LISTEN/NOTIFY loop). The task starts at user logon
    and restarts on crash -- a thin supervised wrapper, not a resident
    poller (the earlier NSSM jarvis-scheduler service was deliberately
    retired in #743; see scripts/install/uninstall-scheduler-service.ps1).
    The polling Orchestrator-Watcher predecessor was decommissioned in #1391.

    Sets JARVIS_PRINCIPAL=autonomous on the launched process per
    docs/security/agent-boundaries.md -- any headless launcher must set
    the principal explicitly so the detection chain doesn't default to
    `live`.

    Device guard: only registers on the designated routine host
    (config/device.json "name" == $env:JARVIS_SCHEDULED_HOST) unless
    -Force is passed -- wake_driver's single-driver invariant
    (agents/pid_sidecar.py) assumes exactly one supervised instance,
    and the routine host is the production target for always-on
    agents (matching Register-SandcastleTask.ps1).

    No device-specific paths/IPs/usernames are hardcoded here --
    RepoRoot and PythonExe are resolved from the local machine at
    registration time, same as Register-SandcastleTask.ps1.

.PARAMETER WatchdogSeconds
    Passed through to --watchdog-seconds (re-claim stale rows, wake-wait
    timeout). Default: 300, matching wake_driver.DEFAULT_STALE_AFTER_SECONDS.

.PARAMETER PythonExe
    Path to the Python interpreter. Defaults to the first `python3` on PATH.

.PARAMETER RepoRoot
    Repository root. Defaults to the repo containing this script.

.PARAMETER WhatIfOnly
    Print the planned scheduled task without registering.

.PARAMETER Force
    Allow registration on other devices (dev rehearsal).

.PARAMETER NoExecute
    Load function definitions only, then return -- no device guard, no
    Task Scheduler calls. For dot-sourcing from Pester tests
    (tests/install/Register-WakeDriver.Tests.ps1).

.EXAMPLE
    .\register-wake-driver.ps1
    # Registers Wake-Driver to run at user logon, watchdog=300s.

.EXAMPLE
    .\register-wake-driver.ps1 -WatchdogSeconds 120
    # Registers with a 120-second watchdog/wake-wait timeout.
#>

[CmdletBinding()]
param(
    [int]$WatchdogSeconds = 300,

    [string]$PythonExe,

    [string]$RepoRoot,

    [switch]$WhatIfOnly,

    [switch]$Force,

    [switch]$NoExecute
)

$ErrorActionPreference = 'Stop'

# ---------------------------------------------------------------------------
# Functions -- defined up front so -NoExecute can dot-source this script for
# Pester tests without triggering the device guard or a real Task Scheduler
# call (mirrors scripts/sandcastle/Register-SandcastleTask.ps1).
# ---------------------------------------------------------------------------

function Get-PowerShellExe {
    $pwshCmd = Get-Command pwsh -ErrorAction SilentlyContinue
    if ($pwshCmd) {
        return $pwshCmd.Source
    }
    return (Get-Command powershell -ErrorAction Stop).Source
}

function Format-WakeDriverActionArgs {
    param(
        [Parameter(Mandatory)][string]$PythonExe,
        [Parameter(Mandatory)][int]$WatchdogSeconds
    )

    # Single-quote the inner python path and double any literal single
    # quotes so an embedded quote in $PythonExe can't break out of the
    # PowerShell string -- nesting double quotes here breaks Windows argv
    # parsing instead (the bug fixed in this PR's own history).
    $escapedPythonExe = $PythonExe -replace "'", "''"
    # REACTIVE_CONCURRENCY_CAP=2 (#1390 AC8) pins the autonomous driver's
    # sandcastle concurrency lower than the interactive-session default (5) --
    # this is an unattended box, so a smaller blast radius per tick is safer.
    $innerCommand = "`$env:JARVIS_PRINCIPAL = 'autonomous'; `$env:REACTIVE_CONCURRENCY_CAP = '2'; & '$escapedPythonExe' -m agents.wake_driver --watchdog-seconds $WatchdogSeconds"

    return @(
        '-NoProfile',
        '-ExecutionPolicy', 'Bypass',
        '-Command', "`"$innerCommand`""
    )
}

if ($NoExecute) { return }

# ---------------------------------------------------------------------------
# Device guard -- routine host only, matching Sandcastle.
# ---------------------------------------------------------------------------

$expectedDevice = $env:JARVIS_SCHEDULED_HOST
$deviceJson     = Join-Path $PSScriptRoot '..\..\config\device.json'
$currentDevice  = $null
if (Test-Path $deviceJson) {
    try {
        $currentDevice = (Get-Content $deviceJson -Raw | ConvertFrom-Json).name
    } catch {
        Write-Warning "config/device.json present but unparsable: $($_.Exception.Message)"
    }
}

if (-not $Force -and $expectedDevice -and $currentDevice -ne $expectedDevice) {
    throw "Refusing to register on '$currentDevice' -- JARVIS_SCHEDULED_HOST designates '$expectedDevice'. Pass -Force for dev rehearsal."
}

# ---------------------------------------------------------------------------
# Resolve paths
# ---------------------------------------------------------------------------

if (-not $RepoRoot) {
    $RepoRoot = (Resolve-Path (Join-Path $PSScriptRoot '..\..')).Path
}

if (-not (Test-Path $RepoRoot)) {
    throw "RepoRoot '$RepoRoot' does not exist."
}

$driverModule = Join-Path $RepoRoot 'agents\wake_driver.py'
if (-not (Test-Path $driverModule)) {
    throw "agents/wake_driver.py not found under '$RepoRoot'. Is the repo root correct?"
}

if (-not $PythonExe) {
    $cmd = Get-Command python3 -ErrorAction SilentlyContinue
    if (-not $cmd) { $cmd = Get-Command python -ErrorAction Stop }
    $PythonExe = $cmd.Source
}

$pwshExe = Get-PowerShellExe

# ---------------------------------------------------------------------------
# Task identity
# ---------------------------------------------------------------------------

$taskName = 'Wake-Driver'

# ---------------------------------------------------------------------------
# Build the action -- continuous daemon, JARVIS_PRINCIPAL=autonomous set on
# the launched process (agent-boundaries.md headless-launcher requirement).
# ---------------------------------------------------------------------------

$argParts = Format-WakeDriverActionArgs -PythonExe $PythonExe -WatchdogSeconds $WatchdogSeconds

$action = New-ScheduledTaskAction -Execute $pwshExe `
    -Argument ($argParts -join ' ') `
    -WorkingDirectory $RepoRoot

# ---------------------------------------------------------------------------
# Trigger -- start at user logon, keep running.
# ---------------------------------------------------------------------------

$trigger = New-ScheduledTaskTrigger -AtLogOn

# ---------------------------------------------------------------------------
# Principal + settings -- restart on crash.
# ---------------------------------------------------------------------------

$principal = New-ScheduledTaskPrincipal -UserId $env:USERNAME -LogonType Interactive -RunLevel Limited

$settings = New-ScheduledTaskSettingsSet `
    -AllowStartIfOnBatteries `
    -DontStopIfGoingOnBatteries `
    -StartWhenAvailable `
    -MultipleInstances IgnoreNew `
    -RestartCount 5 `
    -RestartInterval ([timespan]::FromMinutes(1)) `
    -ExecutionTimeLimit ([timespan]::Zero)

# ---------------------------------------------------------------------------
# Register (idempotent)
# ---------------------------------------------------------------------------

$existing = Get-ScheduledTask -TaskName $taskName -ErrorAction SilentlyContinue

if ($WhatIfOnly) {
    Write-Host "[whatif] Would register task '$taskName'"
    Write-Host "         Execute   : $pwshExe"
    Write-Host "         Arguments : $($argParts -join ' ')"
    Write-Host "         WorkingDir: $RepoRoot"
    Write-Host "         Trigger   : AtLogOn (continuous, restart on crash)"
    Write-Host "         Existing  : $(if ($existing) { 'YES (would be replaced)' } else { 'no' })"
    return
}

if ($existing) {
    Write-Host "[register] Unregistering existing '$taskName' (idempotent)"
    Unregister-ScheduledTask -TaskName $taskName -Confirm:$false
}

$description = "Reactive-core wake_driver -- LISTEN/NOTIFY event loop, watchdog ${WatchdogSeconds}s. Restarts on crash/reboot. #1384."

Register-ScheduledTask -TaskName $taskName `
    -Action $action -Trigger $trigger `
    -Principal $principal -Settings $settings `
    -Description $description | Out-Null

Write-Host "[register] '$taskName' registered (continuous daemon, watchdog ${WatchdogSeconds}s)."
Write-Host "           Module : agents.wake_driver"
Write-Host "           Python : $PythonExe"
Write-Host "           Inspect: Get-ScheduledTask -TaskName '$taskName'"
Write-Host "           Invoke : Start-ScheduledTask -TaskName '$taskName'"
