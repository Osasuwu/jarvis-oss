# Run-Sandcastle.ps1 -- slice 4 watchdog wrapper for the AFK sandcastle loop.
# Single entry point production code paths use instead of `tsx main.mts`.
# Decision: 0c3017c6 (fail-fast + autostart + soft-stop window).
#
# Responsibilities:
#  1. Ensure Docker daemon is up (autostart + poll, fail fast on timeout).
#  2. Ensure Ollama is up (autostart + poll, fail fast on timeout).
#  3. Prune stale .sandcastle/runtime/<stamp>/ dirs (keep N most recent).
#  4. Run sandcastle one or more iterations via tsx/npm.
#  5. Parse the result JSON dumped by main.mts.
#  6. Write outcome_record to Supabase via PostgREST anon insert.
#  7. Honor a safe-hours window -- soft-stop between iterations only.
#
# Telegram (slice 6) and multi-tier escalation (slice 5) are layered later.
#
# -RuntimeRetention <N> (default 30) controls the runtime-dir sweep at the
# start of each watchdog run. Env var SANDCASTLE_RUNTIME_RETENTION overrides.
# Pass -RuntimeRetention -1 to disable sweep. (#572)

[CmdletBinding()]
param(
    [ValidateSet('jarvis', 'your-second-repo')]
    [string]$Repo,

    [int]$MaxIterations = 1,

    [string]$Model,

    # Either ISO-8601 datetime ("2026-05-09T03:00:00") or "HH:mm" interpreted
    # as the next occurrence today. Empty string disables the window.
    [string]$WindowEnd,

    [int]$DockerTimeoutSec = 120,

    [int]$OllamaTimeoutSec = 30,

    # Slice 5 (#543): multi-tier escalation. -Model is Tier 0; -Tier1Model is
    # the smaller Ollama fallback on OOM/crash; -Tier2Provider switches to a
    # remote API (deepseek | claude) on persistent failure. Empty values
    # disable that tier (the chain still runs as a single-tier loop).
    [string]$Tier1Model,

    [ValidateSet('', 'deepseek', 'claude')]
    [string]$Tier2Provider = '',

    # 2026-05-14: skip Tier 0/1 Ollama and run Tier 2 as primary. Local Ollama
    # models fail the real-Claude-Code tool_use fidelity check (memory
    # ollama_bench_must_measure_tool_use_fidelity). Recommended for AFK runs;
    # the OOM-escalation chain stays available when this flag is off.
    [switch]$Tier2AsPrimary,

    # 2026-06-15 (#972): run the Anthropic subscription Agent SDK credit as the
    # PRIMARY tier and skip Ollama entirely. Bills the monthly Max Agent-SDK
    # credit via CLAUDE_CODE_OAUTH_TOKEN (set in .sandcastle/.env), NOT the
    # metered API. On any subscription-tier failure (credit exhausted ->
    # hard-stop, or a transient API error) the run falls back to the DeepSeek
    # Tier 2 endpoint for that iteration. Senior to -Tier2AsPrimary: when both
    # are set, subscription is primary and DeepSeek becomes the fallback.
    [switch]$SubscriptionPrimary,

    # Subscription-tier model + effort. claude-opus-4-8 (regular, NOT 1M --
    # extra billing) at medium effort: model = task depth, effort = task width;
    # AFK slices are single sharpened verticals so depth-heavy / width-bounded
    # is the right trade (decision rationale, #972).
    [string]$SubscriptionModel = 'claude-opus-4-8',

    [ValidateSet('low', 'medium', 'high', 'max')]
    [string]$SubscriptionEffort = 'medium',

    # Runtime-dir retention: keep the N most-recent .sandcastle/runtime/<stamp>/
    # directories on watchdog entry, prune older ones. -1 disables the sweep.
    # Env var SANDCASTLE_RUNTIME_RETENTION overrides this if set (#572).
    [int]$RuntimeRetention = 30,

    # Skip the actual sandcastle invocation -- for dry runs and Pester.
    [switch]$NoExecute
)

$ErrorActionPreference = 'Stop'

# ---------------------------------------------------------------------------
# Daemon health probes
# ---------------------------------------------------------------------------

function Test-DockerRunning {
    [CmdletBinding()]
    param()
    try {
        & docker info --format '{{.ServerVersion}}' 2>$null | Out-Null
        return ($LASTEXITCODE -eq 0)
    } catch {
        return $false
    }
}

function Start-DockerDesktop {
    [CmdletBinding()]
    param()
    $exe = "$env:ProgramFiles\Docker\Docker\Docker Desktop.exe"
    if (-not (Test-Path -LiteralPath $exe)) {
        throw "Docker Desktop not installed at expected path: $exe"
    }
    Start-Process -FilePath $exe -WindowStyle Hidden | Out-Null
}

function Wait-DockerReady {
    [CmdletBinding()]
    param([int]$TimeoutSec)
    $deadline = (Get-Date).AddSeconds($TimeoutSec)
    while ((Get-Date) -lt $deadline) {
        if (Test-DockerRunning) { return $true }
        Start-Sleep -Seconds 2
    }
    return $false
}

function Test-OllamaRunning {
    [CmdletBinding()]
    param([string]$BaseUrl = 'http://localhost:11434')
    try {
        $resp = Invoke-WebRequest -Uri "$BaseUrl/api/tags" -UseBasicParsing -TimeoutSec 3 -ErrorAction Stop
        return ($resp.StatusCode -eq 200)
    } catch {
        return $false
    }
}

function Start-OllamaServer {
    [CmdletBinding()]
    param()
    $cmd = Get-Command ollama -ErrorAction SilentlyContinue
    if (-not $cmd) {
        throw "ollama executable not on PATH; cannot autostart."
    }
    Start-Process -FilePath $cmd.Source -ArgumentList 'serve' -WindowStyle Hidden | Out-Null
}

function Wait-OllamaReady {
    [CmdletBinding()]
    param([int]$TimeoutSec, [string]$BaseUrl = 'http://localhost:11434')
    $deadline = (Get-Date).AddSeconds($TimeoutSec)
    while ((Get-Date) -lt $deadline) {
        if (Test-OllamaRunning -BaseUrl $BaseUrl) { return $true }
        Start-Sleep -Seconds 1
    }
    return $false
}

# ---------------------------------------------------------------------------
# Safe-hours window
# ---------------------------------------------------------------------------

function Resolve-WindowEnd {
    [CmdletBinding()]
    param([string]$WindowEnd)
    if ([string]::IsNullOrWhiteSpace($WindowEnd)) { return $null }
    if ($WindowEnd -match '^\d{2}:\d{2}$') {
        $today = (Get-Date).Date
        $end = $today.Add([TimeSpan]::Parse($WindowEnd + ':00'))
        # AFK windows commonly straddle midnight (e.g. jarvis fires at 18:00
        # with WindowEnd=01:00 meaning 01:00 *tomorrow*). If the resolved
        # boundary is already in the past, roll it forward 24h. This also
        # fixes the same-day case where the scheduled task is launched after
        # WindowEnd has technically elapsed for today -- on-call manual runs
        # should still get a fresh window, not record window-expired before
        # any work begins.
        if ($end -lt (Get-Date)) { $end = $end.AddDays(1) }
        return $end
    }
    return [datetime]::Parse($WindowEnd)
}

function Test-WindowExpired {
    [CmdletBinding()]
    param([Nullable[datetime]]$WindowEnd)
    if (-not $WindowEnd) { return $false }
    return ((Get-Date) -ge $WindowEnd)
}

# ---------------------------------------------------------------------------
# Sandcastle invocation
# ---------------------------------------------------------------------------

function Get-RepoRoot {
    [CmdletBinding()]
    param([string]$Repo)
    switch ($Repo) {
        'jarvis' {
            # scripts/sandcastle/Run-Sandcastle.ps1 → repo root is two levels up.
            return (Resolve-Path -LiteralPath (Join-Path $PSScriptRoot '..\..')).Path
        }
        'your-second-repo' {
            $root = $env:SECOND_REPO_ROOT
            if ([string]::IsNullOrWhiteSpace($root)) {
                throw "Set SECOND_REPO_ROOT to the your-second-repo checkout (slice 9 will deploy this on the routine host)."
            }
            if (-not (Test-Path -LiteralPath $root)) {
                throw "SECOND_REPO_ROOT does not exist: $root"
            }
            return (Resolve-Path -LiteralPath $root).Path
        }
        default { throw "Unknown repo: $Repo" }
    }
}

function New-RuntimeDir {
    [CmdletBinding()]
    param([string]$RepoRoot, [string]$Stamp)
    $dir = Join-Path $RepoRoot ".sandcastle/runtime/$Stamp"
    New-Item -ItemType Directory -Path $dir -Force | Out-Null
    return $dir
}

function Invoke-RuntimeSweep {
    # Prune .sandcastle/runtime/<stamp>/ to the most recent $Keep directories.
    # Used by Invoke-Watchdog before each run so nightly AFK loops don't fill
    # disk with stale per-iteration runtime dirs. $Keep -lt 0 disables sweep.
    [CmdletBinding()]
    param(
        [string]$RuntimeRoot,
        [int]$Keep = 30
    )
    if ($Keep -lt 0) { return @() }
    if (-not (Test-Path -LiteralPath $RuntimeRoot)) { return @() }
    $dirs = Get-ChildItem -LiteralPath $RuntimeRoot -Directory -ErrorAction SilentlyContinue |
        Sort-Object -Property Name -Descending
    if ($dirs.Count -le $Keep) { return @() }
    $toPrune = $dirs | Select-Object -Skip $Keep
    $pruned = @()
    foreach ($d in $toPrune) {
        try {
            Remove-Item -LiteralPath $d.FullName -Recurse -Force -ErrorAction Stop
            $pruned += $d.Name
        } catch {
            Write-Warning "runtime-sweep: failed to remove $($d.FullName): $_"
        }
    }
    return $pruned
}

function Invoke-NpmSandcastle {
    # Thin wrapper around `npm run sandcastle` extracted from Invoke-Sandcastle
    # so the npm call can be mocked in tests (#572). The PS 5.1 stderr-wrapping
    # workaround (#608) lives here.
    [CmdletBinding()]
    param([string]$LogFile)
    if (-not (Get-Command npm -ErrorAction SilentlyContinue)) {
        return [pscustomobject]@{ cmdNotFound = $true; exitCode = -1 }
    }
    # PS 5.1 wraps every native-command stderr line as a NativeCommandError
    # under EAP=Stop, killing the watchdog on npm's first stderr line
    # (any warning). Delegate the 2>&1 merge to cmd.exe so PS only sees
    # a single stdout stream -- no wrapping, no terminating exception.
    # See issue #608.
    #
    # No --silent: it suppressed npm's error framing, so when the in-container
    # agent died on a provider error (DeepSeek HTTP 402 "Insufficient Balance")
    # nothing reached run.log and diag_tail stayed blank for the whole
    # 2026-06-03..11 outage (#955). Noisier npm banners in run.log are the
    # acceptable cost of never losing the crash text again.
    $combined = & cmd.exe /c 'npm run sandcastle 2>&1'
    $exitCode = $LASTEXITCODE
    if ($LogFile) {
        $combined | Out-File -FilePath $LogFile -Encoding utf8 -Append
    }
    return [pscustomobject]@{ cmdNotFound = $false; exitCode = $exitCode }
}

function Invoke-Sandcastle {
    [CmdletBinding()]
    param(
        [string]$RepoRoot,
        [string]$Model,
        [int]$MaxIterations,
        [string]$ResultFile,
        [string]$LogFile,
        [string]$RunId,
        # Slice 5 (#543) tier overrides. Empty => main.mts falls back to Ollama
        # defaults via OLLAMA_BASE_URL / "ollama" auth.
        [string]$BaseUrl,
        [string]$AuthToken,
        # Auth mode (#972). Default 'endpoint' is the REAL-MONEY GUARD: every
        # Ollama/DeepSeek caller is auto-pinned to ANTHROPIC_BASE_URL/TOKEN, so
        # the presence of CLAUDE_CODE_OAUTH_TOKEN in .env can never silently
        # bill the subscription credit. Only the subscription tier passes
        # -AuthMode subscription explicitly.
        [ValidateSet('endpoint', 'subscription')]
        [string]$AuthMode = 'endpoint',
        # Effort (#972), subscription tier only. Empty => main.mts default.
        [string]$Effort,
        # Forced-target issue (escalation retries). Empty => agent free-picks.
        [string]$TargetIssue
    )

    # Stale result.json from a prior iteration would otherwise be silently
    # re-read on a partial-write crash. Always start clean.
    Remove-Item -LiteralPath $ResultFile -ErrorAction SilentlyContinue

    # Save-and-restore so dot-sourced Pester runs don't bleed values across calls.
    $prev = @{
        SANDCASTLE_RESULT_FILE      = $env:SANDCASTLE_RESULT_FILE
        SANDCASTLE_MAX_ITERATIONS   = $env:SANDCASTLE_MAX_ITERATIONS
        SANDCASTLE_RUN_ID           = $env:SANDCASTLE_RUN_ID
        SANDCASTLE_AGENT_MODEL      = $env:SANDCASTLE_AGENT_MODEL
        SANDCASTLE_AGENT_BASE_URL   = $env:SANDCASTLE_AGENT_BASE_URL
        SANDCASTLE_AGENT_AUTH_TOKEN = $env:SANDCASTLE_AGENT_AUTH_TOKEN
        SANDCASTLE_AGENT_AUTH_MODE  = $env:SANDCASTLE_AGENT_AUTH_MODE
        SANDCASTLE_AGENT_EFFORT     = $env:SANDCASTLE_AGENT_EFFORT
        SANDCASTLE_TARGET_ISSUE     = $env:SANDCASTLE_TARGET_ISSUE
        OLLAMA_MODEL                = $env:OLLAMA_MODEL
    }
    $env:SANDCASTLE_RESULT_FILE    = $ResultFile
    $env:SANDCASTLE_MAX_ITERATIONS = "$MaxIterations"
    if ($RunId)       { $env:SANDCASTLE_RUN_ID           = $RunId }
    if ($Model)       { $env:SANDCASTLE_AGENT_MODEL      = $Model
                        $env:OLLAMA_MODEL                = $Model }
    if ($BaseUrl)     { $env:SANDCASTLE_AGENT_BASE_URL   = $BaseUrl }
    if ($AuthToken)   { $env:SANDCASTLE_AGENT_AUTH_TOKEN = $AuthToken }
    # AuthMode always set (defaults 'endpoint') — the real-money guard. Effort
    # only when supplied (subscription tier); when omitted, CLEAR it so a prior
    # run's value (or a host-set SANDCASTLE_AGENT_EFFORT) can't leak into this
    # child — an endpoint tier must reach main.mts with no effort override.
    $env:SANDCASTLE_AGENT_AUTH_MODE = $AuthMode
    if ($Effort) { $env:SANDCASTLE_AGENT_EFFORT = $Effort }
    else         { Remove-Item Env:SANDCASTLE_AGENT_EFFORT -ErrorAction SilentlyContinue }
    # TargetIssue: pass empty string verbatim so retries can clear it.
    $env:SANDCASTLE_TARGET_ISSUE = "$TargetIssue"

    Push-Location -LiteralPath $RepoRoot
    $cmdNotFound = $false
    try {
        $npmResult = Invoke-NpmSandcastle -LogFile $LogFile
        $cmdNotFound = $npmResult.cmdNotFound
        $exitCode = $npmResult.exitCode
    } finally {
        Pop-Location
        $env:SANDCASTLE_RESULT_FILE      = $prev.SANDCASTLE_RESULT_FILE
        $env:SANDCASTLE_MAX_ITERATIONS   = $prev.SANDCASTLE_MAX_ITERATIONS
        $env:SANDCASTLE_RUN_ID           = $prev.SANDCASTLE_RUN_ID
        $env:SANDCASTLE_AGENT_MODEL      = $prev.SANDCASTLE_AGENT_MODEL
        $env:SANDCASTLE_AGENT_BASE_URL   = $prev.SANDCASTLE_AGENT_BASE_URL
        $env:SANDCASTLE_AGENT_AUTH_TOKEN = $prev.SANDCASTLE_AGENT_AUTH_TOKEN
        $env:SANDCASTLE_AGENT_AUTH_MODE  = $prev.SANDCASTLE_AGENT_AUTH_MODE
        $env:SANDCASTLE_AGENT_EFFORT     = $prev.SANDCASTLE_AGENT_EFFORT
        $env:SANDCASTLE_TARGET_ISSUE     = $prev.SANDCASTLE_TARGET_ISSUE
        $env:OLLAMA_MODEL                = $prev.OLLAMA_MODEL
    }

    if ($cmdNotFound) {
        return [pscustomobject]@{ ok = $false; exitCode = -1; result = $null; reason = 'npm-not-found' }
    }
    if ($exitCode -ne 0) {
        return [pscustomobject]@{ ok = $false; exitCode = $exitCode; result = $null; reason = "exit=$exitCode" }
    }
    if (-not (Test-Path -LiteralPath $ResultFile)) {
        return [pscustomobject]@{ ok = $false; exitCode = $exitCode; result = $null; reason = 'no-result-file' }
    }
    try {
        $json = Get-Content -LiteralPath $ResultFile -Raw -Encoding utf8 | ConvertFrom-Json
    } catch {
        return [pscustomobject]@{ ok = $false; exitCode = $exitCode; result = $null; reason = "json-parse-error: $_" }
    }
    return [pscustomobject]@{ ok = $true; exitCode = 0; result = $json; reason = $null }
}

# ---------------------------------------------------------------------------
# Tier escalation -- slice 5 (#543, decision f8e27d53)
# ---------------------------------------------------------------------------
#
# Detects model-side resource exhaustion (OOM / model-load failure) so the
# watchdog can retry on a smaller Ollama model (Tier 1) or escalate to a
# remote API (Tier 2). False positives are cheap (one extra retry); false
# negatives downgrade to a generic failure outcome, which is still safe.

# Substrings (case-insensitive) that indicate the agent's model itself fell
# over due to memory pressure rather than a logic / agent-side error.
$script:OOMSignatures = @(
    'out of memory',
    'oom',
    'cuda out of memory',
    'model requires more system memory',
    'model load failed',
    'failed to load model',
    'unable to allocate'
)

function Test-IsOOM {
    [CmdletBinding()]
    param(
        [string]$Reason,
        [string]$LogFile
    )
    if ($Reason -match '^exit=137$') { return $true }   # SIGKILL on Linux OOM
    if ($Reason -like 'json-parse-error*') { return $false }  # malformed result, not OOM
    if (-not $LogFile -or -not (Test-Path -LiteralPath $LogFile)) { return $false }
    try {
        $content = Get-Content -LiteralPath $LogFile -Raw -Encoding utf8 -ErrorAction Stop
    } catch {
        return $false
    }
    if (-not $content) { return $false }
    foreach ($sig in $script:OOMSignatures) {
        if ($content -match [regex]::Escape($sig)) { return $true }
    }
    return $false
}

function Get-IssueFromBranch {
    [CmdletBinding()]
    param([string]$Branch)
    if (-not $Branch) { return $null }
    if ($Branch -match '^(?:feat|fix|chore)/(\d+)\b') { return [int]$Matches[1] }
    return $null
}

function Invoke-Gh {
    & gh @args 2>$null
}

function Get-IssueLabels {
    [CmdletBinding()]
    param([int]$Issue, [string]$RepoSlug)
    if (-not $Issue) { return @() }
    try {
        $ghArgs = @('issue', 'view', "$Issue", '--json', 'labels', '--jq', '[.labels[].name]')
        if ($RepoSlug) { $ghArgs += @('--repo', $RepoSlug) }
        $raw = Invoke-Gh @ghArgs
        if ($LASTEXITCODE -ne 0 -or -not $raw) { return @() }
        return ($raw | ConvertFrom-Json)
    } catch {
        return @()
    }
}

function Add-IssueLabel {
    [CmdletBinding()]
    param([int]$Issue, [string]$Label, [string]$RepoSlug)
    if (-not $Issue -or -not $Label) { return $false }
    $ghArgs = @('issue', 'edit', "$Issue", '--add-label', $Label)
    if ($RepoSlug) { $ghArgs += @('--repo', $RepoSlug) }
    Invoke-Gh @ghArgs | Out-Null
    return ($LASTEXITCODE -eq 0)
}

function Resolve-Tier2Config {
    [CmdletBinding()]
    param(
        [string]$Provider,        # 'deepseek' | 'claude' | ''
        [int]$Issue,
        [string]$RepoSlug,
        [hashtable]$EnvVars       # parsed .sandcastle/.env
    )
    if (-not $Provider) { return $null }

    # `use-claude-api` label flips Tier 2 from the configured default to
    # Claude (AC: cron runs never auto-promote to Claude; the label is the
    # explicit owner gate).
    $labels = Get-IssueLabels -Issue $Issue -RepoSlug $RepoSlug
    $effective = if ($labels -contains 'use-claude-api') { 'claude' } else { $Provider }

    function Coalesce([string]$a, [string]$b) {
        if ([string]::IsNullOrWhiteSpace($a)) { return $b } else { return $a }
    }
    switch ($effective) {
        'deepseek' {
            return @{
                Provider  = 'deepseek'
                Model     = (Coalesce $EnvVars['DEEPSEEK_MODEL']    'deepseek-coder')
                BaseUrl   = (Coalesce $EnvVars['DEEPSEEK_BASE_URL'] 'https://api.deepseek.com/anthropic')
                AuthToken = $EnvVars['DEEPSEEK_API_KEY']
            }
        }
        'claude' {
            return @{
                Provider  = 'claude'
                Model     = (Coalesce $EnvVars['CLAUDE_MODEL']    'claude-haiku-4-5-20251001')
                BaseUrl   = (Coalesce $EnvVars['CLAUDE_BASE_URL'] 'https://api.anthropic.com')
                AuthToken = $EnvVars['ANTHROPIC_API_KEY']
            }
        }
        default { return $null }
    }
}

# ---------------------------------------------------------------------------
# Telegram alerting -- infra-down only (slice 6, #544, decision 0c3017c6).
# Routine partial / agent-side failure / OOM-escalated outcomes stay silent.
# ---------------------------------------------------------------------------

# Reasons that warrant waking up the principal in chat. Anything else
# (agent-side exit codes, partial:window-expired, success) stays silent
# so morning chat carries signal not noise.
#
# Note: AC #534/#544 mention 'container-launch-fail' as a class but the
# watchdog has no current call site that emits that literal reason --
# Docker container start failures surface as `exit=N` from sandcastle's
# tsx/npm wrapper, classified agent-side by Test-IsInfraDown. Wiring a
# Docker-level health check that distinguishes "image missing / container
# crashed at start" from agent-side errors is tracked in the watchdog
# hardening follow-up #572.
$script:TelegramInfraReasons = @(
    'docker-down',
    'ollama-down',
    'npm-not-found',
    'no-result-file',
    'provider-billing'
)

function Test-IsInfraDown {
    [CmdletBinding()]
    param([string]$Reason)
    if (-not $Reason) { return $false }
    foreach ($r in $script:TelegramInfraReasons) {
        if ($Reason.StartsWith($r)) { return $true }
    }
    return $false
}

# Provider-billing failure signatures (#955). A Tier-2 provider that runs out
# of balance returns an HTTP 402 / "Insufficient Balance" body, which the
# in-container agent surfaces in its crash text. The watchdog otherwise
# classifies this as a generic agent-side `exit=1` and stays silent -- the
# exact failure mode that ran unnoticed 2026-06-03..11. Matching these
# signatures promotes the reason to `provider-billing`, which is an
# infra-down class (above) and therefore fires the Telegram alert.
$script:ProviderBillingSignatures = @(
    'insufficient balance',
    'payment required',
    'insufficient funds'
)

function Test-IsProviderBilling {
    [CmdletBinding()]
    param([string]$Text)
    if (-not $Text) { return $false }
    foreach ($sig in $script:ProviderBillingSignatures) {
        if ($Text -match [regex]::Escape($sig)) { return $true }
    }
    # HTTP 402 (Payment Required) in a status/error context. Two narrow branches
    # instead of a wide `\D{0,20}` window, which matched prose like
    # `error occurred at line 402` and `issue #402` -- spurious billing alerts
    # an agent's run.log can realistically produce (#956 review):
    #   (a) a context word (http/status/error/code) followed by ONLY structured
    #       separators (`:`, `=`, space/tab, JSON punctuation) before 402 --
    #       keeps `status: 402`, `error=402`, `{"code":402}`, `HTTP 402`, while
    #       prose (which puts letters between the word and 402) no longer matches.
    #       Uses [ \t] not \s so newlines in multi-line log tails can't bridge
    #       an `error` keyword to a bare `402` several lines down;
    #   (b) the HTTP status line `HTTP/1.1 402 ...`, which (a) misses because
    #       version digits and dot are not in the separator character class.
    if ($Text -match '(?i)\b(?:http|status|error|code)[ \t:="''{},]{0,6}\b402\b') { return $true }
    if ($Text -match '(?i)\bhttp/\d[\d.]*\s+402\b') { return $true }
    return $false
}

function Format-RedactedError {
    [CmdletBinding()]
    param([string]$Message, [string]$Secret)
    if (-not $Secret) { return $Message }
    return ($Message -replace [regex]::Escape($Secret), '<TOKEN-REDACTED>')
}

# Pre-flight DeepSeek balance probe (#955). GET /user/balance returns
# {"is_available": false, ...} once the account is exhausted -- catching it
# before we spin a container means the run records `provider-billing` and
# fires the alert immediately instead of burning a container boot to discover
# the same 402. Fail-OPEN: no key, network error, HTTP error, or a missing
# is_available field all return $true (proceed) -- the in-run signature class
# (Test-IsProviderBilling) is the backstop, so a flaky probe must never block
# an otherwise-healthy run. Only an affirmative is_available=false blocks.
function Test-DeepSeekBalance {
    [CmdletBinding()]
    param(
        [string]$ApiKey,
        [string]$BalanceUrl = 'https://api.deepseek.com/user/balance'
    )
    if (-not $ApiKey) { return $true }
    try {
        $resp = Invoke-RestMethod -Uri $BalanceUrl -Method Get `
            -Headers @{ Authorization = "Bearer $ApiKey" } -TimeoutSec 10 -ErrorAction Stop
    } catch {
        # Write-Host (stream 1), not Write-Warning (stream 3): a Scheduled Task
        # with default config discards the warning stream, so a persistently
        # failing fail-open probe would silently bypass the whole pre-flight
        # guard with no trace in the task log (#956 review). Matches the rest of
        # the [watchdog] operational logging, which is all stdout.
        Write-Host "[watchdog] WARNING: deepseek balance probe failed (fail-open): $(Format-RedactedError -Message "$_" -Secret $ApiKey)"
        return $true
    }
    if ($null -eq $resp -or $null -eq $resp.is_available) { return $true }
    return ($resp.is_available -eq $true)
}

# Sandcastle tagged-error classes (node_modules/@ai-hero/sandcastle/dist/errors.js).
# The orchestrate() loop returns cleanly on every *normal* termination (no
# commits, no completion signal, max iterations all return a result). The only
# way `npm run sandcastle` exits non-zero is one of these throwing -- so a
# nightly `exit=1` is always a real crash, never a benign "nothing to do".
# Surfacing the class into the outcome turns an opaque exit code into an
# actionable signal (idle-timeout vs worktree-teardown vs API error) without
# needing the routine-host-disk run.log. Order: most specific first (substring-safe
# anyway, matches are on the literal class token).
$script:SandcastleErrorClasses = @(
    'AgentIdleTimeoutError', 'ContainerStartTimeoutError', 'MergeToHostTimeoutError',
    'WorktreeError', 'DockerError', 'SyncError', 'SessionCaptureError',
    'PromptError', 'AgentError', 'ExecError'
)

function Get-LogTail {
    # Last N non-empty lines of the run.log -- the tail carries the thrown
    # tagged-error stack from `npm run sandcastle`. Guards a missing/locked file
    # (returns '') so the failure path never crashes while reporting a failure.
    [CmdletBinding()]
    param([string]$LogFile, [int]$Lines = 12)
    if (-not $LogFile -or -not (Test-Path -LiteralPath $LogFile)) { return '' }
    try {
        $all = @(Get-Content -LiteralPath $LogFile -Encoding utf8 -ErrorAction Stop |
            Where-Object { $_.Trim() })
    } catch { return '' }
    if (-not $all.Count) { return '' }
    ($all | Select-Object -Last $Lines) -join "`n"
}

function Get-SandcastleErrorClass {
    # First known tagged-error class mentioned in $Text (reason + log tail), or ''.
    [CmdletBinding()]
    param([string]$Text)
    if (-not $Text) { return '' }
    foreach ($cls in $script:SandcastleErrorClasses) {
        if ($Text -match [regex]::Escape($cls)) { return $cls }
    }
    return ''
}

function Protect-LogTail {
    # Defense-in-depth before a container log tail reaches Supabase: strip any
    # known literal secrets, then redact generic token shapes by pattern. The
    # agent is instructed never to print secrets, but a crash tail is untrusted.
    [CmdletBinding()]
    param([string]$Text, [string[]]$Secrets = @())
    if (-not $Text) { return '' }
    foreach ($s in $Secrets) {
        if ($s) { $Text = $Text -replace [regex]::Escape($s), '<SECRET-REDACTED>' }
    }
    $Text = $Text -replace 'gh[pousr]_[A-Za-z0-9]{20,}', '<GH-TOKEN-REDACTED>'
    $Text = $Text -replace 'github_pat_[A-Za-z0-9_]{20,}', '<GH-TOKEN-REDACTED>'
    $Text = $Text -replace 'sk-ant-[A-Za-z0-9\-_]{20,}', '<ANTHROPIC-KEY-REDACTED>'
    # Generic sk- prefixed keys (DeepSeek, OpenRouter, etc.) -- must run AFTER
    # the sk-ant- pattern above to avoid partial replacement artifacts.
    $Text = $Text -replace 'sk-[A-Za-z0-9\-_]{32,}', '<API-KEY-REDACTED>'
    return $Text
}

function Send-TelegramAlert {
    [CmdletBinding()]
    param(
        [string]$BotToken,
        [string]$ChatId,
        [string]$Message
    )
    if (-not $BotToken -or -not $ChatId) {
        Write-Warning "Telegram token/chat-id missing -- skipping alert."
        return
    }
    # 200-char cap is an AC; -3 for the '...' tail.
    $maxLen = 200
    if ($Message.Length -gt $maxLen) {
        $Message = $Message.Substring(0, $maxLen - 3) + '...'
    }
    $url = "https://api.telegram.org/bot$BotToken/sendMessage"
    # JSON-encoded body for parity with Write-OutcomeRecord (the file's other
    # HTTP call) and explicit content-type. Telegram accepts both, but
    # consistency makes drift / regressions easier to spot.
    $body = @{ chat_id = $ChatId; text = $Message } | ConvertTo-Json -Compress
    try {
        return Invoke-RestMethod -Uri $url -Method Post -Body $body -ContentType 'application/json' -ErrorAction Stop
    } catch {
        # Sanitize before re-raising: Invoke-RestMethod error messages
        # include the request URL with the bot token embedded. Strip it
        # so callers / logs only ever see "<TOKEN-REDACTED>".
        throw "telegram alert failed: $(Format-RedactedError -Message "$_" -Secret $BotToken)"
    }
}

# ---------------------------------------------------------------------------
# Pytest gate (your-second-repo only, #630)
#
# Test-surface discovery strategy: for each changed .py file, look for a
# co-located test_<name>.py in the same directory or in tests/. Falls back
# to full `pytest` suite when no targeted test files are found.
#
# Decision: touched-file-mapped test discovery with full-suite fallback.
# Rationale: agent may refactor files whose test coverage lives outside
# the co-located pattern (e.g. a tests/ directory mirror). Full suite
# has ~30s overhead on your-second-repo's small test corpus -- acceptable.
# Reversibility: reversible (strategy is config at the function level).
# Alternatives:
#   1. pytest --lf (last-failed) -- rejected, first run has no history.
#   2. Full suite always -- rejected, agent changes are typically small
#      and targeted; full suite is a safety net, not the primary gate.
# Confidence: 0.8.
# ---------------------------------------------------------------------------

function Get-RelatedTestFiles {
    [CmdletBinding()]
    param(
        [string[]]$ChangedFiles,
        [string]$RepoRoot
    )
    $testFiles = @()
    foreach ($f in $ChangedFiles) {
        $name = [System.IO.Path]::GetFileNameWithoutExtension($f)
        $dir  = [System.IO.Path]::GetDirectoryName($f)
        # Co-located: src/module.py -> src/test_module.py
        $colocated = Join-Path (Join-Path $RepoRoot $dir) "test_$name.py"
        if (Test-Path -LiteralPath $colocated -PathType Leaf) {
            $testFiles += $colocated
            continue
        }
        # Tests mirror: src/module.py -> tests/test_module.py
        $inTests = Join-Path (Join-Path $RepoRoot 'tests') "test_$name.py"
        if (Test-Path -LiteralPath $inTests -PathType Leaf) {
            $testFiles += $inTests
            continue
        }
    }
    return ($testFiles | Select-Object -Unique)
}

function Invoke-PytestGate {
    [CmdletBinding()]
    param(
        [string]$RepoRoot,
        [string]$Branch
    )

    if (-not (Test-Path -LiteralPath $RepoRoot)) {
        return [pscustomobject]@{
            passed          = $false
            collectionError = $true
            summary         = "your-second-repo repo root not found: $RepoRoot"
            testsRun        = 0
        }
    }

    # Get changed .py files against the base branch
    Push-Location -LiteralPath $RepoRoot
    try {
        & git fetch origin main --quiet 2>$null | Out-Null
        $changedRaw = & git diff origin/main...$Branch --name-only 2>$null
        $gitExit = $LASTEXITCODE
    } finally {
        Pop-Location
    }

    if ($gitExit -ne 0 -or [string]::IsNullOrWhiteSpace($changedRaw)) {
        # No base branch to diff or no changes -- run full suite as safety net.
        $changedFiles = @()
    } else {
        $changedFiles = ($changedRaw -split "`n") | Where-Object { $_ -match '\.py$' -and $_ -match '\S' }
    }

    $testTargets = @()
    if ($changedFiles.Count -gt 0) {
        $testTargets = Get-RelatedTestFiles -ChangedFiles $changedFiles -RepoRoot $RepoRoot
    }

    # Run pytest
    Push-Location -LiteralPath $RepoRoot
    try {
        if ($testTargets.Count -gt 0) {
            Write-Host "[pytest-gate] running $($testTargets.Count) test files for $($changedFiles.Count) changed .py files"
            $output = & pytest $testTargets --tb=short -q 2>&1
        } else {
            Write-Host "[pytest-gate] no targeted tests found -- running full suite"
            $output = & pytest --tb=short -q 2>&1
        }
        $exitCode = $LASTEXITCODE
    } catch {
        return [pscustomobject]@{
            passed          = $false
            collectionError = $true
            summary         = "pytest invocation failed: $_"
            testsRun        = 0
        }
    } finally {
        Pop-Location
    }

    # Parse output for test count (B1: join array to scalar before -match so $Matches is populated)
    $testsRun = 0
    $joined = $output -join "`n"
    if ($joined -match '(\d+) passed') { $testsRun = [int]$Matches[1] }
    if ($joined -match '(\d+) failed') { $testsRun += [int]$Matches[1] }

    # Extract failing test names
    $failedLines = $output | Select-String '^FAILED ' -ErrorAction SilentlyContinue | ForEach-Object { $_.Line }
    $failureNames = $failedLines -replace '^FAILED ', ''

    if ($exitCode -in @(2, 3, 4, 5)) {
        return [pscustomobject]@{
            passed          = $false
            collectionError = $true
            exitCode        = $exitCode
            summary         = "pytest collection error (exit=$exitCode)"
            testsRun        = $testsRun
        }
    }

    $passed = ($exitCode -eq 0)
    $summary = if ($passed) {
        "${testsRun} passed"
    } else {
        "${testsRun} total, $($failureNames.Count) failed: $($failureNames -join ', ')"
    }

    return [pscustomobject]@{
        passed          = $passed
        collectionError = $false
        exitCode        = $exitCode
        summary         = $summary
        testsRun        = $testsRun
        failures        = $failureNames
    }
}

function Stop-AgentPR {
    [CmdletBinding()]
    param(
        [string]$Branch,
        [string]$RepoSlug
    )
    if ([string]::IsNullOrWhiteSpace($Branch) -or [string]::IsNullOrWhiteSpace($RepoSlug)) {
        return $false
    }
    try {
        # Find the PR for this branch
        $prNum = Invoke-Gh pr list --repo $RepoSlug --head $Branch --state open --json number --jq '.[0].number'
        if ($prNum -and [int]$prNum.Trim() -gt 0) {
            Invoke-Gh pr close $prNum --repo $RepoSlug --comment "Closed by sandcastle watchdog -- pytest gate failed." | Out-Null
            return ($LASTEXITCODE -eq 0)
        }
    } catch {
        Write-Warning "Stop-AgentPR: $_"
    }
    return $false
}

# ---------------------------------------------------------------------------
# Decision memory write -- records pytest-gate decisions in memories table
# (M6/AC7: decision must be queryable by next session via memory_recall).
# Anon INSERT allowed when source_provenance like 'sandcastle:%' (RLS policy).
# ---------------------------------------------------------------------------

function Write-SandcastleDecisionMemory {
    [CmdletBinding()]
    param(
        [string]$SupabaseUrl,
        [string]$SupabaseKey,
        [string]$Project,
        [string]$Name,
        [string]$Description,
        [string]$Content,
        [string]$RunId
    )
    if (-not $SupabaseUrl -or -not $SupabaseKey) {
        Write-Warning "SUPABASE_URL/SUPABASE_KEY missing -- skipping decision memory write."
        return
    }
    $body = @{
        type              = 'decision'
        project           = $Project
        name              = $Name
        description       = $Description
        content           = $Content
        tags              = @('sandcastle', 'afk', 'pytest-gate')
        source_provenance = "sandcastle:pytest-gate:$RunId"
    }
    $headers = @{
        apikey         = $SupabaseKey
        Authorization  = "Bearer $SupabaseKey"
        'Content-Type' = 'application/json'
        Prefer         = 'return=minimal,resolution=merge-duplicates'
    }
    $url = "$($SupabaseUrl.TrimEnd('/'))/rest/v1/memories"
    try {
        Invoke-RestMethod -Uri $url -Method Post -Headers $headers `
            -Body ($body | ConvertTo-Json -Depth 4) -ErrorAction Stop | Out-Null
    } catch {
        Write-Warning "decision memory write failed: $_"
    }
}

# ---------------------------------------------------------------------------
# Outcome recording -- direct PostgREST insert (anon, RLS-gated by source_provenance)
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
        # Strip only matched outer quote pairs so values like "it's" or
        # base64 padding ending in '=' survive verbatim.
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

function Write-OutcomeRecord {
    [CmdletBinding()]
    param(
        [string]$SupabaseUrl,
        [string]$SupabaseKey,
        [string]$Repo,
        [string]$Status,        # success | partial | failure
        [string]$Summary,
        [hashtable]$LlmMetrics, # @{ input_tokens; output_tokens; cache_read; cache_creation; model }
        [string[]]$ExtraTags = @(),
        [string]$RunId
    )

    if (-not $SupabaseUrl -or -not $SupabaseKey) {
        Write-Warning "SUPABASE_URL/SUPABASE_KEY missing -- skipping outcome_record write."
        return $null
    }

    $tags = @('sandcastle', 'afk') + $ExtraTags

    $body = @{
        task_type         = 'autonomous'
        task_description  = "sandcastle:$Repo watchdog run $RunId"
        outcome_status    = $Status
        outcome_summary   = $Summary
        project           = $Repo
        pattern_tags      = $tags
        # Token metrics ride in lessons until task_outcomes gains a dedicated
        # llm jsonb column -- slice 4 keeps the schema untouched on purpose.
        # `lessons` carries token metrics as JSON until task_outcomes gains a
        # dedicated llm jsonb column. Stable shape consumers can rely on:
        #   { input_tokens, output_tokens, cache_read_input_tokens,
        #     cache_creation_input_tokens, model }
        lessons           = ($LlmMetrics | ConvertTo-Json -Compress)
        source_provenance = "sandcastle:watchdog:$RunId"
    }
    $headers = @{
        apikey          = $SupabaseKey
        Authorization   = "Bearer $SupabaseKey"
        'Content-Type'  = 'application/json'
        Prefer          = 'return=representation'
    }

    $url = "$($SupabaseUrl.TrimEnd('/'))/rest/v1/task_outcomes"
    $resp = Invoke-RestMethod -Uri $url -Method Post -Headers $headers -Body ($body | ConvertTo-Json -Depth 6) -ErrorAction Stop
    return $resp
}

# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

function Invoke-Watchdog {
    [CmdletBinding()]
    param(
        [string]$Repo,
        [int]$MaxIterations,
        [string]$Model,
        [string]$WindowEnd,
        [int]$DockerTimeoutSec,
        [int]$OllamaTimeoutSec,
        # Slice 5 (#543) tier overrides; empty disables the corresponding tier.
        [string]$Tier1Model,
        [string]$Tier2Provider,
        # 2026-05-14: when set, skip Tier 0/1 Ollama entirely and run Tier 2
        # (DeepSeek / Anthropic API) as the primary invocation. Local Ollama
        # models fail the real-Claude-Code tool_use fidelity check — see memory
        # ollama_bench_must_measure_tool_use_fidelity. Default $false preserves
        # the original OOM-escalation chain used by existing tests.
        [switch]$Tier2AsPrimary,
        # #972: run the Anthropic subscription Agent SDK credit as the primary
        # tier (skips Ollama like -Tier2AsPrimary) with DeepSeek as fallback.
        # Senior to -Tier2AsPrimary when both are set.
        [switch]$SubscriptionPrimary,
        [string]$SubscriptionModel = 'claude-opus-4-8',
        [ValidateSet('low', 'medium', 'high', 'max')]
        [string]$SubscriptionEffort = 'medium',
        # #572: runtime-dir retention; env var SANDCASTLE_RUNTIME_RETENTION wins.
        [int]$RuntimeRetention = 30
    )

    $repoRoot = Get-RepoRoot -Repo $Repo
    $stamp = (Get-Date).ToString('yyyyMMdd-HHmmss')

    $retention = $RuntimeRetention
    if ($env:SANDCASTLE_RUNTIME_RETENTION) {
        $parsed = 0
        if ([int]::TryParse($env:SANDCASTLE_RUNTIME_RETENTION, [ref]$parsed)) {
            $retention = $parsed
        }
    }
    $runtimeRoot = Join-Path $repoRoot '.sandcastle/runtime'
    $pruned = Invoke-RuntimeSweep -RuntimeRoot $runtimeRoot -Keep $retention
    if ($pruned.Count -gt 0) {
        Write-Host "[watchdog] runtime-sweep: pruned $($pruned.Count) stale dirs (kept $retention)"
    }

    $runtimeDir = New-RuntimeDir -RepoRoot $repoRoot -Stamp $stamp
    $logFile = Join-Path $runtimeDir 'run.log'
    $resultFile = Join-Path $runtimeDir 'result.json'
    $runId = "$Repo-watchdog-$stamp"

    $envVars = Read-DotEnvFile -Path (Join-Path $repoRoot '.sandcastle/.env')
    $supabaseUrl = $envVars['SUPABASE_URL']
    $supabaseKey = $envVars['SUPABASE_KEY']
    $tgToken     = $envVars['TELEGRAM_BOT_TOKEN']
    $tgChatId    = $envVars['TELEGRAM_CHAT_ID']
    $oauthToken  = $envVars['CLAUDE_CODE_OAUTH_TOKEN']
    if ($env:SUPABASE_URL)            { $supabaseUrl = $env:SUPABASE_URL }
    if ($env:SUPABASE_KEY)            { $supabaseKey = $env:SUPABASE_KEY }
    if ($env:TELEGRAM_BOT_TOKEN)      { $tgToken     = $env:TELEGRAM_BOT_TOKEN }
    if ($env:TELEGRAM_CHAT_ID)        { $tgChatId    = $env:TELEGRAM_CHAT_ID }
    if ($env:CLAUDE_CODE_OAUTH_TOKEN) { $oauthToken  = $env:CLAUDE_CODE_OAUTH_TOKEN }

    $windowEndDt = Resolve-WindowEnd -WindowEnd $WindowEnd

    function Record([string]$status, [string]$summary, [hashtable]$llm, [string]$reason) {
        try {
            Write-OutcomeRecord -SupabaseUrl $supabaseUrl -SupabaseKey $supabaseKey `
                -Repo $Repo -Status $status -Summary $summary -LlmMetrics $llm `
                -RunId $runId | Out-Null
        } catch {
            Write-Warning "outcome_record write failed: $_"
        }
        if ((Test-IsInfraDown -Reason $reason)) {
            $msg = "[sandcastle:$Repo] $reason | run=$runId | log=$logFile"
            try {
                Send-TelegramAlert -BotToken $tgToken -ChatId $tgChatId -Message $msg | Out-Null
            } catch {
                Write-Warning "telegram alert failed: $_"
            }
        }
    }

    # 1. Docker
    if (-not (Test-DockerRunning)) {
        Write-Host "[watchdog] Docker not running -- autostarting."
        Start-DockerDesktop
        if (-not (Wait-DockerReady -TimeoutSec $DockerTimeoutSec)) {
            Record 'failure' "docker-down: daemon not ready within ${DockerTimeoutSec}s" @{} 'docker-down'
            throw "docker-down: daemon did not come up within ${DockerTimeoutSec}s"
        }
    }

    # 2. Resolve Tier 2 primary (DeepSeek / Anthropic API) — when set, the
    #    watchdog skips local Ollama tiers entirely. Local Ollama failed the
    #    real-Claude-Code tool_use fidelity check on qwen2.5-coder:14b (markdown
    #    JSON fence) and qwen3-coder:30b (Hermes-XML) — see memory
    #    ollama_bench_must_measure_tool_use_fidelity + smoke 2026-05-14. Remote
    #    Anthropic-compat endpoints (DeepSeek native, Anthropic itself) emit
    #    structured tool_use blocks reliably.
    $repoSlug = switch ($Repo) {
        'jarvis'   { 'your-username/your-repo' }
        'your-second-repo' { 'your-username/your-second-repo' }
        default    { '' }
    }
    $tier2Primary = $null
    if ($Tier2AsPrimary -and $Tier2Provider) {
        # -Issue 0 == "no issue picked yet" -- Get-IssueLabels short-circuits to
        # @() in that case, so the use-claude-api flip stays issue-scoped.
        # Using 0 (explicit int) avoids the parser quirk of empty-string-to-int
        # coercion that varies across PowerShell versions.
        $tier2Primary = Resolve-Tier2Config -Provider $Tier2Provider -Issue 0 `
            -RepoSlug $repoSlug -EnvVars $envVars
        if (-not ($tier2Primary -and $tier2Primary.AuthToken)) {
            Write-Warning "[watchdog] Tier 2 ($Tier2Provider) requested as primary but config incomplete (key missing) — falling back to local Ollama tiers."
            $tier2Primary = $null
        } else {
            Write-Host "[watchdog] Tier 2 ($($tier2Primary.Provider): $($tier2Primary.Model)) is primary — Tier 0/1 Ollama disabled for this run."
            # Pre-flight billing probe (#955) -- deepseek only. Catch an exhausted
            # balance before booting a container, so the outcome is the actionable
            # `provider-billing` reason (alert fires) rather than a wasted container
            # boot ending in an opaque agent-side exit=1. Fail-open inside the probe.
            if ($tier2Primary.Provider -eq 'deepseek' -and
                -not (Test-DeepSeekBalance -ApiKey $tier2Primary.AuthToken)) {
                $reason = 'provider-billing: deepseek balance exhausted (pre-flight probe)'
                # Tag the outcome so pre-flight billing rows are distinguishable
                # from generic failures in Supabase, matching the in-run path
                # which sets $totalUsage.provider_billing (#956 review).
                Record 'failure' $reason @{ provider_billing = $true } $reason
                throw $reason
            }
        }
    }

    # 2b. Subscription primary (#972). The Anthropic Max Agent-SDK credit runs
    #     as the primary tier (bills CLAUDE_CODE_OAUTH_TOKEN, NOT the metered
    #     API) and Ollama is skipped — same rationale as Tier-2-primary. Senior
    #     to -Tier2AsPrimary: when both are set, subscription is primary and the
    #     configured Tier 2 (DeepSeek) becomes the credit-exhaustion fallback.
    $subscriptionFallback = $null
    if ($SubscriptionPrimary) {
        $tier2Primary = $null   # subscription supersedes Tier-2-as-primary
        $fallbackProvider = if ($Tier2Provider) { $Tier2Provider } else { 'deepseek' }
        $subscriptionFallback = Resolve-Tier2Config -Provider $fallbackProvider -Issue 0 `
            -RepoSlug $repoSlug -EnvVars $envVars
        if ($subscriptionFallback -and $subscriptionFallback.AuthToken) {
            Write-Host "[watchdog] Subscription ($SubscriptionModel, effort=$SubscriptionEffort) is primary — Ollama disabled; fallback=$($subscriptionFallback.Provider):$($subscriptionFallback.Model)."
        } else {
            $subscriptionFallback = $null
            Write-Host "[watchdog] Subscription ($SubscriptionModel, effort=$SubscriptionEffort) is primary — Ollama disabled; no DeepSeek fallback configured (key missing)."
        }
    }

    # 3. Ollama (skipped when Tier 2 or subscription is primary — no local model)
    if (-not $tier2Primary -and -not $SubscriptionPrimary) {
        if (-not (Test-OllamaRunning)) {
            Write-Host "[watchdog] Ollama not running -- autostarting."
            Start-OllamaServer
            if (-not (Wait-OllamaReady -TimeoutSec $OllamaTimeoutSec)) {
                Record 'failure' "ollama-down: server not ready within ${OllamaTimeoutSec}s" @{} 'ollama-down'
                throw "ollama-down: server did not come up within ${OllamaTimeoutSec}s"
            }
        }
    }

    # 4. Iterate (soft-stop on window expiry between iterations)
    $totalUsage = @{ input_tokens = 0; output_tokens = 0; cache_read_input_tokens = 0; cache_creation_input_tokens = 0; model = $Model }
    $allCommits = @()
    $branch = $null
    $iter = 0
    $partialReason = $null
    $queueDrained = $false     # agent emitted the completion signal (queue empty)
    $tierCompleted = $null    # 'subscription' | 'tier0' | 'tier1' | 'tier2:deepseek' | 'tier2:claude'

    while ($iter -lt $MaxIterations) {
        if (Test-WindowExpired -WindowEnd $windowEndDt) {
            $partialReason = 'window-expired'
            break
        }

        $iter++
        Write-Host "[watchdog] iteration $iter/$MaxIterations"

        if ($SubscriptionPrimary) {
            # ----- Subscription primary: Anthropic Max Agent-SDK credit -----
            # -AuthMode subscription => main.mts injects ONLY the OAuth token; no
            # BaseUrl/AuthToken (those would route to the metered API).
            # -TargetIssue '' (free-pick) is intentional and matches the tier0 /
            # tier2Primary primaries below: each *iteration* lets the agent grab a
            # fresh AFK-queue issue. A within-iteration escalation (the fallback
            # block just below) pins to $targetIssue so the SAME issue is retried;
            # iteration N+1 only runs after iteration N succeeded (a failure throws
            # at the shared handler below), so re-using the prior issue would
            # re-attempt one that already has a PR.
            $invocation = Invoke-Sandcastle -RepoRoot $repoRoot -Model $SubscriptionModel `
                -MaxIterations 1 -ResultFile $resultFile -LogFile $logFile -RunId $runId `
                -AuthMode 'subscription' -Effort $SubscriptionEffort `
                -TargetIssue ''
            $tierUsed = 'subscription'
            # Guard the property access: a failed run returns result=$null. This is
            # harmless under default strictness (PS returns $null for $null.branch),
            # but a future Set-StrictMode would turn it into a PropertyNotFound throw
            # *before* the fallback block below — silently bypassing the DeepSeek
            # safety net on credit exhaustion. Keep it explicit.
            $targetIssue = if ($invocation.result) { Get-IssueFromBranch -Branch $invocation.result.branch } else { $null }
            $oomDetected = $false

            # Fallback: ANY subscription failure (credit exhausted -> hard-stop
            # when overflow is disabled, or a transient API error) drops to the
            # DeepSeek endpoint for this iteration. One bounded retry; self-heals
            # transient failures, mild waste if the credit is truly exhausted.
            # NB the exact credit-exhaustion signature is not yet characterized
            # (mirror of the DeepSeek-402 silent-failure note) — monitor (#972).
            if (-not $invocation.ok -and $subscriptionFallback) {
                Write-Host "[watchdog] subscription tier failed ($($invocation.reason)) -- falling back to $($subscriptionFallback.Provider):$($subscriptionFallback.Model)"
                $invocation = Invoke-Sandcastle -RepoRoot $repoRoot -Model $subscriptionFallback.Model `
                    -MaxIterations 1 -ResultFile $resultFile -LogFile $logFile -RunId $runId `
                    -BaseUrl $subscriptionFallback.BaseUrl -AuthToken $subscriptionFallback.AuthToken `
                    -AuthMode 'endpoint' -TargetIssue ([string]$targetIssue)
                # Compound attribution: a bare "tier2:deepseek" would hide that the
                # subscription tier was attempted first, so a Supabase trace on a
                # double-failure run could not tell "DeepSeek-primary failed" from
                # "subscription failed THEN DeepSeek failed". Preserve both.
                $tierUsed = "subscription>tier2:$($subscriptionFallback.Provider)"
                if (-not $targetIssue -and $invocation.result) {
                    $targetIssue = Get-IssueFromBranch -Branch $invocation.result.branch
                }
            }
            # No explicit `else { throw }` for the no-fallback case: a failed
            # $invocation with $subscriptionFallback = $null falls through to the
            # shared failure handler below (`if (-not $invocation.ok)`), which
            # records the 'failure' outcome AND throws. Throwing early here would
            # skip that Record write — fail-fast is intentional, not accidental.
        } elseif ($tier2Primary) {
            # ----- Tier 2 primary: remote Anthropic-compat endpoint (DeepSeek / Anthropic API) -----
            $invocation = Invoke-Sandcastle -RepoRoot $repoRoot -Model $tier2Primary.Model `
                -MaxIterations 1 -ResultFile $resultFile -LogFile $logFile -RunId $runId `
                -BaseUrl $tier2Primary.BaseUrl -AuthToken $tier2Primary.AuthToken `
                -TargetIssue ''
            $tierUsed = "tier2:$($tier2Primary.Provider)"
            # Same null-guard as the subscription path: a failed run returns
            # result=$null — harmless under default strictness, a PropertyNotFound
            # throw under a future Set-StrictMode. Guard all three call sites.
            $targetIssue = if ($invocation.result) { Get-IssueFromBranch -Branch $invocation.result.branch } else { $null }
            $oomDetected = $false
        } else {
            # ----- Tier 0: primary Ollama model -----
            $invocation = Invoke-Sandcastle -RepoRoot $repoRoot -Model $Model `
                -MaxIterations 1 -ResultFile $resultFile -LogFile $logFile -RunId $runId `
                -TargetIssue ''
            $tierUsed = 'tier0'
            $targetIssue = if ($invocation.result) { Get-IssueFromBranch -Branch $invocation.result.branch } else { $null }
            $oomDetected = (-not $invocation.ok) -and (Test-IsOOM -Reason $invocation.reason -LogFile $logFile)
        }

        # ----- Tier 1: smaller Ollama model on OOM/crash (only when Tier 2 is NOT primary) -----
        if (-not $tier2Primary -and -not $SubscriptionPrimary -and -not $invocation.ok -and $Tier1Model -and $oomDetected) {
            Write-Host "[watchdog] Tier 0 OOM detected -- escalating to Tier 1 model: $Tier1Model"
            $invocation = Invoke-Sandcastle -RepoRoot $repoRoot -Model $Tier1Model `
                -MaxIterations 1 -ResultFile $resultFile -LogFile $logFile -RunId $runId `
                -TargetIssue ([string]$targetIssue)
            $tierUsed = 'tier1'
            if (-not $targetIssue -and $invocation.result) {
                $targetIssue = Get-IssueFromBranch -Branch $invocation.result.branch
            }
        }

        # ----- Tier 2: remote API on persistent failure -----
        # Reached when the chain saw a Tier-0 OOM (gate above set $oomDetected)
        # and the chain still hasn't recovered. Tier 1 may have been skipped
        # entirely (no $Tier1Model) or it may have run and failed for any
        # reason -- both qualify per AC #3 ("persistent failure after Tier 1").
        if (-not $tier2Primary -and -not $SubscriptionPrimary -and -not $invocation.ok -and $oomDetected -and $Tier2Provider) {
            $tier2 = Resolve-Tier2Config -Provider $Tier2Provider -Issue $targetIssue `
                -RepoSlug $repoSlug -EnvVars $envVars
            if ($tier2 -and $tier2.AuthToken) {
                # Pre-flight billing probe on the OOM-escalation lane too (#956
                # review). The in-run classifier below still fires the alert on a
                # drained balance, but without this probe the escalation boots a
                # container against a dead provider first -- a wasted boot. Mirror
                # the primary-path check; deepseek only; fail-open inside.
                if ($tier2.Provider -eq 'deepseek' -and
                    -not (Test-DeepSeekBalance -ApiKey $tier2.AuthToken)) {
                    $reason = 'provider-billing: deepseek balance exhausted (pre-flight probe, OOM escalation)'
                    # Preserve accumulated Tier 0/1 token counts in the outcome
                    # instead of a fresh hash that silently drops them.
                    $totalUsage.provider_billing = $true
                    $totalUsage.tier = $tierUsed
                    Record 'failure' $reason $totalUsage $reason
                    throw $reason
                }
                Write-Host "[watchdog] Tier 1 failed -- escalating to Tier 2 ($($tier2.Provider): $($tier2.Model))"
                $invocation = Invoke-Sandcastle -RepoRoot $repoRoot -Model $tier2.Model `
                    -MaxIterations 1 -ResultFile $resultFile -LogFile $logFile -RunId $runId `
                    -BaseUrl $tier2.BaseUrl -AuthToken $tier2.AuthToken `
                    -TargetIssue ([string]$targetIssue)
                $tierUsed = "tier2:$($tier2.Provider)"
            } else {
                Write-Warning "[watchdog] Tier 2 requested but config incomplete (provider=$Tier2Provider, key set=$([bool]$tier2.AuthToken)); skipping."
            }
        }

        if (-not $invocation.ok) {
            $reason = if ($invocation.reason) { $invocation.reason } else { "exit=$($invocation.exitCode)" }
            # Diagnose the crash from the run.log tail so a nightly `exit=1` is
            # actionable from Supabase alone (the routine-host run.log is not
            # reachable from the orchestrator host). Redact before persisting.
            $logTail  = Get-LogTail -LogFile $logFile
            $errClass = Get-SandcastleErrorClass -Text "$reason`n$logTail"
            # Billing classification (#955): scan the RAW reason+log tail (before
            # redaction) for a provider out-of-balance signature. Promoting the
            # reason to `provider-billing` makes Test-IsInfraDown match, which is
            # what fires the Telegram alert -- otherwise a 402 looks like a
            # generic agent-side exit=1 and stays silent.
            if (Test-IsProviderBilling -Text "$reason`n$logTail") {
                $reason = "provider-billing: $reason"
                $totalUsage.provider_billing = $true
                # A 402 is provider infra, not an agent-side crash -- clear the
                # sandcastle error class so Supabase rows don't carry contradictory
                # signals (e.g. error_class=AgentError + provider_billing=true).
                $errClass = ''
            }
            # #972: also redact the subscription OAuth token when present (tier2Primary
            # and tier2 cover the DeepSeek/endpoint path; $oauthToken covers subscription).
            $extraSecrets = @($tier2Primary.AuthToken, $tier2.AuthToken, $oauthToken) | Where-Object { $_ }
            $logTail  = Protect-LogTail -Text $logTail -Secrets (@($supabaseKey, $supabaseUrl, $tgToken) + $extraSecrets)
            # Full chain failure: label the issue if we know which one was attempted.
            # Skip labeling on billing failures -- "too-large-for-local" means the
            # model is too small, not that the provider account is drained.
            $labelApplied = $false
            if ($targetIssue -and $repoSlug -and -not $totalUsage.provider_billing) {
                $labelApplied = Add-IssueLabel -Issue $targetIssue -Label 'too-large-for-local' -RepoSlug $repoSlug
            }
            $totalUsage.tier = $tierUsed
            $totalUsage.too_large_for_local = $labelApplied
            if ($errClass) { $totalUsage.error_class = $errClass }
            if ($logTail)  { $totalUsage.diag_tail   = $logTail }
            $classNote = if ($errClass) { " class=$errClass" } else { '' }
            Record 'failure' "sandcastle invocation failed: $reason (tier=$tierUsed issue=$targetIssue label=$labelApplied)$classNote" $totalUsage $reason
            throw "sandcastle invocation failed: $reason"
        }

        $tierCompleted = $tierUsed
        $r = $invocation.result
        $branch = $r.branch
        $totalUsage.tier = $tierCompleted
        if ($tierCompleted -ne 'tier0') {
            $totalUsage.model = $r.model    # not always set; best-effort
            if (-not $totalUsage.model) {
                # Fall back to tier label so reviewers see which tier won.
                $totalUsage.model = $tierCompleted
            }
        }
        if ($r.commits) { $allCommits += $r.commits }
        foreach ($it in $r.iterations) {
            if ($it.usage) {
                $totalUsage.input_tokens               += [int]$it.usage.inputTokens
                $totalUsage.output_tokens              += [int]$it.usage.outputTokens
                $totalUsage.cache_read_input_tokens    += [int]$it.usage.cacheReadInputTokens
                $totalUsage.cache_creation_input_tokens+= [int]$it.usage.cacheCreationInputTokens
            }
        }

        # Honor the agent's completion signal (prompt.md §Done): it fires only
        # when the AFK queue is drained -- nothing left to pick. Re-invoking on
        # an empty queue is pure downside: wasted runs plus a fresh crash surface
        # (e.g. an idle-timeout while the model sits on an empty queue), and a
        # crash on any redundant iteration would overwrite this night's verdict
        # with a spurious `failure` and discard the work already done. Stop here
        # and let the success path record the run. (A flaky model that emits the
        # signal early loses at most one extra issue this night; tomorrow's run
        # picks it back up -- far cheaper than the manufactured-failure cascade.)
        if ($r.completionSignal) {
            $queueDrained = $true
            Write-Host "[watchdog] agent signaled queue drained after iteration $iter -- stopping early."
            break
        }
    }

    # ---- 5. Pytest gate (your-second-repo only) ----
    $pytestResult = $null
    if (-not $partialReason -and $Repo -eq 'your-second-repo') {
        $pytestResult = Invoke-PytestGate -RepoRoot $repoRoot -Branch $branch
        $totalUsage.gates = @{ pytest = $pytestResult }

        if (-not $pytestResult.passed) {
            $pytestReason = if ($pytestResult.collectionError) { 'tests-broken' } else { 'tests-failing' }
            $pytestLabel = $pytestReason

            # Close the PR if one was opened
            if ($branch) {
                Stop-AgentPR -Branch $branch -RepoSlug $repoSlug
            }

            # Label the issue
            if ($targetIssue -and $repoSlug) {
                Add-IssueLabel -Issue $targetIssue -Label $pytestLabel -RepoSlug $repoSlug | Out-Null
                # Drop status:in-progress
                Invoke-Gh issue edit $targetIssue --repo $repoSlug --remove-label 'status:in-progress' | Out-Null
            }

            $summary = "pytest-gate:$pytestReason -- branch=$branch $($pytestResult.summary)"
            Record 'partial' $summary $totalUsage ''

            # M6/AC7: record decision in Supabase memory for next-session recall
            Write-SandcastleDecisionMemory `
                -SupabaseUrl $supabaseUrl -SupabaseKey $supabaseKey `
                -Project 'your-second-repo' `
                -Name "pytest-gate:${pytestReason}:$branch" `
                -Description "pytest gate blocked sandcastle PR on $branch ($pytestReason)" `
                -Content "Sandcastle run $runId blocked PR on branch $branch for your-second-repo. Reason: $pytestReason. $($pytestResult.summary)" `
                -RunId $runId

            Write-Host "[watchdog] $summary"
            return
        }

        Write-Host "[watchdog] pytest gate passed ($($pytestResult.testsRun) tests)"
    }

    if ($partialReason) {
        $summary = "partial:$partialReason -- branch=$branch iterations=$iter commits=$($allCommits.Count)"
        Record 'partial' $summary $totalUsage ''
        Write-Host "[watchdog] $summary"
        return
    }

    if ($queueDrained -and $allCommits.Count -eq 0) {
        # Clean "nothing to do" heartbeat -- the queue was empty/all-attempted.
        # Recorded as success (the run worked), not failure, so it neither
        # pollutes the outcome log nor masks real crashes.
        $summary = "success:idle -- queue drained, no eligible issues (iterations=$iter)"
    } else {
        $summary = "success -- branch=$branch iterations=$iter commits=$($allCommits.Count)"
    }
    Record 'success' $summary $totalUsage ''
    Write-Host "[watchdog] $summary"
}

# Entry guard: only run when invoked as a script with a -Repo argument.
# Dot-sourcing without arguments (Pester) loads functions but does not execute.
if (-not $NoExecute -and $Repo) {
    Invoke-Watchdog -Repo $Repo -MaxIterations $MaxIterations -Model $Model `
        -WindowEnd $WindowEnd -DockerTimeoutSec $DockerTimeoutSec `
        -OllamaTimeoutSec $OllamaTimeoutSec `
        -Tier1Model $Tier1Model -Tier2Provider $Tier2Provider `
        -Tier2AsPrimary:$Tier2AsPrimary `
        -SubscriptionPrimary:$SubscriptionPrimary `
        -SubscriptionModel $SubscriptionModel -SubscriptionEffort $SubscriptionEffort `
        -RuntimeRetention $RuntimeRetention
}
