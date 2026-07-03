# Pester tests for scripts/sandcastle/Register-SandcastleTask.ps1 (issue #865).
# Exercises argument assembly, device-guard logic, per-repo defaults, and
# quota-probe mode — no real Task Scheduler calls.
#
# Compatible with Pester 3.4 (built-in on Windows PowerShell 5.1).
#
# Run:
#   Invoke-Pester -Path tests/sandcastle/Register-SandcastleTask.Tests.ps1

$script:TaskPath = Join-Path $PSScriptRoot '..\..\scripts\sandcastle\Register-SandcastleTask.ps1'
# -Repo is mandatory in the default 'Sandcastle' parameter set; supply it at
# load so the script dot-sources under Windows PowerShell 5.1 / Pester 3.4
# (where an unsatisfied mandatory param blocks the load). -NoExecute short-
# circuits before any scheduler call, and the helper functions take -Repo
# explicitly, so this load-time value never leaks into a test assertion.
. $script:TaskPath -Repo jarvis -NoExecute

# ---------------------------------------------------------------------------
# Per-repo defaults
# ---------------------------------------------------------------------------

Describe 'Get-SandcastleDefaults' {
    It 'returns jarvis defaults (18:00 start, 01:00 end, Sandcastle-Jarvis)' {
        $d = Get-SandcastleDefaults -Repo 'jarvis'
        $d.Start    | Should Be '18:00'
        $d.End      | Should Be '01:00'
        $d.TaskName | Should Be 'Sandcastle-Jarvis'
    }

    It 'returns redrobot defaults (01:00 start, 08:00 end, Sandcastle-Redrobot)' {
        $d = Get-SandcastleDefaults -Repo 'redrobot'
        $d.Start    | Should Be '01:00'
        $d.End      | Should Be '08:00'
        $d.TaskName | Should Be 'Sandcastle-Redrobot'
    }
}

# ---------------------------------------------------------------------------
# Device config parsing (device.json name extraction)
# ---------------------------------------------------------------------------

Describe 'Get-ConfigDeviceName' {
    It 'returns device name from valid device.json' {
        $tmp = Join-Path $env:TEMP "device-test-$([guid]::NewGuid()).json"
        '{"name":"my-workstation"}' | Set-Content -Path $tmp -Encoding UTF8
        try {
            $name = Get-ConfigDeviceName -DeviceJsonPath $tmp
            $name | Should Be 'my-workstation'
        } finally {
            Remove-Item -LiteralPath $tmp -ErrorAction SilentlyContinue
        }
    }

    It 'returns null when device.json is missing' {
        $name = Get-ConfigDeviceName -DeviceJsonPath (Join-Path $env:TEMP "missing-$([guid]::NewGuid()).json")
        $name | Should BeNullOrEmpty
    }

    It 'returns null when device.json is unparsable' {
        $tmp = Join-Path $env:TEMP "device-test-$([guid]::NewGuid()).json"
        'not valid json {{{' | Set-Content -Path $tmp -Encoding UTF8
        try {
            $name = Get-ConfigDeviceName -DeviceJsonPath $tmp
            $name | Should BeNullOrEmpty
        } finally {
            Remove-Item -LiteralPath $tmp -ErrorAction SilentlyContinue
        }
    }

    It 'reads the name field from a real config/device.json shape' {
        $tmp = Join-Path $env:TEMP "device-test-$([guid]::NewGuid()).json"
        '{ "name": "Routine-Host", "os": "windows", "role": "production" }' |
            Set-Content -Path $tmp -Encoding UTF8
        try {
            $name = Get-ConfigDeviceName -DeviceJsonPath $tmp
            $name | Should Be 'Routine-Host'
        } finally {
            Remove-Item -LiteralPath $tmp -ErrorAction SilentlyContinue
        }
    }
}

# ---------------------------------------------------------------------------
# PowerShell executable resolution
# ---------------------------------------------------------------------------

Describe 'Get-PowerShellExe' {
    It 'prefers pwsh over powershell.exe when pwsh is available' {
        Mock Get-Command {
            if ($Name -eq 'pwsh') { return [pscustomobject]@{ Source = 'C:\Program Files\PowerShell\7\pwsh.exe' } }
            throw "unexpected Get-Command for '$Name'"
        }
        $exe = Get-PowerShellExe
        $exe | Should Be 'C:\Program Files\PowerShell\7\pwsh.exe'
    }

    It 'falls back to powershell.exe when pwsh is absent' {
        $script:fallbackCalled = $false
        Mock Get-Command {
            if ($Name -eq 'pwsh') { return $null }
            if ($Name -eq 'powershell') {
                $script:fallbackCalled = $true
                return [pscustomobject]@{ Source = 'C:\Windows\System32\WindowsPowerShell\v1.0\powershell.exe' }
            }
            throw "unexpected Get-Command for '$Name'"
        }
        $exe = Get-PowerShellExe
        $exe | Should Be 'C:\Windows\System32\WindowsPowerShell\v1.0\powershell.exe'
        $script:fallbackCalled | Should Be $true
    }

    It 'throws when both pwsh and powershell.exe are absent' {
        Mock Get-Command {
            if ($Name -eq 'pwsh') { return $null }
            if ($Name -eq 'powershell') { throw 'powershell not found' }
            throw "unexpected Get-Command for '$Name'"
        }
        { Get-PowerShellExe } | Should Throw
    }
}

# ---------------------------------------------------------------------------
# Sandcastle-mode argument assembly
# ---------------------------------------------------------------------------

Describe 'Format-SandcastleActionArgs' {
    It 'builds minimal args for jarvis (no tiers)' {
        # -SubscriptionPrimary:$false isolates the base arg shape from the
        # default subscription block (covered separately below).
        $args = Format-SandcastleActionArgs -WatchdogPath 'D:\repo\scripts\sandcastle\Run-Sandcastle.ps1' `
            -Repo 'jarvis' -Model 'qwen3-coder:30b' -Tier1Model '' -Tier2Provider '' `
            -MaxIterations 5 -WindowEnd '01:00' -SubscriptionPrimary $false
        $args[0] | Should Be '-NoProfile'
        $args[2] | Should Be 'Bypass'
        $args[3] | Should Be '-File'
        $args[4] | Should Be '"D:\repo\scripts\sandcastle\Run-Sandcastle.ps1"'
        $args[5] | Should Be '-Repo'
        $args[6] | Should Be 'jarvis'
        $args[7] | Should Be '-Model'
        $args[8] | Should Be 'qwen3-coder:30b'
        $args[9] | Should Be '-MaxIterations'
        $args[10] | Should Be 5
        $args[11] | Should Be '-WindowEnd'
        $args[12] | Should Be '01:00'
        $args.Count | Should Be 13   # lone -NoProfile switch + 6 parameter pairs
    }

    It 'includes -Tier1Model when specified' {
        $args = Format-SandcastleActionArgs -WatchdogPath 'w.ps1' -Repo 'jarvis' -Model 'm' `
            -Tier1Model 'qwen2.5-coder:7b' -Tier2Provider '' -MaxIterations 3 -WindowEnd '01:00'
        $idx = [array]::IndexOf($args, '-Tier1Model')
        $idx | Should Not Be -1
        $args[$idx + 1] | Should Be 'qwen2.5-coder:7b'
    }

    It 'legacy: -SubscriptionPrimary:$false + provider set -> -Tier2Provider and -Tier2AsPrimary' {
        $args = Format-SandcastleActionArgs -WatchdogPath 'w.ps1' -Repo 'jarvis' -Model 'm' `
            -Tier1Model '' -Tier2Provider 'deepseek' -MaxIterations 3 -WindowEnd '01:00' `
            -SubscriptionPrimary $false
        $idx = [array]::IndexOf($args, '-Tier2Provider')
        $idx | Should Not Be -1
        $args[$idx + 1] | Should Be 'deepseek'
        ($args -contains '-Tier2AsPrimary')    | Should Be $true
        ($args -contains '-SubscriptionPrimary') | Should Be $false
    }

    It 'legacy: uses claude as Tier2 provider when specified (-SubscriptionPrimary:$false)' {
        $args = Format-SandcastleActionArgs -WatchdogPath 'w.ps1' -Repo 'jarvis' -Model 'm' `
            -Tier1Model '' -Tier2Provider 'claude' -MaxIterations 3 -WindowEnd '01:00' `
            -SubscriptionPrimary $false
        $idx = [array]::IndexOf($args, '-Tier2Provider')
        $idx | Should Not Be -1
        $args[$idx + 1] | Should Be 'claude'
        ($args -contains '-Tier2AsPrimary') | Should Be $true
    }

    It 'default (no flag): DeepSeek is primary -- emits -Tier2AsPrimary, NOT -SubscriptionPrimary' {
        # 2026-06-17: default reverted to DeepSeek-primary (#972). Anthropic
        # shelved the separate Agent-SDK automation quota at launch, so the
        # subscription path is dormant opt-in code, not the default tier.
        $args = Format-SandcastleActionArgs -WatchdogPath 'w.ps1' -Repo 'jarvis' -Model 'm' `
            -Tier1Model '' -Tier2Provider 'deepseek' -MaxIterations 3 -WindowEnd '01:00'
        ($args -contains '-SubscriptionPrimary') | Should Be $false
        ($args -contains '-SubscriptionModel')   | Should Be $false
        $tIdx = [array]::IndexOf($args, '-Tier2Provider')
        $tIdx | Should Not Be -1
        $args[$tIdx + 1] | Should Be 'deepseek'
        ($args -contains '-Tier2AsPrimary') | Should Be $true
    }

    It 'opt-in: -SubscriptionPrimary $true emits -SubscriptionPrimary + model/effort, DeepSeek as fallback, NOT -Tier2AsPrimary' {
        $args = Format-SandcastleActionArgs -WatchdogPath 'w.ps1' -Repo 'jarvis' -Model 'm' `
            -Tier1Model '' -Tier2Provider 'deepseek' -MaxIterations 3 -WindowEnd '01:00' `
            -SubscriptionPrimary $true
        ($args -contains '-SubscriptionPrimary') | Should Be $true
        $mIdx = [array]::IndexOf($args, '-SubscriptionModel')
        $mIdx | Should Not Be -1
        $args[$mIdx + 1] | Should Be 'claude-opus-4-8'
        $eIdx = [array]::IndexOf($args, '-SubscriptionEffort')
        $eIdx | Should Not Be -1
        $args[$eIdx + 1] | Should Be 'medium'
        # DeepSeek stays wired as fallback...
        $tIdx = [array]::IndexOf($args, '-Tier2Provider')
        $tIdx | Should Not Be -1
        $args[$tIdx + 1] | Should Be 'deepseek'
        # ...but NOT promoted to primary.
        ($args -contains '-Tier2AsPrimary') | Should Be $false
    }

    It 'opt-in: subscription model/effort overridable' {
        $args = Format-SandcastleActionArgs -WatchdogPath 'w.ps1' -Repo 'jarvis' -Model 'm' `
            -Tier1Model '' -Tier2Provider 'deepseek' -MaxIterations 3 -WindowEnd '01:00' `
            -SubscriptionPrimary $true -SubscriptionModel 'claude-sonnet-4-6' -SubscriptionEffort 'high'
        $mIdx = [array]::IndexOf($args, '-SubscriptionModel')
        $args[$mIdx + 1] | Should Be 'claude-sonnet-4-6'
        $eIdx = [array]::IndexOf($args, '-SubscriptionEffort')
        $args[$eIdx + 1] | Should Be 'high'
    }

    It 'builds redrobot args with correct repo value' {
        $args = Format-SandcastleActionArgs -WatchdogPath 'w.ps1' -Repo 'redrobot' -Model 'm' `
            -Tier1Model '' -Tier2Provider '' -MaxIterations 5 -WindowEnd '08:00'
        $idx = [array]::IndexOf($args, '-Repo')
        $idx | Should Not Be -1
        $args[$idx + 1] | Should Be 'redrobot'
    }

    It 'quotes watchdog path with spaces' {
        $args = Format-SandcastleActionArgs -WatchdogPath 'C:\Program Files\repo\Run-Sandcastle.ps1' `
            -Repo 'jarvis' -Model 'm' -Tier1Model '' -Tier2Provider '' `
            -MaxIterations 1 -WindowEnd '01:00'
        $args[4] | Should Match '^".*Run-Sandcastle.ps1"$'
    }
}

# ---------------------------------------------------------------------------
# Quota-probe mode argument assembly
# ---------------------------------------------------------------------------

Describe 'Format-QuotaProbeActionArgs' {
    It 'builds args with given script path and cache TTL' {
        $args = Format-QuotaProbeActionArgs -ProbeScript 'D:\repo\scripts\sandcastle\Quota-Probe.ps1' -CacheTTLMinutes 35
        $args[0] | Should Be '-NoProfile'
        $args[2] | Should Be 'Bypass'
        $args[3] | Should Be '-File'
        $args[4] | Should Be '"D:\repo\scripts\sandcastle\Quota-Probe.ps1"'
        $args[5] | Should Be '-CacheTTLMinutes'
        $args[6] | Should Be 35
        $args.Count | Should Be 7   # lone -NoProfile switch + 3 parameter pairs
    }

    It 'accepts custom interval-based cache TTL' {
        $args = Format-QuotaProbeActionArgs -ProbeScript 'q.ps1' -CacheTTLMinutes 20
        $args[5] | Should Be '-CacheTTLMinutes'
        $args[6] | Should Be 20
    }

    It 'quotes probe script path containing spaces' {
        $args = Format-QuotaProbeActionArgs -ProbeScript 'C:\Program Files\repo\Quota-Probe.ps1' -CacheTTLMinutes 30
        $args[4] | Should Match '^".*Quota-Probe.ps1"$'
    }
}

# ---------------------------------------------------------------------------
# NoExecute guard: loading the script without executing the main body
# ---------------------------------------------------------------------------

Describe 'NoExecute guard' {
    It 'loads functions without executing main body' {
        # Dot-sourcing with -NoExecute should define functions
        # but not throw about missing required params
        Get-ConfigDeviceName     -ErrorAction SilentlyContinue | Out-Null
        Get-SandcastleDefaults   -Repo 'jarvis' -ErrorAction SilentlyContinue | Out-Null
        Get-PowerShellExe        -ErrorAction SilentlyContinue | Out-Null
        Format-SandcastleActionArgs -WatchdogPath 'w' -Repo 'jarvis' -Model 'm' `
            -Tier1Model '' -Tier2Provider '' -MaxIterations 1 -WindowEnd '' -ErrorAction SilentlyContinue | Out-Null
        Format-QuotaProbeActionArgs -ProbeScript 'q' -CacheTTLMinutes 30 -ErrorAction SilentlyContinue | Out-Null
        # If we get here without errors, all functions loaded
        $true | Should Be $true
    }
}
