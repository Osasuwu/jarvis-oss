#!/usr/bin/env pwsh
<#
.SYNOPSIS
    Audit ANTHROPIC_API_KEY presence across all machine scopes and files.
    Default (no switch): dry-run — report only.
    -Apply: remove from safe locations (User/Machine env, ~/.claude/.env).
    Dotfiles are always report-only (manual removal).

.DESCRIPTION
    Enumerates all sources ANTHROPIC_API_KEY could be set from:
      - PowerShell User / Machine environment scopes
      - Current-process $env:ANTHROPIC_API_KEY
      - bash/zsh dotfiles (~/.bashrc, ~/.zshrc, ~/.profile, ~/.bash_profile, ~/.zprofile)
      - ~/.claude/.env and ~/.claude/**/*.env
      - Repo-root .env (warn-only)
      - ~/.config/claude/.env (Linux Claude Desktop)

    Output NEVER includes the full key value. Masking: first 4 + last 4 + key length.
#>

param(
    [switch]$Apply,
    [switch]$DryRun = $true
)

$ErrorActionPreference = 'Stop'

# ── Helpers ─────────────────────────────────────────────────────────────────

function Mask-Key([string]$key) {
    if ([string]::IsNullOrEmpty($key)) { return '(empty)' }
    if ($key.Length -le 12) { return $key.Substring(0, [Math]::Min(2, $key.Length)) + '...' + $key.Substring([Math]::Max($key.Length - 2, 0)) }
    $first4 = $key.Substring(0, 4)
    $last4  = $key.Substring($key.Length - 4)
    return "${first4}...${last4}  (len=$($key.Length))"
}

function Write-Result($label, $source, $value, [ConsoleColor]$color = 'Yellow') {
    if ($null -eq $value) { return }
    Write-Host ("  [{0,-12}] {1,-30} {2}" -f $label, $source, (Mask-Key $value)) -ForegroundColor $color
}

# ── Sources ─────────────────────────────────────────────────────────────────

$findings = @()  # [pscustomobject]@{ Source; Scope; Value; Remediable }>

# 1. PowerShell environment scopes
$scopes = @(
    @{ Name = 'User (HKCU) scope';    Scope = 'User' }
    @{ Name = 'Machine (HKLM) scope'; Scope = 'Machine' }
    @{ Name = 'Current process';       Scope = 'Process' }
)
foreach ($s in $scopes) {
    $val = [Environment]::GetEnvironmentVariable('ANTHROPIC_API_KEY', $s.Scope)
    if ($null -ne $val -and '' -ne $val) {
        $findings += [pscustomobject]@{ Source = $s.Name; Scope = $s.Scope; Value = $val; Remediable = $s.Scope -ne 'Process' }
    } elseif ('' -eq $val) {
        $findings += [pscustomobject]@{ Source = $s.Name; Scope = $s.Scope; Value = ''; Remediable = $false }
    }
}

# 2. ~/.claude/.env and ~/.claude/**/*.env
$claudeDirs = @()
$homeEnv = Join-Path $env:USERPROFILE '.claude'
if (Test-Path $homeEnv) {
    $claudeDirs += Get-ChildItem -Path $homeEnv -Recurse -Filter '*.env' -ErrorAction SilentlyContinue
}
# Also check ~/.config/claude/ on Linux via WSL
$configClaude = Join-Path $env:USERPROFILE '.config\claude'
if (Test-Path $configClaude) {
    $claudeDirs += Get-ChildItem -Path $configClaude -Recurse -Filter '*.env' -ErrorAction SilentlyContinue
}
foreach ($f in $claudeDirs) {
    $content = Get-Content -Path $f.FullName -ErrorAction SilentlyContinue
    foreach ($line in $content) {
        if ($line -match '^ANTHROPIC_API_KEY=(.+)$') {
            $val = $matches[1].Trim('"', "'")
            $findings += [pscustomobject]@{ Source = $f.FullName; Scope = 'claude-env'; Value = $val; Remediable = $true }
        }
    }
}

# 3. Repo-root .env (warn-only, not remediable by this script)
$repoRoot = Split-Path -Parent $PSScriptRoot
$repoEnv = Join-Path $repoRoot '.env'
if (Test-Path $repoEnv) {
    $content = Get-Content -Path $repoEnv -ErrorAction SilentlyContinue
    foreach ($line in $content) {
        if ($line -match '^ANTHROPIC_API_KEY=(.+)$') {
            $val = $matches[1].Trim('"', "'")
            $findings += [pscustomobject]@{ Source = "$repoEnv (warn-only)"; Scope = 'repo-env'; Value = $val; Remediable = $false }
        }
    }
}

# 4. Dotfiles (bash/zsh profiles) — pattern match only, report file+line
$dotfiles = @(
    (Join-Path $env:USERPROFILE '.bashrc'),
    (Join-Path $env:USERPROFILE '.zshrc'),
    (Join-Path $env:USERPROFILE '.profile'),
    (Join-Path $env:USERPROFILE '.bash_profile'),
    (Join-Path $env:USERPROFILE '.zprofile')
)
foreach ($df in $dotfiles) {
    if (-not (Test-Path $df)) { continue }
    $lines = Get-Content -Path $df -ErrorAction SilentlyContinue
    $lineNo = 0
    foreach ($line in $lines) {
        $lineNo++
        # Match ANTHROPIC_API_KEY assignments or exports
        if ($line -match '^(?:export\s+)?ANTHROPIC_API_KEY=') {
            $val = ''
            if ($line -match '=(.+)$') {
                $val = $matches[1].Trim('"', "'")
            }
            $findings += [pscustomobject]@{
                Source = "$df`:$lineNo"
                Scope  = 'dotfile'
                Value  = $val
                Remediable = $false  # never auto-remove from dotfiles
            }
        }
    }
}

# ── Report ──────────────────────────────────────────────────────────────────

Write-Host "`nANTHROPIC_API_KEY Audit Report" -ForegroundColor Cyan
Write-Host "==============================" -ForegroundColor Cyan
Write-Host "Mode: $(if ($Apply) { 'APPLY (mutating)' } else { 'DRY-RUN (default)' })" -ForegroundColor $(if ($Apply) { 'Red' } else { 'Green' })
Write-Host ""

$hits = @()
$empty = @()
$remediable = @()

foreach ($f in $findings) {
    if ([string]::IsNullOrEmpty($f.Value)) {
        $empty += $f
        Write-Result 'EMPTY' $f.Source $f.Value 'DarkYellow'
    } else {
        $hits += $f
        if ($f.Remediable) { $remediable += $f }
        Write-Result 'HIT' $f.Source $f.Value 'Yellow'
    }
}

# ── Summary line ────────────────────────────────────────────────────────────

Write-Host ""
if ($hits.Count -eq 0 -and $empty.Count -eq 0) {
    Write-Host "[OK] No ANTHROPIC_API_KEY detected." -ForegroundColor Green
} else {
    $hitList = ($hits | ForEach-Object { Mask-Key $_.Value }) -join ', '
    $emptyList = ($empty | ForEach-Object { "[$($_.Source)]" }) -join ', '
    Write-Host "[LEAK-RISK] $($hits.Count) hit(s) detected: $hitList" -ForegroundColor Yellow
    if ($empty.Count -gt 0) {
        Write-Host "[WARN] $($empty.Count) empty-string(s) at: $emptyList" -ForegroundColor DarkYellow
        Write-Host "  (login shell may pre-set ANTHROPIC_API_KEY='' — harmless but dusty)" -ForegroundColor DarkYellow
    }
}

# ── Remediation ─────────────────────────────────────────────────────────────

if ($hits.Count -eq 0) {
    Write-Host "`nNothing to remediate." -ForegroundColor Green
    exit 0
}

if (-not $Apply) {
    Write-Host "`nRun with -Apply to remove $($remediable.Count) remediable hit(s) from User/Machine scope and ~/.claude/.env." -ForegroundColor Cyan
    Write-Host "  (dotfiles not auto-cleaned — check the file:line above and remove manually.)" -ForegroundColor Cyan
    exit 0
}

# --Apply mode: remediate safe locations
Write-Host "`n--- Applying remediation ---" -ForegroundColor Red

foreach ($f in $hits) {
    if (-not $f.Remediable) {
        Write-Host "  [SKIP] $($f.Source) — not auto-remediated" -ForegroundColor DarkYellow
        continue
    }

    if ($f.Scope -eq 'User' -or $f.Scope -eq 'Machine') {
        [Environment]::SetEnvironmentVariable('ANTHROPIC_API_KEY', $null, $f.Scope)
        Write-Host "  [REMOVED] $($f.Scope) scope: ANTHROPIC_API_KEY" -ForegroundColor Green
    } elseif ($f.Scope -eq 'claude-env') {
        # Remove ANTHROPIC_API_KEY line from the .env file
        $path = $f.Source
        $lines = Get-Content -Path $path
        $newLines = $lines | Where-Object { $_ -notmatch '^ANTHROPIC_API_KEY=' }
        if ($newLines.Count -eq 0) {
            Remove-Item -Path $path -Force
            Write-Host "  [REMOVED] $path (file deleted — was only ANTHROPIC_API_KEY)" -ForegroundColor Green
        } else {
            $newLines | Set-Content -Path $path -NoNewline -Encoding UTF8
            # Restore trailing newline stripped by -NoNewline above
            Add-Content -Path $path -Value ''
            Write-Host "  [CLEANED] $path — ANTHROPIC_API_KEY line removed" -ForegroundColor Green
        }
    }
}

Write-Host "`nDotfiles not modified. Review and remove manually from lines above." -ForegroundColor Yellow
Write-Host "Re-run audit to confirm." -ForegroundColor Cyan
