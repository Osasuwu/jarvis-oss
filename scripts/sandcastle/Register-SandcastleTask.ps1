<#
.SYNOPSIS
    Register (or re-register) a Windows Task Scheduler entry for the sandcastle
    AFK loop or the quota probe. Idempotent -- running again replaces the
    existing entry.

.DESCRIPTION
    Two modes:

    **Sandcastle mode** (default, requires -Repo):
    Slices #545 (jarvis) and #546 (redrobot). The task fires Run-Sandcastle.ps1
    nightly inside the chosen safe-hours window:
        jarvis   : 18:00 → soft-stop at 01:00  (7h)
        redrobot : 01:00 → soft-stop at 08:00  (7h)
    Non-overlapping schedule covers all non-working hours (18 → 08) end-to-end.
    Earlier 22:00 / 02:00 defaults were Ollama-VRAM-contention-driven; with
    Tier 2-as-primary (#711) the local Ollama is bypassed in AFK runs, so the
    contention constraint no longer applies and the windows can grow.

    **Quota-probe mode** (-QuotaProbe):
    Registers Quota-Probe.ps1 as a recurring task every N minutes (default 30).
    Polls Claude Max weekly usage and broadcasts pressure state. See issue #635.

    The script must run on the routine host (decision 4890aa35 -- routine host = prod,
    Main = dev/test bench). On other devices the script refuses unless -Force.

.PARAMETER QuotaProbe
    Register the quota-probe recurring task instead of a sandcastle loop.
    Mutually exclusive with -Repo.

.PARAMETER QuotaProbeInterval
    Polling interval in minutes for quota-probe mode. Default 30.

.PARAMETER Repo
    Which sandcastle loop to wire up: jarvis or redrobot. Ignored in quota-probe mode.

.PARAMETER StartTime
    Override the default start time. Default per-repo:
        jarvis   = 18:00
        redrobot = 01:00

.PARAMETER WindowEnd
    Override the soft-stop boundary passed to Run-Sandcastle.ps1. Default per-repo:
        jarvis   = 01:00
        redrobot = 08:00

.PARAMETER Model
    Tier 0 Ollama model. Default flipped from qwen2.5-coder:14b → qwen3-coder:30b
    on 2026-05-14 to track the #538 benchmark winner. Both models still fail the
    real-Claude-Code tool_use fidelity probe (14b: markdown JSON fence;
    30b: Hermes-XML — see memory ollama_bench_must_measure_tool_use_fidelity),
    which is why AFK scheduled tasks use -Tier2AsPrimary to bypass the Ollama
    chain entirely; this parameter only matters for interactive smoke runs
    that opt back into the local chain.

.PARAMETER Tier1Model
    Tier 1 OOM-downgrade Ollama model. Defaults from #538: qwen2.5-coder:7b.

.PARAMETER Tier2Provider
    deepseek (default) routes AFK runs through DeepSeek's Anthropic-compatible
    endpoint as Tier 2 primary (paired with the auto-appended -Tier2AsPrimary
    on Run-Sandcastle.ps1). Pass an empty string to disable Tier 2 entirely
    (interactive Ollama-only smoke runs). Set to claude to use the Anthropic
    API key from .env instead (carries Max-subscription quota risk -- prefer
    deepseek for unattended cron).

    By default (-SubscriptionPrimary $false) Tier2Provider IS the AFK primary
    via the auto-appended -Tier2AsPrimary. Only when -SubscriptionPrimary is
    opted in does Tier2Provider step back to the credit-exhaustion fallback.

.PARAMETER SubscriptionPrimary
    #972. Default $false: DeepSeek (Tier 2) is the AFK primary. Opt in with
    -SubscriptionPrimary $true to register the Anthropic Max Agent-SDK credit
    as the primary tier (Opus 4.8 @ medium effort, bills the $100/mo credit via
    CLAUDE_CODE_OAUTH_TOKEN in .sandcastle/.env), DeepSeek as the fallback.
    Requires CLAUDE_CODE_OAUTH_TOKEN set on the host. 2026-06-17: kept OFF —
    Anthropic shelved the separate Agent-SDK automation quota at launch, so the
    credit path is dormant code until that lands. (Space form, not the colon
    `:$true` switch syntax — this is a [bool] param; `:$true` is a binding error
    on Windows PowerShell 5.1.)

.PARAMETER SubscriptionModel
    Subscription-tier model. Default claude-opus-4-8 (regular, NOT the 1M
    context variant — that carries extra billing).

.PARAMETER SubscriptionEffort
    Subscription-tier effort: low | medium | high | max. Default medium —
    model = task depth, effort = task width; AFK slices are single sharpened
    verticals, so depth-heavy / width-bounded.

.PARAMETER RepoRoot
    Filesystem path to the target repo. Defaults to the jarvis repo discovered
    from this script's own path (../../). For -Repo redrobot, pass the
    redrobot worktree path explicitly.

.PARAMETER WhatIf
    Print the planned scheduled task XML without registering.

.PARAMETER Force
    Allow registration on non-routine-host devices. For dev rehearsal only.

.EXAMPLE
    .\Register-SandcastleTask.ps1 -Repo jarvis
    # Registers Sandcastle-Jarvis daily at 18:00, soft-stop 01:00.

.EXAMPLE
    .\Register-SandcastleTask.ps1 -Repo redrobot -RepoRoot C:\repos\redrobot\redrobot
    # Registers Sandcastle-Redrobot daily at 01:00, soft-stop 08:00 (non-overlapping).

.EXAMPLE
    .\Register-SandcastleTask.ps1 -QuotaProbe
    # Registers Quota-Probe polling every 30 minutes.

.EXAMPLE
    .\Register-SandcastleTask.ps1 -QuotaProbe -QuotaProbeInterval 15
    # Registers Quota-Probe polling every 15 minutes.
#>
[CmdletBinding(DefaultParameterSetName = 'Sandcastle')]
param(
    [Parameter(ParameterSetName = 'QuotaProbe')]
    [switch]$QuotaProbe,

    [Parameter(ParameterSetName = 'QuotaProbe')]
    [int]$QuotaProbeInterval = 30,

    [Parameter(ParameterSetName = 'Sandcastle', Mandatory)]
    [ValidateSet('jarvis', 'redrobot')]
    [string]$Repo,

    [string]$StartTime,

    [string]$WindowEnd,

    [string]$Model = 'qwen3-coder:30b',

    [string]$Tier1Model = 'qwen2.5-coder:7b',

    [ValidateSet('', 'deepseek', 'claude')]
    [string]$Tier2Provider = 'deepseek',

    # #972: subscription Agent-SDK credit primary is an OPT-IN tier, default OFF.
    # 2026-06-17: Anthropic shelved the separate Agent-SDK automation quota at
    # launch, so the credit path stays in the code but is not used — DeepSeek
    # (Tier 2) is the default AFK primary again. Pass -SubscriptionPrimary $true
    # (space form — `:$true` colon syntax is a [bool]-binding error on PS 5.1)
    # to opt back in once the credit is real and CLAUDE_CODE_OAUTH_TOKEN is set.
    [bool]$SubscriptionPrimary = $false,

    [string]$SubscriptionModel = 'claude-opus-4-8',

    [ValidateSet('low', 'medium', 'high', 'max')]
    [string]$SubscriptionEffort = 'medium',

    [string]$RepoRoot,

    [int]$MaxIterations = 5,

    [switch]$WhatIfOnly,

    [switch]$Force,

    [switch]$NoExecute
)

$ErrorActionPreference = 'Stop'

# ---------------------------------------------------------------------------
# Testable helper functions (extracted for Pester coverage, #865).
# ---------------------------------------------------------------------------

function Get-ConfigDeviceName {
    param([string]$DeviceJsonPath)
    # Guard null/empty path. Under Windows PowerShell 5.1 a $null bound to
    # Test-Path -Path throws NullNotAllowed (an empty string instead returns
    # $false harmlessly). The `$DeviceJsonPath -and ...` short-circuit covers
    # both: an absent path means "no device.json" -> return null, never throw.
    if ($DeviceJsonPath -and (Test-Path $DeviceJsonPath)) {
        try {
            return (Get-Content $DeviceJsonPath -Raw | ConvertFrom-Json).name
        } catch {
            Write-Warning "config/device.json present but unparsable: $($_.Exception.Message)"
        }
    }
    return $null
}

function Get-SandcastleDefaults {
    param([string]$Repo)
    $defaults = @{
        jarvis   = @{ Start = '18:00'; End = '01:00'; TaskName = 'Sandcastle-Jarvis' }
        redrobot = @{ Start = '01:00'; End = '08:00'; TaskName = 'Sandcastle-Redrobot' }
    }
    return $defaults[$Repo]
}

function Get-PowerShellExe {
    $pwshCmd = Get-Command pwsh -ErrorAction SilentlyContinue
    if ($pwshCmd) {
        return $pwshCmd.Source
    }
    return (Get-Command powershell -ErrorAction Stop).Source
}

function Format-SandcastleActionArgs {
    param(
        [string]$WatchdogPath,
        [string]$Repo,
        [string]$Model,
        [string]$Tier1Model,
        [string]$Tier2Provider,
        [int]$MaxIterations,
        [string]$WindowEnd,
        [bool]$SubscriptionPrimary = $false,
        [string]$SubscriptionModel = 'claude-opus-4-8',
        [ValidateSet('low', 'medium', 'high', 'max')]
        [string]$SubscriptionEffort = 'medium'
    )
    $watchdogQuoted = '"' + $WatchdogPath + '"'
    $argParts = @(
        '-NoProfile',
        '-ExecutionPolicy', 'Bypass',
        '-File', $watchdogQuoted,
        '-Repo', $Repo,
        '-Model', $Model,
        '-MaxIterations', $MaxIterations,
        '-WindowEnd', $WindowEnd
    )
    if ($Tier1Model)    { $argParts += @('-Tier1Model', $Tier1Model) }
    if ($SubscriptionPrimary) {
        # #972: subscription Agent-SDK credit is primary for AFK runs. DeepSeek
        # stays wired as the credit-exhaustion fallback (-Tier2Provider) but NOT
        # -Tier2AsPrimary — subscription is senior; the watchdog supersedes
        # Tier-2-as-primary when both are present.
        $argParts += '-SubscriptionPrimary'
        $argParts += @('-SubscriptionModel', $SubscriptionModel)
        $argParts += @('-SubscriptionEffort', $SubscriptionEffort)
        if ($Tier2Provider) { $argParts += @('-Tier2Provider', $Tier2Provider) }
    } elseif ($Tier2Provider) {
        $argParts += @('-Tier2Provider', $Tier2Provider)
        # 2026-05-14: Tier 2 runs as primary for AFK scheduled tasks. Local Ollama
        # tiers fail the real-Claude-Code tool_use fidelity check on qwen2.5-coder:14b
        # and qwen3-coder:30b — see memory ollama_bench_must_measure_tool_use_fidelity.
        $argParts += '-Tier2AsPrimary'
    }
    return $argParts
}

function Format-QuotaProbeActionArgs {
    param(
        [string]$ProbeScript,
        [int]$CacheTTLMinutes
    )
    $probeQuoted = '"' + $ProbeScript + '"'
    return @(
        '-NoProfile',
        '-ExecutionPolicy', 'Bypass',
        '-File', $probeQuoted,
        '-CacheTTLMinutes', $CacheTTLMinutes
    )
}

if ($NoExecute) { return }

# ---------------------------------------------------------------------------
# Device guard -- pin scheduled tasks to a single designated host so that a
# multi-device setup doesn't register duplicate tasks. Set the env var
# JARVIS_SCHEDULED_HOST to the config/device.json `name` of the machine that
# should own scheduled tasks. When unset, any device may register (single-
# machine setups need no guard). Pass -Force to override.
# ---------------------------------------------------------------------------

$expectedDevice = $env:JARVIS_SCHEDULED_HOST
$deviceJson     = Join-Path $PSScriptRoot '..\..\config\device.json'
$currentDevice  = Get-ConfigDeviceName -DeviceJsonPath $deviceJson

if (-not $Force -and $expectedDevice -and $currentDevice -ne $expectedDevice) {
    throw "Refusing to register on '$currentDevice' -- JARVIS_SCHEDULED_HOST designates '$expectedDevice'. Pass -Force for dev rehearsal."
}

# Admin check up front -- Register-ScheduledTask with an S4U principal needs
# elevation. If we don't check here, the idempotent "unregister existing first"
# block below wipes the old task and then the Register call fails with
# "Access is denied", leaving the device with NO task at all.
$isAdmin = ([Security.Principal.WindowsPrincipal]([Security.Principal.WindowsIdentity]::GetCurrent())).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
if (-not $isAdmin -and -not $WhatIfOnly) {
    throw "Register-SandcastleTask must run from an elevated PowerShell. Re-launch as Administrator and retry."
}

# ---------------------------------------------------------------------------
# Quota-probe mode (#635)
# ---------------------------------------------------------------------------

if ($QuotaProbe) {
    if (-not $QuotaProbeInterval -or $QuotaProbeInterval -lt 1) {
        throw "QuotaProbeInterval must be >= 1."
    }

    $taskName = 'Quota-Probe'
    $probeScript = Join-Path $PSScriptRoot 'Quota-Probe.ps1'
    if (-not (Test-Path -LiteralPath $probeScript)) {
        throw "Quota-Probe.ps1 not found at '$probeScript'."
    }

    # Working directory: the jarvis repo root (same discovery as sandcastle mode)
    if (-not $RepoRoot) {
        $RepoRoot = (Resolve-Path (Join-Path $PSScriptRoot '..\..')).Path
    }

    $pwshExe = Get-PowerShellExe

    # Keep the cache TTL just over the probe interval so a probe is not served a
    # stale cache from the previous run (m2: at -QuotaProbeInterval 15 the 35-min
    # default meant nearly every run hit cache instead of re-probing).
    $cacheTtl = $QuotaProbeInterval + 5

    $argParts = Format-QuotaProbeActionArgs -ProbeScript $probeScript -CacheTTLMinutes $cacheTtl

    $action = New-ScheduledTaskAction -Execute $pwshExe `
        -Argument ($argParts -join ' ') `
        -WorkingDirectory $RepoRoot

    # Daily trigger with repetition interval (runs every N minutes all day)
    $startDt = (Get-Date).Date.AddMinutes(5)  # start 5 min past midnight
    if ($startDt -lt (Get-Date)) { $startDt = $startDt.AddDays(1) }

    # New-ScheduledTaskTriggerRepetition does not exist as a cmdlet on PS 5.1 / Win11
    # (#792). The supported way to attach a repetition pattern is the -Once parameter
    # set on New-ScheduledTaskTrigger, which builds the MSFT_TaskRepetitionPattern
    # CIM instance internally.
    #
    # Duration cap: Task Scheduler XML rejects durations exceeding PT24H × 9999
    # (P36500D = 100 years tripped this — Register-ScheduledTask error
    # "(10,29):Duration:P36500D"). 9999 days (~27 years) is the documented max and
    # is effectively indefinite for a self-retriggered task.
    $trigger = New-ScheduledTaskTrigger -Once -At $startDt `
        -RepetitionInterval ([timespan]::FromMinutes($QuotaProbeInterval)) `
        -RepetitionDuration ([timespan]::FromDays(9999))

    # S4U: run whether or not the user is logged on, and fire even when the screen
    # is locked (m1: LogonType Interactive skips the run on a locked workstation).
    $principal = New-ScheduledTaskPrincipal -UserId $env:USERNAME -LogonType S4U -RunLevel Limited

    $settings = New-ScheduledTaskSettingsSet `
        -AllowStartIfOnBatteries `
        -DontStopIfGoingOnBatteries `
        -StartWhenAvailable `
        -MultipleInstances IgnoreNew `
        -ExecutionTimeLimit ([timespan]::FromMinutes(15))

    $existing = Get-ScheduledTask -TaskName $taskName -ErrorAction SilentlyContinue

    if ($WhatIfOnly) {
        Write-Host "[whatif] Would register task '$taskName'"
        Write-Host "         Execute   : $pwshExe"
        Write-Host "         Arguments : $($argParts -join ' ')"
        Write-Host "         WorkingDir: $RepoRoot"
        Write-Host "         Interval  : Every ${QuotaProbeInterval}min"
        Write-Host "         Existing  : $(if ($existing) { 'YES (would be replaced)' } else { 'no' })"
        return
    }

    if ($existing) {
        Write-Host "[register] Unregistering existing '$taskName' (idempotent)"
        Unregister-ScheduledTask -TaskName $taskName -Confirm:$false
    }

    Register-ScheduledTask -TaskName $taskName `
        -Action $action -Trigger $trigger `
        -Principal $principal -Settings $settings `
        -Description "Quota pressure probe every ${QuotaProbeInterval}min. Issue #635." | Out-Null

    Write-Host "[register] '$taskName' scheduled every ${QuotaProbeInterval}min."
    Write-Host "           Inspect: Get-ScheduledTask -TaskName '$taskName'"
    Write-Host "           Trigger now: Start-ScheduledTask -TaskName '$taskName'"
    return
}

# ---------------------------------------------------------------------------
# Per-repo defaults
# ---------------------------------------------------------------------------

$defaults = Get-SandcastleDefaults -Repo $Repo

if (-not $StartTime) { $StartTime = $defaults.Start }
if (-not $WindowEnd) { $WindowEnd = $defaults.End }
$taskName = $defaults.TaskName

# Watchdog always lives in the jarvis repo (same dir as this script). Both
# jarvis and redrobot loops invoke the same parameterised watchdog -- it
# handles per-repo dispatch internally via Get-RepoRoot. This avoids
# duplicating the script across repos (epic #534 architectural commitment:
# "config identical between jarvis and redrobot").
$watchdog = Join-Path $PSScriptRoot 'Run-Sandcastle.ps1'
if (-not (Test-Path $watchdog)) {
    throw "Watchdog not found at '$watchdog'. Register-SandcastleTask must run from the jarvis repo's scripts/sandcastle directory."
}

# Working directory for the scheduled task -- cosmetic only (the watchdog
# does its own Push-Location to the resolved repo root). Default to the
# jarvis repo so logs / cwd-relative output land somewhere sane.
if (-not $RepoRoot) {
    $RepoRoot = (Resolve-Path (Join-Path $PSScriptRoot '..\..')).Path
}

if (-not (Test-Path $RepoRoot)) {
    throw "RepoRoot '$RepoRoot' does not exist."
}

# For redrobot, the watchdog uses REDROBOT_REPO_ROOT to find the redrobot
# worktree at runtime. Surface a missing var loudly here rather than at 02:00.
if ($Repo -eq 'redrobot') {
    $machineEnv = [System.Environment]::GetEnvironmentVariable('REDROBOT_REPO_ROOT', 'Machine')
    $userEnv    = [System.Environment]::GetEnvironmentVariable('REDROBOT_REPO_ROOT', 'User')
    $envVal     = if ($machineEnv) { $machineEnv } elseif ($userEnv) { $userEnv } else { $null }
    if (-not $envVal) {
        Write-Warning "REDROBOT_REPO_ROOT machine env var not set. The scheduled task will fail at runtime. Set it once:  setx /M REDROBOT_REPO_ROOT C:\repos\redrobot"
    } elseif (-not (Test-Path $envVal)) {
        Write-Warning "REDROBOT_REPO_ROOT='$envVal' does not exist on disk. Fix before 02:00."
    } else {
        Write-Host "[register] REDROBOT_REPO_ROOT='$envVal' resolves to a real path."
    }
}

# ---------------------------------------------------------------------------
# Build the action -- pwsh preferred, fallback to powershell.exe (5.1).
# ---------------------------------------------------------------------------

$pwshExe = Get-PowerShellExe

$argParts = Format-SandcastleActionArgs -WatchdogPath $watchdog `
    -Repo $Repo -Model $Model -Tier1Model $Tier1Model `
    -Tier2Provider $Tier2Provider -MaxIterations $MaxIterations `
    -WindowEnd $WindowEnd `
    -SubscriptionPrimary $SubscriptionPrimary `
    -SubscriptionModel $SubscriptionModel -SubscriptionEffort $SubscriptionEffort

# Surface the billing-impacting tier choice on every registration. The default
# is -SubscriptionPrimary $false (#972) — a bare `Register-SandcastleTask.ps1
# -Repo X` registers a DeepSeek-primary task; opting into the subscription
# credit (which bills the Anthropic Max Agent-SDK credit) is explicit. Print the
# resolved tier either way so a billing flip is never silent.
if ($SubscriptionPrimary) {
    Write-Host "[register] PRIMARY tier: subscription ($SubscriptionModel, effort=$SubscriptionEffort) — bills the Agent-SDK credit; DeepSeek fallback=$(if ($Tier2Provider) { $Tier2Provider } else { '<none>' })." -ForegroundColor Yellow
} else {
    Write-Host "[register] PRIMARY tier: $(if ($Tier2Provider) { "endpoint ($Tier2Provider)" } else { 'Ollama local' }) — subscription credit NOT used."
}

$action = New-ScheduledTaskAction -Execute $pwshExe `
    -Argument ($argParts -join ' ') `
    -WorkingDirectory $RepoRoot

# ---------------------------------------------------------------------------
# Trigger -- daily including weekends, at the start of the safe-hours window.
# ---------------------------------------------------------------------------

$today = (Get-Date).Date
$startDt = [datetime]::ParseExact("$($today.ToString('yyyy-MM-dd')) $StartTime", 'yyyy-MM-dd HH:mm', $null)
# If StartTime already passed today, schedule starts firing tomorrow.
if ($startDt -lt (Get-Date)) { $startDt = $startDt.AddDays(1) }

$trigger = New-ScheduledTaskTrigger -Daily -At $startDt

# ---------------------------------------------------------------------------
# Principal + settings.
# ---------------------------------------------------------------------------

$principal = New-ScheduledTaskPrincipal -UserId $env:USERNAME -LogonType Interactive -RunLevel Limited

$settings = New-ScheduledTaskSettingsSet `
    -AllowStartIfOnBatteries `
    -DontStopIfGoingOnBatteries `
    -StartWhenAvailable `
    -MultipleInstances IgnoreNew `
    -ExecutionTimeLimit ([timespan]::FromHours(8))

# ---------------------------------------------------------------------------
# Register (idempotent).
# ---------------------------------------------------------------------------

$existing = Get-ScheduledTask -TaskName $taskName -ErrorAction SilentlyContinue

if ($WhatIfOnly) {
    Write-Host "[whatif] Would register task '$taskName'"
    Write-Host "         Execute   : $pwshExe"
    Write-Host "         Arguments : $($argParts -join ' ')"
    Write-Host "         WorkingDir: $RepoRoot"
    Write-Host "         Trigger   : Daily at $StartTime (next fire: $startDt)"
    Write-Host "         WindowEnd : $WindowEnd"
    Write-Host "         Existing  : $(if ($existing) { 'YES (would be replaced)' } else { 'no' })"
    return
}

if ($existing) {
    Write-Host "[register] Unregistering existing '$taskName' (idempotent)"
    Unregister-ScheduledTask -TaskName $taskName -Confirm:$false
}

$description = "Sandcastle AFK loop for $Repo. Slice #$(if ($Repo -eq 'jarvis') { '545' } else { '546' }). Soft-stop at $WindowEnd. Decisions 4890aa35, 0c3017c6, f8e27d53, 58670ea5."

Register-ScheduledTask -TaskName $taskName `
    -Action $action -Trigger $trigger `
    -Principal $principal -Settings $settings `
    -Description $description | Out-Null

Write-Host "[register] '$taskName' scheduled daily at $StartTime (window ends $WindowEnd)."
Write-Host "           Model=$Model  Tier1=$Tier1Model  Tier2=$(if ($Tier2Provider) { $Tier2Provider } else { '<disabled>' })"
Write-Host "           Inspect: Get-ScheduledTask -TaskName '$taskName'"
Write-Host "           Trigger now: Start-ScheduledTask -TaskName '$taskName'"
