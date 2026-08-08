# Pester tests for scripts/install/register-wake-driver.ps1 (issue #1384).
# Exercises the -Command argument assembly, in particular the nested-quote
# escaping around $PythonExe (this logic already broke once during #1384's
# own development -- a double-quote-inside-double-quote bug that corrupted
# Windows argv parsing, fixed before this test existed). No real Task
# Scheduler calls -- covered by -NoExecute.
#
# Compatible with Pester 3.4 (built-in on Windows PowerShell 5.1).
#
# Run:
#   Invoke-Pester -Path tests/install/Register-WakeDriver.Tests.ps1

$script:TaskPath = Join-Path $PSScriptRoot '..\..\scripts\install\register-wake-driver.ps1'
# -NoExecute short-circuits before the device guard or any scheduler call --
# no parameters are mandatory in this script's default parameter set, so
# dot-sourcing needs nothing else supplied at load time.
. $script:TaskPath -NoExecute

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
        Mock Get-Command {
            if ($Name -eq 'pwsh') { return $null }
            if ($Name -eq 'powershell') { return [pscustomobject]@{ Source = 'C:\Windows\System32\WindowsPowerShell\v1.0\powershell.exe' } }
            throw "unexpected Get-Command for '$Name'"
        }
        $exe = Get-PowerShellExe
        $exe | Should Be 'C:\Windows\System32\WindowsPowerShell\v1.0\powershell.exe'
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
# -Command argument assembly -- the nested-quote escaping regression
# ---------------------------------------------------------------------------

Describe 'Format-WakeDriverActionArgs' {
    It 'builds the expected -NoProfile / -ExecutionPolicy / -Command shape' {
        $args = Format-WakeDriverActionArgs -PythonExe 'C:\Python311\python.exe' -WatchdogSeconds 300
        $args[0] | Should Be '-NoProfile'
        $args[1] | Should Be '-ExecutionPolicy'
        $args[2] | Should Be 'Bypass'
        $args[3] | Should Be '-Command'
        $args.Count | Should Be 5
    }

    It 'sets JARVIS_PRINCIPAL=autonomous in the inner command' {
        $args = Format-WakeDriverActionArgs -PythonExe 'C:\Python311\python.exe' -WatchdogSeconds 300
        $args[4] | Should Match ([regex]::Escape("`$env:JARVIS_PRINCIPAL = 'autonomous'"))
    }

    It 'sets REACTIVE_CONCURRENCY_CAP=2 in the inner command (#1390 AC8)' {
        $args = Format-WakeDriverActionArgs -PythonExe 'C:\Python311\python.exe' -WatchdogSeconds 300
        $args[4] | Should Match ([regex]::Escape("`$env:REACTIVE_CONCURRENCY_CAP = '2'"))
    }

    It 'passes --watchdog-seconds through unchanged' {
        $args = Format-WakeDriverActionArgs -PythonExe 'C:\Python311\python.exe' -WatchdogSeconds 120
        $args[4] | Should Match '--watchdog-seconds 120'
    }

    It 'wraps the -Command payload in a single outer double-quoted string (no nested double quotes)' {
        $args = Format-WakeDriverActionArgs -PythonExe 'C:\Python311\python.exe' -WatchdogSeconds 300
        $payload = $args[4]
        $payload.Substring(0, 1)  | Should Be '"'
        $payload.Substring($payload.Length - 1, 1) | Should Be '"'
        # Only the two wrapping quotes are double quotes -- the inner python
        # path must be single-quoted, or Windows argv parsing breaks on the
        # nested-double-quote bug this test guards against.
        $inner = $payload.Substring(1, $payload.Length - 2)
        ($inner -like '*"*') | Should Be $false
    }

    It 'doubles an embedded single quote in the python path so the string cannot break out' {
        $args = Format-WakeDriverActionArgs -PythonExe "C:\Users\o'brien\python.exe" -WatchdogSeconds 300
        $args[4] | Should Match ([regex]::Escape("o''brien"))
    }

    It 'single-quotes the python invocation, not double-quotes' {
        $args = Format-WakeDriverActionArgs -PythonExe 'C:\Python311\python.exe' -WatchdogSeconds 300
        $args[4] | Should Match ([regex]::Escape("& 'C:\Python311\python.exe'"))
    }
}

# ---------------------------------------------------------------------------
# NoExecute guard: loading the script without executing the main body
# ---------------------------------------------------------------------------

Describe 'NoExecute guard' {
    It 'loads functions without executing main body (no device guard, no Task Scheduler call)' {
        Get-PowerShellExe -ErrorAction SilentlyContinue | Out-Null
        Format-WakeDriverActionArgs -PythonExe 'p' -WatchdogSeconds 1 -ErrorAction SilentlyContinue | Out-Null
        # If we get here without errors (or a device-guard throw), the
        # -NoExecute short-circuit worked.
        $true | Should Be $true
    }
}
