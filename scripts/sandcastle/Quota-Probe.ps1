<#
.SYNOPSIS
    Poll Claude Max weekly usage and broadcast pressure state via hysteresis.

.DESCRIPTION
    Standalone scheduled job for the routine host. Runs `claude -p "/usage"`,
    parses the weekly% consumption, caches the result, and applies hysteresis
    to broadcast pressure state via `gh variable set CLAUDE_QUOTA_PRESSURE`
    and a `quota_pressure` row in the legacy `events` table (see Event sink below).

    Hysteresis:
        weekly% >= 80  → trip  (CLAUDE_QUOTA_PRESSURE=true)
        weekly% <  70  → release (CLAUDE_QUOTA_PRESSURE=false)
        70 <= weekly% < 80 → no state change (anti-flap)

    Cache: result cached as `~/.jarvis/orchestrator/usage.json` (default TTL 35 min).
    Reuses cached value on parse failure so transient Claude-CLI glitches don't
    flip the pressure variable.

    Event sink: writes a `quota_pressure` row to the legacy `events` table
    (mcp-memory/schema.sql), NOT `events_canonical`. The `events` table is what
    the #327 escalation hook (scripts/telegram-notify-hook.py) reads -- it is the
    only consumer that turns a pressure trip into an owner notification. The C17
    `events_canonical` substrate (#476) has no application writers wired yet
    (#477 cutover incomplete), so a row there would reach no consumer.

    Realizes decision 46830b4e (80%/70% hysteresis), which SUPERSEDES the initial
    90% single-gate decision d5b3fdd3 -- do not re-introduce a 90% threshold.

.PARAMETER CacheDir
    Directory for the usage cache file. Default: ~/.jarvis/orchestrator.

.PARAMETER CacheTTLMinutes
    How long a cached result is considered fresh. Default 35 min (for a 30-min
    probe cadence, gives ~5 min overlap so transient probe failures use cache).

.PARAMETER TripThreshold
    weekly% threshold to set pressure=true. Default 80.

.PARAMETER ReleaseThreshold
    weekly% below which pressure is released. Default 70.

.PARAMETER NoBroadcast
    Dry-run switch: log what would happen but don't call gh or write the event.

.PARAMETER DotEnvPath
    Path to a .env file with SUPABASE_URL and SUPABASE_KEY for writing
    `events` rows. Default: $PSScriptRoot\..\..\.sandcastle\.env
    (the sandcastle env, which also carries these creds).

.PARAMETER ClaudeCli
    Path to the claude CLI executable. Default: auto-discover via Get-Command.

.EXAMPLE
    .\Quota-Probe.ps1
    # Normal probe: check usage, apply hysteresis, broadcast if state changed.

.EXAMPLE
    .\Quota-Probe.ps1 -NoBroadcast
    # Check-only: parse usage, log state, but do not write gh var or events.
#>

[CmdletBinding()]
param(
    [string]$CacheDir,

    [ValidateRange(1, 1440)]
    [int]$CacheTTLMinutes = 35,

    [ValidateRange(1, 99)]
    [int]$TripThreshold = 80,

    [ValidateRange(0, 98)]
    [int]$ReleaseThreshold = 70,

    [switch]$NoBroadcast,

    [string]$DotEnvPath,

    [string]$ClaudeCli,

    [string]$PressureVar = 'CLAUDE_QUOTA_PRESSURE',

    # Dot-source guard: when set, the script defines its functions but does NOT
    # auto-discover the claude CLI or run the entry point. Tests dot-source with
    # -NoExecute so loading the module never fires the live probe (would exit 2
    # / call the real CLI and kill the Pester runner).
    [switch]$NoExecute
)

$ErrorActionPreference = 'Stop'

# ---------------------------------------------------------------------------
# Defaults
# ---------------------------------------------------------------------------

if (-not $CacheDir) {
    $CacheDir = Join-Path $env:USERPROFILE '.jarvis\orchestrator'
}

if (-not $DotEnvPath) {
    $DotEnvPath = Join-Path $PSScriptRoot '..\..\.sandcastle\.env'
}

if ($ReleaseThreshold -ge $TripThreshold) {
    throw "ReleaseThreshold ($ReleaseThreshold) must be < TripThreshold ($TripThreshold)."
}

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

function Read-DotEnvFile {
    [CmdletBinding()]
    param([string]$Path)
    $vars = @{}
    if (-not (Test-Path -LiteralPath $Path)) { return $vars }
    foreach ($line in Get-Content -LiteralPath $Path) {
        $trimmed = $line.Trim()
        if (-not $trimmed -or $trimmed.StartsWith('#')) { continue }
        $eq = $trimmed.IndexOf('=')
        if ($eq -lt 1) { continue }
        $key = $trimmed.Substring(0, $eq).Trim()
        $val = $trimmed.Substring($eq + 1).Trim()
        if ($val.Length -ge 2) {
            $first = $val[0]; $last = $val[$val.Length - 1]
            if (($first -eq '"' -and $last -eq '"') -or ($first -eq "'" -and $last -eq "'")) {
                $val = $val.Substring(1, $val.Length - 2)
            }
        }
        $vars[$key] = $val
    }
    return $vars
}

function Test-CacheFresh {
    [CmdletBinding()]
    param(
        [string]$Path,
        [int]$MaxAgeMinutes
    )
    if (-not (Test-Path -LiteralPath $Path)) { return $false }
    try {
        $data = Get-Content -LiteralPath $Path -Raw -Encoding utf8 -ErrorAction Stop | ConvertFrom-Json
        if (-not $data.cached_at) { return $false }
        $age = (Get-Date) - [datetime]::Parse($data.cached_at)
        return ($age.TotalMinutes -lt $MaxAgeMinutes)
    } catch {
        return $false
    }
}

function Read-Cache {
    [CmdletBinding()]
    param([string]$Path)
    if (-not (Test-Path -LiteralPath $Path)) { return $null }
    try {
        $data = Get-Content -LiteralPath $Path -Raw -Encoding utf8 -ErrorAction Stop | ConvertFrom-Json
        return [pscustomobject]@{
            percent   = [int]$data.percent
            cached_at = $data.cached_at
        }
    } catch {
        return $null
    }
}

function Write-Cache {
    [CmdletBinding()]
    param(
        [string]$Path,
        [int]$Percent
    )
    $dir = Split-Path $Path -Parent
    if (-not (Test-Path -LiteralPath $dir)) {
        New-Item -ItemType Directory -Path $dir -Force -ErrorAction Stop | Out-Null
    }
    $content = @{
        percent   = $Percent
        cached_at = (Get-Date).ToString('o')
    } | ConvertTo-Json
    $content | Out-File -FilePath $Path -Encoding utf8 -Force
}

function Invoke-UsageProbe {
    [CmdletBinding()]
    param([string]$CliPath)
    try {
        $output = & $CliPath -p '/usage' 2>&1
        if ($LASTEXITCODE -ne 0) {
            Write-Warning "claude -p '/usage' exited with code $LASTEXITCODE"
            return $null
        }
        return ($output | Out-String)
    } catch {
        Write-Warning "claude -p '/usage' call failed: $_"
        return $null
    }
}

function Get-UsagePercentFromOutput {
    [CmdletBinding()]
    param([string]$Output)
    if ([string]::IsNullOrWhiteSpace($Output)) { return $null }

    # Match the first integer that follows the word "weekly", whatever separators
    # sit between them. Covers the live pipe-delimited table
    #   "Weekly       | 45%      | 100%"
    # as well as "Weekly usage: 45%", "weekly%: 72" (no trailing %), "weekly = 80".
    # [^\d]* skips pipes/colons/spaces/% so the pipe in the table no longer breaks
    # the match (the previous [:\s]* stopped at the '|'). -match is case-insensitive.
    if ($Output -match 'weekly[^\d]*(\d+)') {
        $val = [int]$Matches[1]
        if ($val -ge 0 -and $val -le 100) {
            return $val
        }
    }
    return $null
}

function Get-PressureState {
    # Returns $true / $false for the current pressure state, or $null when the
    # state could NOT be read (gh failure). Callers MUST treat $null as "unknown"
    # and skip the hysteresis decision -- defaulting an unreadable state to $false
    # makes every probe at >=trip% re-emit a trip event (M1: event spam on gh
    # connectivity loss).
    [CmdletBinding()]
    param([string]$VarName, [string]$Repo = 'your-username/your-repo')
    try {
        $list = & gh variable list --repo $Repo --json name,value 2>$null
        if ($LASTEXITCODE -ne 0 -or [string]::IsNullOrWhiteSpace($list)) { return $null }
        $entry = $list | ConvertFrom-Json | Where-Object { $_.name -eq $VarName }
        if (-not $entry) { return $false }  # variable absent => not pressed (readable)
        return ($entry[0].value -eq 'true')
    } catch {
        Write-Warning "Failed to read gh variable $VarName`: $_"
        return $null
    }
}

function Write-GhVariable {
    [CmdletBinding()]
    param(
        [string]$VarName,
        [bool]$Value,
        [string]$Repo = 'your-username/your-repo'
    )
    $strVal = if ($Value) { 'true' } else { 'false' }
    try {
        & gh variable set $VarName --repo $Repo --body $strVal 2>$null
        if ($LASTEXITCODE -ne 0) {
            Write-Warning "gh variable set $VarName=$strVal exited with code $LASTEXITCODE"
            return $false
        }
        Write-Host "[quota] gh variable $VarName = $strVal"
        return $true
    } catch {
        Write-Warning "Failed to set gh variable $VarName`: $_"
        return $false
    }
}

function Write-PressureEvent {
    # Writes a quota_pressure row to the legacy `events` table (NOT
    # events_canonical -- see synopsis). The body matches the `events` schema
    # (mcp-memory/schema.sql) and the columns the #327 telegram hook reads.
    [CmdletBinding()]
    param(
        [string]$SupabaseUrl,
        [string]$SupabaseKey,
        [int]$Percent,
        [ValidateSet('tripped', 'released')]
        [string]$State
    )
    if (-not $SupabaseUrl -or -not $SupabaseKey) {
        Write-Warning "Supabase credentials missing -- skipping events write."
        return $null
    }

    $title = if ($State -eq 'tripped') {
        "Claude Max weekly at ${Percent}% -- pressure tripped"
    } else {
        "Claude Max weekly at ${Percent}% -- pressure released"
    }

    $body = @{
        event_type = 'quota_pressure'
        severity    = 'high'
        repo        = 'your-username/your-repo'
        source      = 'quota_probe'
        title       = $title
        payload     = @{
            weekly_percent = $Percent
            state          = $State
        }
    } | ConvertTo-Json -Depth 4

    $headers = @{
        apikey          = $SupabaseKey
        Authorization   = "Bearer $SupabaseKey"
        'Content-Type'  = 'application/json'
    }

    $url = "$($SupabaseUrl.TrimEnd('/'))/rest/v1/events"
    try {
        $resp = Invoke-RestMethod -Uri $url -Method Post -Headers $headers -Body $body -ErrorAction Stop
        Write-Host "[quota] events row written (state=$State, percent=$Percent)"
        return $resp
    } catch {
        Write-Warning "events write failed: $_"
        return $null
    }
}

# ---------------------------------------------------------------------------
# Main probe logic
# ---------------------------------------------------------------------------

function Invoke-QuotaProbe {
    [CmdletBinding()]
    param(
        [string]$CacheDir,
        [int]$CacheTTLMinutes,
        [int]$TripThreshold,
        [int]$ReleaseThreshold,
        [switch]$NoBroadcast,
        [string]$DotEnvPath,
        [string]$ClaudeCli,
        [string]$PressureVar = 'CLAUDE_QUOTA_PRESSURE'
    )

    $stateFile = Join-Path $CacheDir 'usage.json'
    # $percent stays $null until a value is obtained. PowerShell 5.1 has no
    # [int?] literal (it is a parse error), and plain [int]/[bool] have no
    # .HasValue/.Value members -- so we use $null + ($null -ne $percent) checks
    # rather than Nullable semantics (C1).
    $percent = $null
    $cacheHit = $false

    # ---- Phase 1: Probe ----
    if (Test-CacheFresh -Path $stateFile -MaxAgeMinutes $CacheTTLMinutes) {
        $cached = Read-Cache -Path $stateFile
        if ($cached -and $cached.percent -ge 0) {
            $percent = [int]$cached.percent
            $cacheHit = $true
            Write-Host "[quota] cache hit: ${percent}% (age < ${CacheTTLMinutes}m)"
        }
    }

    if ($null -eq $percent) {
        $raw = Invoke-UsageProbe -CliPath $ClaudeCli
        if ($raw) {
            $parsed = Get-UsagePercentFromOutput -Output $raw
            if ($null -ne $parsed) {
                $percent = [int]$parsed
                Write-Cache -Path $stateFile -Percent $percent
                Write-Host "[quota] probe: ${percent}% (cached)"
            }
        }

        # Fallback: if probe failed and we have stale cached data, use it
        if ($null -eq $percent) {
            $cached = Read-Cache -Path $stateFile
            if ($cached -and $cached.percent -ge 0) {
                $percent = [int]$cached.percent
                Write-Warning "[quota] probe failed -- using stale cached value ${percent}%"
            }
        }
    }

    if ($null -eq $percent) {
        # Write-Warning, not Write-Error: ErrorActionPreference='Stop' would make
        # Write-Error terminating and break this function's contract of RETURNING
        # an action='error' result for the entry point to act on (exit 2).
        Write-Warning "[quota] no usage data available -- skipping hysteresis check."
        return [pscustomobject]@{
            action     = 'error'
            reason     = 'no-usage-data'
            percent    = $null
        }
    }

    # ---- Phase 2: Hysteresis ----
    # Get-PressureState returns $null when the gh state is unreadable. Treating
    # that as "not pressed" would re-fire a trip event every probe while gh is
    # down (M1), so we skip the decision instead.
    $currentlyPressed = Get-PressureState -VarName $PressureVar
    if ($null -eq $currentlyPressed) {
        Write-Warning "[quota] pressure state unreadable (gh failure) -- skipping hysteresis to avoid event spam."
        return [pscustomobject]@{
            action     = 'skipped'
            reason     = 'pressure-state-unreadable'
            percent    = $percent
        }
    }
    Write-Host "[quota] percent=${percent}% pressed=$currentlyPressed trip=$TripThreshold release=$ReleaseThreshold"

    $action = 'none'
    $reason = ''
    $newState = $null

    if ($percent -ge $TripThreshold) {
        if (-not $currentlyPressed) {
            $action = 'trip'
            $newState = $true
            $reason = "weekly ${percent}% >= ${TripThreshold}% -- tripping pressure"
        } else {
            $reason = "weekly ${percent}% >= ${TripThreshold}% but already pressed (no change)"
        }
    } elseif ($percent -lt $ReleaseThreshold) {
        if ($currentlyPressed) {
            $action = 'release'
            $newState = $false
            $reason = "weekly ${percent}% < ${ReleaseThreshold}% -- releasing pressure"
        } else {
            $reason = "weekly ${percent}% < ${ReleaseThreshold}% but already released (no change)"
        }
    } else {
        $reason = "weekly ${percent}% in hysteresis band (${ReleaseThreshold}-$($TripThreshold - 1)%) -- no change"
    }

    # ---- Phase 3: Broadcast ----
    $ghOk = $null
    $eventOk = $null

    if ($action -eq 'none') {
        Write-Host "[quota] $reason"
    } elseif ($NoBroadcast) {
        Write-Host "[quota] dry-run: would $action ($reason)"
    } else {
        Write-Host "[quota] ${action}: $reason"

        # Map the action verb ('trip'/'release') to the past-tense state the
        # event sink expects ('tripped'/'released') (C3 -- passing 'trip' to a
        # [ValidateSet('tripped','released')] param threw on every broadcast).
        $eventState = if ($action -eq 'trip') { 'tripped' } else { 'released' }

        # 1. gh variable ($newState is a plain [bool] -- no .Value member, C2)
        $ghOk = Write-GhVariable -VarName $PressureVar -Value $newState

        # 2. events row (legacy `events` table -- the telegram hook's source)
        $envVars = Read-DotEnvFile -Path $DotEnvPath
        $sbUrl = $envVars['SUPABASE_URL']
        $sbKey = $envVars['SUPABASE_KEY']
        if ($env:SUPABASE_URL) { $sbUrl = $env:SUPABASE_URL }
        if ($env:SUPABASE_KEY) { $sbKey = $env:SUPABASE_KEY }
        $eventOk = Write-PressureEvent -SupabaseUrl $sbUrl -SupabaseKey $sbKey `
            -Percent $percent -State $eventState
    }

    return [pscustomobject]@{
        action           = $action
        reason           = $reason
        percent          = $percent
        cacheHit         = $cacheHit
        currentlyPressed = $currentlyPressed
        ghVariableSet    = $ghOk
        eventWritten     = ($null -ne $eventOk)
    }
}

# ---------------------------------------------------------------------------
# Entry -- skipped entirely when dot-sourced with -NoExecute (tests). The claude
# CLI auto-discovery lives here so loading the module never calls Get-Command /
# exit 2 (C5).
# ---------------------------------------------------------------------------

if (-not $NoExecute) {
    if (-not $ClaudeCli) {
        $claudeCmd = Get-Command claude -ErrorAction SilentlyContinue
        if (-not $claudeCmd) {
            Write-Error "claude CLI not found on PATH."
            exit 2
        }
        $ClaudeCli = $claudeCmd.Source
    }

    $result = Invoke-QuotaProbe -CacheDir $CacheDir -CacheTTLMinutes $CacheTTLMinutes `
        -TripThreshold $TripThreshold -ReleaseThreshold $ReleaseThreshold `
        -NoBroadcast:$NoBroadcast -DotEnvPath $DotEnvPath -ClaudeCli $ClaudeCli `
        -PressureVar $PressureVar

    $result | ConvertTo-Json -Depth 4 | Out-Host

    if ($result.action -eq 'error') { exit 2 }
    exit 0
}
