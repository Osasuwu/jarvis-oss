# Pester tests for scripts/sandcastle/Run-Sandcastle.ps1 (issue #541).
# Exercises the daemon-state matrix and soft-stop window logic with mocked
# health probes -- no real Docker/Ollama/Supabase calls.
#
# Compatible with Pester 3.4 (built-in on Windows PowerShell 5.1) -- uses
# `Should Be` and `Assert-MockCalled` rather than the Pester 5 dash-syntax.
#
# Run:
#   Invoke-Pester -Path tests/sandcastle/Run-Sandcastle.Tests.ps1

$script:WatchdogPath = Join-Path $PSScriptRoot '..\..\scripts\sandcastle\Run-Sandcastle.ps1'
. $script:WatchdogPath -NoExecute

Describe 'Resolve-WindowEnd' {
    It 'returns null for empty input' {
        Resolve-WindowEnd -WindowEnd '' | Should BeNullOrEmpty
    }

    It 'parses HH:mm in the future of today and keeps today date' {
        # Pick a time guaranteed to be at least 1 minute in the future of the
        # current wall clock so the roll-forward logic doesn't apply.
        $future = (Get-Date).AddMinutes(15)
        $hhmm = "{0:D2}:{1:D2}" -f $future.Hour, $future.Minute
        $end = Resolve-WindowEnd -WindowEnd $hhmm
        # End may equal (Get-Date).Date OR (Get-Date).Date.AddDays(1) if the
        # 15-min offset crossed midnight; assert hour/minute round-trip only.
        $end.Hour   | Should Be $future.Hour
        $end.Minute | Should Be $future.Minute
    }

    It 'rolls HH:mm forward 24h when the boundary already passed today' {
        # AFK regression (#711 follow-up): jarvis fires at 18:00 with
        # WindowEnd=01:00 meaning 01:00 tomorrow. Without roll-forward the
        # watchdog records partial:window-expired and exits without work.
        $past = (Get-Date).AddHours(-2)
        $hhmm = "{0:D2}:{1:D2}" -f $past.Hour, $past.Minute
        $end = Resolve-WindowEnd -WindowEnd $hhmm
        $end | Should Not BeNullOrEmpty
        ($end -gt (Get-Date)) | Should Be $true
        # Must NOT be in today's past; rolled to tomorrow.
        $end.Date | Should Be (Get-Date).Date.AddDays(1)
    }

    It 'parses ISO datetime' {
        $end = Resolve-WindowEnd -WindowEnd '2026-05-09T03:00:00'
        $end.Year | Should Be 2026
        $end.Hour | Should Be 3
    }
}

Describe 'Test-WindowExpired' {
    It 'returns false when window is null' {
        Test-WindowExpired -WindowEnd $null | Should Be $false
    }

    It 'returns true when window is in the past' {
        Test-WindowExpired -WindowEnd ((Get-Date).AddMinutes(-5)) | Should Be $true
    }

    It 'returns false when window is in the future' {
        Test-WindowExpired -WindowEnd ((Get-Date).AddMinutes(5)) | Should Be $false
    }
}

Describe 'Wait-DockerReady' {
    It 'returns true when Docker becomes ready before timeout' {
        Mock Test-DockerRunning { $true }
        Wait-DockerReady -TimeoutSec 2 | Should Be $true
    }

    It 'returns false when Docker never comes up within timeout' {
        Mock Test-DockerRunning { $false }
        Wait-DockerReady -TimeoutSec 1 | Should Be $false
    }
}

Describe 'Wait-OllamaReady' {
    It 'returns true when Ollama is up' {
        Mock Test-OllamaRunning { $true }
        Wait-OllamaReady -TimeoutSec 1 | Should Be $true
    }

    It 'returns false when Ollama times out' {
        Mock Test-OllamaRunning { $false }
        Wait-OllamaReady -TimeoutSec 1 | Should Be $false
    }
}

Describe 'Read-DotEnvFile' {
    $tmp = Join-Path $env:TEMP "sandcastle-watchdog-env-$([guid]::NewGuid()).env"
    @(
        '# comment',
        'SUPABASE_URL=https://example.supabase.co',
        'SUPABASE_KEY="anon-key-here"',
        "QUOTED='single'",
        # Unbalanced quotes must survive verbatim (regression: naive Trim
        # corrupted base64 padding `key=` and apostrophes in values).
        'BASE64_KEY=abcd1234==',
        "APOS_VAL=it's",
        '',
        'INVALID_LINE_NO_EQUALS'
    ) | Set-Content -Path $tmp -Encoding UTF8

    It 'parses key=value, ignores comments and blanks' {
        $vars = Read-DotEnvFile -Path $tmp
        $vars['SUPABASE_URL']                          | Should Be 'https://example.supabase.co'
        $vars['SUPABASE_KEY']                          | Should Be 'anon-key-here'
        $vars['QUOTED']                                | Should Be 'single'
        $vars.ContainsKey('INVALID_LINE_NO_EQUALS')    | Should Be $false
    }

    It 'preserves base64 padding and apostrophes' {
        $vars = Read-DotEnvFile -Path $tmp
        $vars['BASE64_KEY'] | Should Be 'abcd1234=='
        $vars['APOS_VAL']   | Should Be "it's"
    }

    It 'returns empty hashtable for missing file' {
        $vars = Read-DotEnvFile -Path "$env:TEMP\does-not-exist-$([guid]::NewGuid()).env"
        $vars.Count | Should Be 0
    }

    Remove-Item -LiteralPath $tmp -ErrorAction SilentlyContinue
}

Describe 'Test-IsInfraDown' {
    It 'returns true for whitelisted infra reasons' {
        Test-IsInfraDown -Reason 'docker-down'    | Should Be $true
        Test-IsInfraDown -Reason 'ollama-down'    | Should Be $true
        Test-IsInfraDown -Reason 'npm-not-found'  | Should Be $true
        Test-IsInfraDown -Reason 'no-result-file' | Should Be $true
    }

    It 'matches reasons with trailing detail (StartsWith)' {
        Test-IsInfraDown -Reason 'docker-down: daemon timed out' | Should Be $true
    }

    It 'returns false for routine / agent-side reasons' {
        Test-IsInfraDown -Reason ''                  | Should Be $false
        Test-IsInfraDown -Reason 'exit=7'            | Should Be $false
        Test-IsInfraDown -Reason 'window-expired'    | Should Be $false
        Test-IsInfraDown -Reason 'json-parse-error'  | Should Be $false
    }

    It 'returns true for provider-billing reasons (#955)' {
        Test-IsInfraDown -Reason 'provider-billing'                            | Should Be $true
        Test-IsInfraDown -Reason 'provider-billing: exit=1 (deepseek 402)'     | Should Be $true
    }
}

Describe 'Test-IsProviderBilling' {
    # Tier-2 provider ran out of money (#955): DeepSeek answers HTTP 402
    # {"error":{"message":"Insufficient Balance"}} on /anthropic/v1/messages.
    # The watchdog must classify this as infra-down (the AFK loop is dead
    # until a human tops up the account), not as a routine agent-side exit=1
    # -- that misclassification kept the 2026-06-03..11 outage silent.
    It 'matches the DeepSeek Insufficient Balance message case-insensitively' {
        Test-IsProviderBilling -Text 'API Error: 402 {"error":{"message":"Insufficient Balance"}}' | Should Be $true
        Test-IsProviderBilling -Text 'insufficient balance' | Should Be $true
    }

    It 'matches every billing string signature' {
        # Each entry in $script:ProviderBillingSignatures must be exercised so a
        # future edit to the list cannot silently drop a case (#956 review noted
        # 'insufficient funds' had no direct assertion).
        Test-IsProviderBilling -Text 'payment required'     | Should Be $true
        Test-IsProviderBilling -Text 'insufficient funds'   | Should Be $true
    }

    It 'matches generic payment-required wording' {
        Test-IsProviderBilling -Text 'HTTP 402 Payment Required' | Should Be $true
    }

    It 'matches a 402 status code when a structured context separator precedes it' {
        Test-IsProviderBilling -Text 'request failed with status 402'  | Should Be $true
        Test-IsProviderBilling -Text '{"error":{"code":402}}'          | Should Be $true
        Test-IsProviderBilling -Text 'error=402'                       | Should Be $true
        Test-IsProviderBilling -Text 'status: 402'                     | Should Be $true
    }

    It 'matches a bare HTTP 402 via branch (a), independent of the string signatures (#956 review)' {
        # 'HTTP 402' with no reason phrase contains none of the billing string
        # signatures, so this independently exercises regex branch (a) -- the
        # only handler for a status code without "Payment Required" text. The
        # 'HTTP 402 Payment Required' assertion above is short-circuited by the
        # 'payment required' signature before branch (a) is ever reached, so it
        # would still pass even if branch (a) regressed; this one would not.
        Test-IsProviderBilling -Text 'HTTP 402' | Should Be $true
    }

    It 'matches the HTTP status line even with a non-standard 402 reason phrase' {
        # The string signatures catch "Payment Required"; this branch covers a
        # custom 402 body like `HTTP/1.1 402 Account Exhausted` (#956 review
        # false-negative: version digits sit between `http` and `402`).
        Test-IsProviderBilling -Text 'HTTP/1.1 402 Payment Required'   | Should Be $true
        Test-IsProviderBilling -Text 'HTTP/1.1 402 Account Exhausted'  | Should Be $true
        Test-IsProviderBilling -Text 'HTTP/2 402 Quota Drained'        | Should Be $true
    }

    It 'does not fire on a bare 402 in branch names or token counts' {
        Test-IsProviderBilling -Text 'checked out feat/402-some-issue' | Should Be $false
        Test-IsProviderBilling -Text 'inputTokens=402'                 | Should Be $false
    }

    It 'does not fire on prose where letters separate a context word from 402' {
        # #956 review (HIGH): the prior `\D{0,20}` window matched these, firing
        # spurious Telegram billing alerts on ordinary agent run.log content.
        Test-IsProviderBilling -Text 'Error occurred at line 402 of config.js' | Should Be $false
        Test-IsProviderBilling -Text 'error handling in issue #402'            | Should Be $false
        Test-IsProviderBilling -Text 'see the code path around line 402'       | Should Be $false
    }

    It 'does not fire across newlines (error on one line, 402 several lines later)' {
        # \s in .NET regex matches \n; branch (a) must use [ \t] to avoid
        # cross-line false positives from multi-line log tails.
        Test-IsProviderBilling -Text "error`n`n`n`n`n`n402" | Should Be $false
    }

    It 'does not fire on code #402 or error #402 (GitHub issue / line references)' {
        Test-IsProviderBilling -Text 'code #402'  | Should Be $false
        Test-IsProviderBilling -Text 'error #402' | Should Be $false
    }

    It 'matches billing signature on the second line (the log-tail position)' {
        Test-IsProviderBilling -Text "exit=1`nAPI Error: 402 Insufficient Balance" | Should Be $true
        Test-IsProviderBilling -Text "exit=1`ninsufficient balance" | Should Be $true
    }

    It 'returns false for empty and generic failures' {
        Test-IsProviderBilling -Text ''        | Should Be $false
        Test-IsProviderBilling -Text 'exit=1'  | Should Be $false
    }
}

Describe 'Test-DeepSeekBalance' {
    # Pre-flight probe against GET /user/balance (#955). Only an affirmative
    # {"is_available": false} blocks the run; every error path fails OPEN so
    # a flaky balance endpoint can never stop a healthy night -- the
    # provider-billing signature class is the in-run backstop.
    It 'returns false when the API reports is_available=false (balance exhausted)' {
        Mock Invoke-RestMethod { [pscustomobject]@{ is_available = $false; balance_infos = @() } }
        Test-DeepSeekBalance -ApiKey 'k' | Should Be $false
    }

    It 'returns true when the API reports is_available=true' {
        Mock Invoke-RestMethod { [pscustomobject]@{ is_available = $true } }
        Test-DeepSeekBalance -ApiKey 'k' | Should Be $true
    }

    It 'fails OPEN on network/HTTP errors' {
        Mock Invoke-RestMethod { throw 'connect timeout' }
        Test-DeepSeekBalance -ApiKey 'k' | Should Be $true
    }

    It 'fails OPEN when the response has no is_available field' {
        Mock Invoke-RestMethod { [pscustomobject]@{ unexpected = 1 } }
        Test-DeepSeekBalance -ApiKey 'k' | Should Be $true
    }

    It 'fails OPEN when Invoke-RestMethod returns $null (empty 200 body)' {
        # Left side of the `$null -eq $resp -or $null -eq $resp.is_available`
        # guard: a 200 OK with an empty body. The "no is_available field" test
        # above only exercises the right side (#956 review).
        Mock Invoke-RestMethod { $null }
        Test-DeepSeekBalance -ApiKey 'k' | Should Be $true
    }

    It 'returns false when API returns the string "false" instead of boolean false' {
        Mock Invoke-RestMethod { [pscustomobject]@{ is_available = 'false' } }
        Test-DeepSeekBalance -ApiKey 'k' | Should Be $false
    }
    It 'skips the HTTP call entirely when the key is empty' {
        Mock Invoke-RestMethod { throw 'must not be called' }
        Test-DeepSeekBalance -ApiKey '' | Should Be $true
        Assert-MockCalled Invoke-RestMethod -Times 0 -Exactly -Scope It
    }

    It 'sends the key as a bearer header to the balance endpoint' {
        $script:probeArgs = $null
        Mock Invoke-RestMethod {
            $script:probeArgs = @{ Uri = $Uri; Headers = $Headers }
            [pscustomobject]@{ is_available = $true }
        }
        Test-DeepSeekBalance -ApiKey 'k123' | Out-Null
        $script:probeArgs.Uri                   | Should Be 'https://api.deepseek.com/user/balance'
        $script:probeArgs.Headers.Authorization | Should Be 'Bearer k123'
    }
}

Describe 'Invoke-NpmSandcastle npm flags (#955)' {
    It 'does not pass --silent to npm run (it suppressed provider errors during the 402 outage)' {
        # Regression pin: with --silent the agent's "Insufficient Balance"
        # output never reached run.log, so Get-LogTail returned '' and
        # diag_tail stayed blank for the whole 5-day outage.
        $src = Get-Content -LiteralPath $script:WatchdogPath -Raw
        $src | Should Not Match 'npm run --silent'
    }
}

Describe 'Send-TelegramAlert' {
    It 'returns null without HTTP call when token/chat-id missing' {
        Mock Invoke-RestMethod { 'should-not-be-called' }
        Send-TelegramAlert -BotToken '' -ChatId '' -Message 'hello' | Should BeNullOrEmpty
        Assert-MockCalled Invoke-RestMethod -Times 0 -Exactly -Scope It
    }

    It 'truncates messages over 200 chars' {
        $script:captured = $null   # script-scoped; clear before mock so prior tests don't bleed in.
        Mock Invoke-RestMethod -ParameterFilter { $true } -MockWith {
            $script:captured = $Body
            'ok'
        }
        $long = ('x' * 250)
        Send-TelegramAlert -BotToken 't' -ChatId '1' -Message $long | Out-Null
        # Body is JSON-encoded; parse to inspect the text field.
        $parsed = $script:captured | ConvertFrom-Json
        $parsed.text.Length | Should Be 200
        $parsed.text        | Should Match '\.\.\.$'
    }

}

Describe 'Format-RedactedError' {
    It 'replaces every occurrence of the secret with TOKEN-REDACTED' {
        $err = "Invoke-RestMethod : (404) https://api.telegram.org/botSECRET-TOKEN/sendMessage failed; SECRET-TOKEN exposed twice"
        $out = Format-RedactedError -Message $err -Secret 'SECRET-TOKEN'
        $out | Should Match '<TOKEN-REDACTED>'
        $out | Should Not Match 'SECRET-TOKEN'
    }

    It 'returns input unchanged when secret is empty (no global wipe)' {
        $err = 'boom'
        Format-RedactedError -Message $err -Secret '' | Should Be 'boom'
    }

    It 'escapes regex metacharacters in the secret' {
        $err = 'leaked: a.b+c'
        $out = Format-RedactedError -Message $err -Secret 'a.b+c'
        $out | Should Match '<TOKEN-REDACTED>'
        $out | Should Not Match 'a\.b\+c'
    }
}

Describe 'Get-SandcastleErrorClass' {
    It 'detects an idle-timeout crash' {
        Get-SandcastleErrorClass -Text 'foo AgentIdleTimeoutError: idle 600s' |
            Should Be 'AgentIdleTimeoutError'
    }
    It 'detects a worktree-teardown crash' {
        Get-SandcastleErrorClass -Text 'WorktreeError: git worktree remove failed' |
            Should Be 'WorktreeError'
    }
    It 'does not confuse AgentIdleTimeoutError for the generic AgentError' {
        Get-SandcastleErrorClass -Text 'AgentIdleTimeoutError thrown' |
            Should Be 'AgentIdleTimeoutError'
    }
    It 'returns empty when no known class is present' {
        Get-SandcastleErrorClass -Text 'random failure exit=1' | Should Be ''
    }
    It 'returns empty on empty input' {
        Get-SandcastleErrorClass -Text '' | Should Be ''
    }
}

Describe 'Protect-LogTail' {
    It 'redacts a known literal secret' {
        $out = Protect-LogTail -Text 'before SUPER-SECRET-VALUE after' -Secrets @('SUPER-SECRET-VALUE')
        $out | Should Not Match 'SUPER-SECRET-VALUE'
        $out | Should Match 'SECRET-REDACTED'
    }
    It 'redacts a GitHub token shape by pattern (no literal needed)' {
        # Built at runtime so the token shape never appears as a source literal
        # (keeps gitleaks quiet on the test file itself).
        $tok = 'ghp_' + ('a' * 36)
        $out = Protect-LogTail -Text "stderr: auth $tok rejected"
        $out | Should Not Match 'ghp_a'
        $out | Should Match 'GH-TOKEN-REDACTED'
    }
    It 'redacts an Anthropic key shape by pattern' {
        $tok = 'sk-ant-' + ('x' * 24)
        $out = Protect-LogTail -Text "key=$tok"
        $out | Should Not Match 'sk-ant-x'
        $out | Should Match 'ANTHROPIC-KEY-REDACTED'
    }
    It 'redacts a generic sk- prefixed key shape (DeepSeek, OpenRouter, etc.)' {
        $tok = 'sk-' + ('d' * 40)
        $out = Protect-LogTail -Text "Authorization: Bearer $tok failed"
        $out | Should Not Match 'sk-ddd'
        $out | Should Match 'API-KEY-REDACTED'
    }
    It 'redacts the subscription OAuth token passed explicitly in -Secrets' {
        # The failure handler passes $oauthToken in -Secrets (Run-Sandcastle.ps1
        # ~L1173). Even though the OAuth token also matches the sk-ant pattern,
        # the literal-secret path must redact it so a credit-exhaustion log tail
        # never persists the Agent-SDK credential to Supabase. Built at runtime
        # so the token shape never appears as a source literal (gitleaks).
        $oauth = 'sk-ant-oat01-' + ('z' * 40)
        $out = Protect-LogTail -Text "CLAUDE_CODE_OAUTH_TOKEN=$oauth exhausted" -Secrets @($oauth)
        $out | Should Not Match 'sk-ant-oat01-z'
        $out | Should Match 'REDACTED'
    }
    It 'returns empty string on empty input' {
        Protect-LogTail -Text '' | Should Be ''
    }
}

Describe 'Get-LogTail' {
    It 'returns empty string when the log file does not exist' {
        Get-LogTail -LogFile (Join-Path $env:TEMP "no-such-$([guid]::NewGuid()).log") |
            Should Be ''
    }
    It 'returns empty string when LogFile is blank' {
        Get-LogTail -LogFile '' | Should Be ''
    }
    It 'returns the last N non-empty lines joined by newline' {
        $f = Join-Path $env:TEMP "logtail-$([guid]::NewGuid()).log"
        @('l1', '', 'l2', 'l3', '', 'l4') | Set-Content -LiteralPath $f -Encoding utf8
        try {
            $out = Get-LogTail -LogFile $f -Lines 2
            $out | Should Be "l3`nl4"
        } finally {
            Remove-Item -LiteralPath $f -ErrorAction SilentlyContinue
        }
    }
}

Describe 'Test-IsOOM' {
    It 'flags exit code 137 (Linux OOM-kill) without a log file' {
        Test-IsOOM -Reason 'exit=137' -LogFile '' | Should Be $true
    }

    It 'rejects unrelated exit codes' {
        Test-IsOOM -Reason 'exit=7' -LogFile '' | Should Be $false
    }

    It 'rejects json-parse-error even if the log mentions OOM' {
        # Malformed result.json comes from a torn write, not from a model
        # OOM -- escalating to a smaller model would not help.
        $tmp = Join-Path $env:TEMP "oom-log-$([guid]::NewGuid()).txt"
        'CUDA out of memory' | Set-Content -Path $tmp -Encoding UTF8
        try {
            Test-IsOOM -Reason 'json-parse-error: Unexpected token' -LogFile $tmp | Should Be $false
        } finally {
            Remove-Item -LiteralPath $tmp -ErrorAction SilentlyContinue
        }
    }

    It 'matches OOM substrings in the log file (case-insensitive)' {
        $tmp = Join-Path $env:TEMP "oom-log-$([guid]::NewGuid()).txt"
        'ollama serve: model requires more system memory than available' | Set-Content -Path $tmp -Encoding UTF8
        try {
            Test-IsOOM -Reason 'exit=1' -LogFile $tmp | Should Be $true
        } finally {
            Remove-Item -LiteralPath $tmp -ErrorAction SilentlyContinue
        }
    }

    It 'returns false when log file does not exist' {
        Test-IsOOM -Reason 'exit=1' -LogFile (Join-Path $env:TEMP "missing-$([guid]::NewGuid()).log") | Should Be $false
    }
}

Describe 'Get-IssueFromBranch' {
    It 'parses feat/<N>-slug' {
        Get-IssueFromBranch -Branch 'feat/543-multi-tier' | Should Be 543
    }
    It 'parses fix/<N>-slug' {
        Get-IssueFromBranch -Branch 'fix/42-broken' | Should Be 42
    }
    It 'returns null for unknown shapes' {
        Get-IssueFromBranch -Branch 'main' | Should BeNullOrEmpty
        Get-IssueFromBranch -Branch ''     | Should BeNullOrEmpty
        Get-IssueFromBranch -Branch $null  | Should BeNullOrEmpty
    }
}

Describe 'Resolve-Tier2Config' {
    It 'returns null when provider is empty' {
        Mock Get-IssueLabels { @() }
        Resolve-Tier2Config -Provider '' -Issue 1 -RepoSlug 'x/y' -EnvVars @{} | Should BeNullOrEmpty
    }

    It 'maps deepseek with envVar overrides + key' {
        Mock Get-IssueLabels { @() }
        $env = @{ DEEPSEEK_API_KEY = 'ds-secret'; DEEPSEEK_MODEL = 'ds-coder-mini' }
        $cfg = Resolve-Tier2Config -Provider 'deepseek' -Issue 99 -RepoSlug 'x/y' -EnvVars $env
        $cfg.Provider  | Should Be 'deepseek'
        $cfg.Model     | Should Be 'ds-coder-mini'
        $cfg.AuthToken | Should Be 'ds-secret'
        $cfg.BaseUrl   | Should Match '^https://'
    }

    It 'use-claude-api label flips deepseek default to claude' {
        Mock Get-IssueLabels { @('use-claude-api', 'priority:high') }
        $env = @{ ANTHROPIC_API_KEY = 'sk-ant-test'; DEEPSEEK_API_KEY = 'ds' }
        $cfg = Resolve-Tier2Config -Provider 'deepseek' -Issue 99 -RepoSlug 'x/y' -EnvVars $env
        $cfg.Provider  | Should Be 'claude'
        $cfg.AuthToken | Should Be 'sk-ant-test'
    }

    It 'leaves provider as deepseek when label missing' {
        Mock Get-IssueLabels { @('priority:medium') }
        $env = @{ DEEPSEEK_API_KEY = 'ds' }
        $cfg = Resolve-Tier2Config -Provider 'deepseek' -Issue 99 -RepoSlug 'x/y' -EnvVars $env
        $cfg.Provider | Should Be 'deepseek'
    }
}

Describe 'Invoke-Watchdog tier escalation matrix (slice 5, #543)' {
    BeforeEach {
        Mock Test-DockerRunning { $true }
        Mock Test-OllamaRunning { $true }
        Mock Start-DockerDesktop { }
        Mock Start-OllamaServer  { }
        Mock Get-RepoRoot { $env:TEMP }
        Mock Read-DotEnvFile {
            @{
                SUPABASE_URL       = 'https://x'
                SUPABASE_KEY       = 'k'
                TELEGRAM_BOT_TOKEN = 't'
                TELEGRAM_CHAT_ID   = '1'
                DEEPSEEK_API_KEY   = 'ds-key'
                ANTHROPIC_API_KEY  = 'cl-key'
            }
        }
        Mock New-RuntimeDir { Join-Path $env:TEMP "sandcastle-tier-test-$([guid]::NewGuid())" }
        Mock Invoke-RuntimeSweep { @() }   # #572: keep sweep no-op in matrix tests
        Mock Write-OutcomeRecord { 'mocked' }
        Mock Send-TelegramAlert  { 'mocked' }
        Mock Add-IssueLabel      { $true }
        Mock Get-IssueLabels     { @() }
        # #956: the OOM-escalation path now pre-flights the deepseek balance.
        # Default it healthy so escalation tests stay off the network; the
        # exhausted-balance case overrides this mock in its own It.
        Mock Test-DeepSeekBalance { $true }
    }

    It 'AC: Tier 0 success -> no escalation, no labels, no Tier 1/2 invocation' {
        $script:calls = @()
        Mock Invoke-Sandcastle {
            $script:calls += $Model
            [pscustomobject]@{
                ok = $true; exitCode = 0; reason = $null
                result = [pscustomobject]@{
                    branch = 'feat/100-foo'; commits = @(); iterations = @()
                }
            }
        }
        Mock Test-IsOOM { $false }

        Invoke-Watchdog -Repo 'jarvis' -MaxIterations 1 -Model 'qwen-large' `
            -Tier1Model 'qwen-small' -Tier2Provider 'deepseek' `
            -WindowEnd '' -DockerTimeoutSec 5 -OllamaTimeoutSec 5

        Assert-MockCalled Invoke-Sandcastle -Times 1 -Exactly -Scope It
        Assert-MockCalled Add-IssueLabel    -Times 0 -Exactly -Scope It
        $script:calls[0] | Should Be 'qwen-large'
    }

    It 'AC: Tier 0 OOM -> exactly one Tier 1 retry, success records tier1' {
        $script:calls = @()
        $script:n = 0
        Mock Invoke-Sandcastle {
            $script:n++
            $script:calls += $Model
            if ($script:n -eq 1) {
                return [pscustomobject]@{ ok = $false; exitCode = 137; reason = 'exit=137'; result = $null }
            }
            return [pscustomobject]@{
                ok = $true; exitCode = 0; reason = $null
                result = [pscustomobject]@{
                    branch = 'feat/200-bar'; commits = @(); iterations = @()
                }
            }
        }
        Mock Test-IsOOM { $true }

        Invoke-Watchdog -Repo 'jarvis' -MaxIterations 1 -Model 'qwen-large' `
            -Tier1Model 'qwen-small' -Tier2Provider 'deepseek' `
            -WindowEnd '' -DockerTimeoutSec 5 -OllamaTimeoutSec 5

        Assert-MockCalled Invoke-Sandcastle -Times 2 -Exactly -Scope It
        $script:calls[0] | Should Be 'qwen-large'
        $script:calls[1] | Should Be 'qwen-small'
        Assert-MockCalled Add-IssueLabel -Times 0 -Exactly -Scope It
        Assert-MockCalled Write-OutcomeRecord -Times 1 -Exactly -Scope It `
            -ParameterFilter { $Status -eq 'success' -and $LlmMetrics.tier -eq 'tier1' }
    }

    It 'AC: Tier 0+1 fail -> Tier 2 (deepseek) success, no too-large label' {
        $script:n = 0
        $script:calls = @()
        Mock Invoke-Sandcastle {
            $script:n++
            $script:calls += @{ Model = $Model; BaseUrl = $BaseUrl; Token = $AuthToken }
            if ($script:n -le 2) {
                $branch = if ($script:n -eq 1) { 'feat/300-baz' } else { $null }
                return [pscustomobject]@{
                    ok = $false; exitCode = 137; reason = 'exit=137'
                    result = if ($branch) { [pscustomobject]@{ branch = $branch } } else { $null }
                }
            }
            return [pscustomobject]@{
                ok = $true; exitCode = 0; reason = $null
                result = [pscustomobject]@{
                    branch = 'feat/300-baz'; commits = @(); iterations = @()
                }
            }
        }
        Mock Test-IsOOM { $true }

        Invoke-Watchdog -Repo 'jarvis' -MaxIterations 1 -Model 'qwen-large' `
            -Tier1Model 'qwen-small' -Tier2Provider 'deepseek' `
            -WindowEnd '' -DockerTimeoutSec 5 -OllamaTimeoutSec 5

        Assert-MockCalled Invoke-Sandcastle -Times 3 -Exactly -Scope It
        $script:calls[2].Token | Should Be 'ds-key'
        Assert-MockCalled Add-IssueLabel -Times 0 -Exactly -Scope It
        Assert-MockCalled Write-OutcomeRecord -Times 1 -Exactly -Scope It `
            -ParameterFilter { $Status -eq 'success' -and $LlmMetrics.tier -eq 'tier2:deepseek' }
    }

    It 'AC: full chain fail -> too-large-for-local label applied + outcome=failure' {
        Mock Invoke-Sandcastle {
            [pscustomobject]@{
                ok = $false; exitCode = 137; reason = 'exit=137'
                result = [pscustomobject]@{ branch = 'feat/400-qux' }
            }
        }
        Mock Test-IsOOM { $true }

        { Invoke-Watchdog -Repo 'jarvis' -MaxIterations 1 -Model 'qwen-large' `
              -Tier1Model 'qwen-small' -Tier2Provider 'deepseek' `
              -WindowEnd '' -DockerTimeoutSec 5 -OllamaTimeoutSec 5 } | Should Throw

        Assert-MockCalled Invoke-Sandcastle -Times 3 -Exactly -Scope It
        Assert-MockCalled Add-IssueLabel -Times 1 -Exactly -Scope It `
            -ParameterFilter { $Issue -eq 400 -and $Label -eq 'too-large-for-local' }
        Assert-MockCalled Write-OutcomeRecord -Times 1 -Exactly -Scope It `
            -ParameterFilter { $Status -eq 'failure' -and $Summary -like '*tier=tier2:deepseek*' }
    }

    It 'AC: use-claude-api label flips Tier 2 to Claude API key' {
        Mock Get-IssueLabels { @('use-claude-api') }
        $script:n = 0
        $script:tier2Token = $null
        Mock Invoke-Sandcastle {
            $script:n++
            if ($script:n -le 2) {
                return [pscustomobject]@{
                    ok = $false; exitCode = 137; reason = 'exit=137'
                    result = [pscustomobject]@{ branch = 'feat/500-claude' }
                }
            }
            $script:tier2Token = $AuthToken
            return [pscustomobject]@{
                ok = $true; exitCode = 0; reason = $null
                result = [pscustomobject]@{ branch = 'feat/500-claude'; commits = @(); iterations = @() }
            }
        }
        Mock Test-IsOOM { $true }

        Invoke-Watchdog -Repo 'jarvis' -MaxIterations 1 -Model 'qwen-large' `
            -Tier1Model 'qwen-small' -Tier2Provider 'deepseek' `
            -WindowEnd '' -DockerTimeoutSec 5 -OllamaTimeoutSec 5

        $script:tier2Token | Should Be 'cl-key'
        Assert-MockCalled Write-OutcomeRecord -Times 1 -Exactly -Scope It `
            -ParameterFilter { $LlmMetrics.tier -eq 'tier2:claude' }
    }

    It 'AC: non-OOM Tier 0 failure does NOT escalate (logic error stays a failure)' {
        Mock Invoke-Sandcastle {
            [pscustomobject]@{ ok = $false; exitCode = 7; reason = 'exit=7'; result = $null }
        }
        Mock Test-IsOOM { $false }

        { Invoke-Watchdog -Repo 'jarvis' -MaxIterations 1 -Model 'qwen-large' `
              -Tier1Model 'qwen-small' -Tier2Provider 'deepseek' `
              -WindowEnd '' -DockerTimeoutSec 5 -OllamaTimeoutSec 5 } | Should Throw

        Assert-MockCalled Invoke-Sandcastle -Times 1 -Exactly -Scope It
        Assert-MockCalled Add-IssueLabel    -Times 0 -Exactly -Scope It
        Assert-MockCalled Write-OutcomeRecord -Times 1 -Exactly -Scope It `
            -ParameterFilter { $Status -eq 'failure' -and $LlmMetrics.tier -eq 'tier0' }
    }

    It 'no Tier 1 configured: Tier 0 OOM jumps directly to Tier 2 (deepseek)' {
        $script:n = 0
        Mock Invoke-Sandcastle {
            $script:n++
            if ($script:n -eq 1) {
                return [pscustomobject]@{
                    ok = $false; exitCode = 137; reason = 'exit=137'
                    result = [pscustomobject]@{ branch = 'feat/600-skip-tier1' }
                }
            }
            return [pscustomobject]@{
                ok = $true; exitCode = 0; reason = $null
                result = [pscustomobject]@{ branch = 'feat/600-skip-tier1'; commits = @(); iterations = @() }
            }
        }
        Mock Test-IsOOM { $true }

        Invoke-Watchdog -Repo 'jarvis' -MaxIterations 1 -Model 'qwen-large' `
            -Tier1Model '' -Tier2Provider 'deepseek' `
            -WindowEnd '' -DockerTimeoutSec 5 -OllamaTimeoutSec 5

        Assert-MockCalled Invoke-Sandcastle -Times 2 -Exactly -Scope It
        Assert-MockCalled Write-OutcomeRecord -Times 1 -Exactly -Scope It `
            -ParameterFilter { $LlmMetrics.tier -eq 'tier2:deepseek' }
    }

    It 'AC #956: drained deepseek balance on OOM escalation -> abort before Tier 2 container boot + alert' {
        # Sibling-gap fix: the primary path (Tier2AsPrimary) pre-flighted the
        # balance, but the OOM-escalation lane booted a Tier 2 container against
        # a dead provider first. With the balance exhausted, escalation must fail
        # fast after Tier 0/1 -- the third Invoke-Sandcastle (the Tier 2 boot)
        # never happens, the outcome is the actionable provider-billing reason,
        # and the Telegram alert fires.
        Mock Test-DeepSeekBalance { $false }   # overrides the healthy BeforeEach default
        Mock Invoke-Sandcastle {
            [pscustomobject]@{
                ok = $false; exitCode = 137; reason = 'exit=137'
                result = [pscustomobject]@{ branch = 'feat/801-oom-billing' }
            }
        }
        Mock Test-IsOOM { $true }

        { Invoke-Watchdog -Repo 'jarvis' -MaxIterations 1 -Model 'qwen-large' `
              -Tier1Model 'qwen-small' -Tier2Provider 'deepseek' `
              -WindowEnd '' -DockerTimeoutSec 5 -OllamaTimeoutSec 5 } | Should Throw

        # Tier 0 + Tier 1 booted (2 calls); the Tier 2 container boot is skipped.
        Assert-MockCalled Invoke-Sandcastle   -Times 2 -Exactly -Scope It
        Assert-MockCalled Test-DeepSeekBalance -Times 1 -Exactly -Scope It
        Assert-MockCalled Write-OutcomeRecord -Times 1 -Exactly -Scope It `
            -ParameterFilter {
                $Status -eq 'failure' -and
                $Summary -like 'provider-billing*OOM escalation*' -and
                $LlmMetrics.provider_billing -eq $true -and
                $LlmMetrics.tier -ne $null
            }
        Assert-MockCalled Send-TelegramAlert -Times 1 -Exactly -Scope It `
            -ParameterFilter { $Message -like '*provider-billing*' }
        Assert-MockCalled Add-IssueLabel -Times 0 -Exactly -Scope It
    }

    It 'AC #956: OOM escalation Tier 2 boots but returns 402 in-run -> in-run classifier fires billing alert' {
        # Pre-flight passes (balance healthy at probe time), but the in-run
        # agent hits a 402 mid-flight. The in-run classifier must promote to
        # provider-billing and fire the Telegram alert.
        Mock Test-DeepSeekBalance { $true }   # pre-flight passes
        $script:n = 0
        Mock Invoke-Sandcastle {
            $script:n++
            if ($script:n -le 2) {
                # Tier 0 + Tier 1 OOM
                return [pscustomobject]@{
                    ok = $false; exitCode = 137; reason = 'exit=137'
                    result = [pscustomobject]@{ branch = 'feat/956-oom-inrun' }
                }
            }
            # Tier 2 boots but returns a generic failure with billing in log
            return [pscustomobject]@{
                ok = $false; exitCode = 1; result = $null; reason = 'exit=1'
            }
        }
        Mock Test-IsOOM { $true }
        Mock Get-LogTail { 'API Error: 402 {"error":{"message":"Insufficient Balance"}}' }

        { Invoke-Watchdog -Repo 'jarvis' -MaxIterations 1 -Model 'qwen-large' `
              -Tier1Model 'qwen-small' -Tier2Provider 'deepseek' `
              -WindowEnd '' -DockerTimeoutSec 5 -OllamaTimeoutSec 5 } | Should Throw

        Assert-MockCalled Invoke-Sandcastle -Times 3 -Exactly -Scope It
        Assert-MockCalled Write-OutcomeRecord -Times 1 -Exactly -Scope It `
            -ParameterFilter {
                $Status -eq 'failure' -and
                $Summary -like '*provider-billing*' -and
                $LlmMetrics.provider_billing -eq $true
            }
        Assert-MockCalled Send-TelegramAlert -Times 1 -Exactly -Scope It `
            -ParameterFilter { $Message -like '*provider-billing*' }
    }
}

Describe 'Invoke-Watchdog Tier 2-as-primary (AFK quota split, 2026-05-14)' {
    # When -Tier2AsPrimary is set with a Tier 2 provider, the watchdog must
    # skip Tier 0/1 Ollama entirely and dispatch the first iteration straight
    # to the remote endpoint. Rationale: local Ollama models fail real-Claude-
    # Code tool_use fidelity probes (memory ollama_bench_must_measure_tool_use_fidelity)
    # and the upcoming Anthropic interactive/automatic quota split makes
    # subscription-first AFK strategically expensive. DeepSeek is the cheapest
    # tier that emits structured tool_use blocks reliably.
    BeforeEach {
        Mock Test-DockerRunning { $true }
        Mock Test-OllamaRunning { $true }       # default; overridden where relevant
        Mock Start-DockerDesktop { }
        Mock Start-OllamaServer  { }
        Mock Get-RepoRoot { $env:TEMP }
        Mock Read-DotEnvFile {
            @{
                SUPABASE_URL       = 'https://x'
                SUPABASE_KEY       = 'k'
                TELEGRAM_BOT_TOKEN = 't'
                TELEGRAM_CHAT_ID   = '1'
                DEEPSEEK_API_KEY   = 'ds-key'
                ANTHROPIC_API_KEY  = 'cl-key'
            }
        }
        Mock New-RuntimeDir { Join-Path $env:TEMP "sandcastle-tier2primary-$([guid]::NewGuid())" }
        Mock Invoke-RuntimeSweep { @() }
        Mock Write-OutcomeRecord { 'mocked' }
        Mock Send-TelegramAlert  { 'mocked' }
        Mock Add-IssueLabel      { $true }
        Mock Get-IssueLabels     { @() }
        # #955: keep the pre-flight balance probe off the network in every
        # Tier2AsPrimary test; individual tests re-mock to simulate exhaustion.
        Mock Test-DeepSeekBalance { $true }
    }

    It 'AC: Tier 2 as primary -> first call hits deepseek directly, no Tier 0/1 invocation' {
        $script:calls = @()
        Mock Invoke-Sandcastle {
            $script:calls += @{ Model = $Model; BaseUrl = $BaseUrl; Token = $AuthToken }
            [pscustomobject]@{
                ok = $true; exitCode = 0; reason = $null
                result = [pscustomobject]@{
                    branch = 'feat/700-deepseek-primary'; commits = @(); iterations = @()
                }
            }
        }
        Mock Test-IsOOM { $false }

        Invoke-Watchdog -Repo 'jarvis' -MaxIterations 1 -Model 'qwen-large' `
            -Tier1Model 'qwen-small' -Tier2Provider 'deepseek' -Tier2AsPrimary `
            -WindowEnd '' -DockerTimeoutSec 5 -OllamaTimeoutSec 5

        Assert-MockCalled Invoke-Sandcastle -Times 1 -Exactly -Scope It
        $script:calls[0].Token | Should Be 'ds-key'
        $script:calls[0].BaseUrl | Should Be 'https://api.deepseek.com/anthropic'
        Assert-MockCalled Write-OutcomeRecord -Times 1 -Exactly -Scope It `
            -ParameterFilter { $Status -eq 'success' -and $LlmMetrics.tier -eq 'tier2:deepseek' }
    }

    It 'AC: Tier 2 as primary skips Ollama daemon check (does not autostart, does not throw on ollama-down)' {
        Mock Test-OllamaRunning { $false }   # Ollama is down -- must NOT matter
        Mock Invoke-Sandcastle {
            [pscustomobject]@{
                ok = $true; exitCode = 0; reason = $null
                result = [pscustomobject]@{ branch = 'feat/710-no-ollama'; commits = @(); iterations = @() }
            }
        }

        Invoke-Watchdog -Repo 'jarvis' -MaxIterations 1 -Model 'qwen-large' `
            -Tier1Model 'qwen-small' -Tier2Provider 'deepseek' -Tier2AsPrimary `
            -WindowEnd '' -DockerTimeoutSec 5 -OllamaTimeoutSec 5

        Assert-MockCalled Start-OllamaServer -Times 0 -Exactly -Scope It
        Assert-MockCalled Invoke-Sandcastle  -Times 1 -Exactly -Scope It
    }

    It 'AC: Tier 2 as primary failure does NOT cascade to Tier 0/1 (no escalation when primary itself is remote)' {
        $script:calls = @()
        Mock Invoke-Sandcastle {
            $script:calls += @{ Model = $Model; Token = $AuthToken }
            [pscustomobject]@{
                ok = $false; exitCode = 1; reason = 'exit=1'
                result = [pscustomobject]@{ branch = 'feat/720-tier2-fail' }
            }
        }
        Mock Test-IsOOM { $true }

        { Invoke-Watchdog -Repo 'jarvis' -MaxIterations 1 -Model 'qwen-large' `
              -Tier1Model 'qwen-small' -Tier2Provider 'deepseek' -Tier2AsPrimary `
              -WindowEnd '' -DockerTimeoutSec 5 -OllamaTimeoutSec 5 } | Should Throw

        Assert-MockCalled Invoke-Sandcastle -Times 1 -Exactly -Scope It
        $script:calls[0].Token | Should Be 'ds-key'
    }

    It 'AC: Tier 2 as primary but DEEPSEEK_API_KEY missing -> warning + falls back to Ollama chain' {
        Mock Read-DotEnvFile {
            @{
                SUPABASE_URL       = 'https://x'
                SUPABASE_KEY       = 'k'
                TELEGRAM_BOT_TOKEN = 't'
                TELEGRAM_CHAT_ID   = '1'
                # No DEEPSEEK_API_KEY -- Tier 2 primary resolution fails open
                # to the Ollama chain (warn + downgrade, not throw) so a
                # mis-provisioned .env still produces work via local models.
            }
        }
        $script:calls = @()
        Mock Invoke-Sandcastle {
            $script:calls += @{ Model = $Model; Token = $AuthToken }
            [pscustomobject]@{
                ok = $true; exitCode = 0; reason = $null
                result = [pscustomobject]@{ branch = 'feat/730-fallback'; commits = @(); iterations = @() }
            }
        }
        Mock Test-IsOOM { $false }

        Invoke-Watchdog -Repo 'jarvis' -MaxIterations 1 -Model 'qwen-large' `
            -Tier1Model 'qwen-small' -Tier2Provider 'deepseek' -Tier2AsPrimary `
            -WindowEnd '' -DockerTimeoutSec 5 -OllamaTimeoutSec 5

        # When primary resolution fails, Ollama chain takes over -> first call is Tier 0
        $script:calls[0].Model | Should Be 'qwen-large'
        Assert-MockCalled Write-OutcomeRecord -Times 1 -Exactly -Scope It `
            -ParameterFilter { $LlmMetrics.tier -eq 'tier0' }
    }

    It 'AC: -Tier2AsPrimary without -Tier2Provider stays on Ollama chain (no-op switch)' {
        $script:calls = @()
        Mock Invoke-Sandcastle {
            $script:calls += @{ Model = $Model }
            [pscustomobject]@{
                ok = $true; exitCode = 0; reason = $null
                result = [pscustomobject]@{ branch = 'feat/740-noop'; commits = @(); iterations = @() }
            }
        }
        Mock Test-IsOOM { $false }

        Invoke-Watchdog -Repo 'jarvis' -MaxIterations 1 -Model 'qwen-large' `
            -Tier1Model 'qwen-small' -Tier2Provider '' -Tier2AsPrimary `
            -WindowEnd '' -DockerTimeoutSec 5 -OllamaTimeoutSec 5

        $script:calls[0].Model | Should Be 'qwen-large'
    }

    It 'AC: Tier 2 as primary ignores use-claude-api label because no issue is known yet' {
        # Real Get-IssueLabels short-circuits to @() when Issue is 0/empty (the
        # state at primary-resolution time, before an issue is picked from the
        # queue). Mirror that here so the test exercises production behavior
        # and not the test harness's blanket mock.
        Mock Get-IssueLabels {
            param([int]$Issue, [string]$RepoSlug)
            if (-not $Issue) { return @() }
            return @('use-claude-api')
        }
        $script:calls = @()
        Mock Invoke-Sandcastle {
            $script:calls += @{ Model = $Model; Token = $AuthToken; BaseUrl = $BaseUrl }
            [pscustomobject]@{
                ok = $true; exitCode = 0; reason = $null
                result = [pscustomobject]@{ branch = 'feat/750-claude-primary'; commits = @(); iterations = @() }
            }
        }
        Mock Test-IsOOM { $false }

        Invoke-Watchdog -Repo 'jarvis' -MaxIterations 1 -Model 'qwen-large' `
            -Tier1Model 'qwen-small' -Tier2Provider 'deepseek' -Tier2AsPrimary `
            -WindowEnd '' -DockerTimeoutSec 5 -OllamaTimeoutSec 5

        # Documents the invariant: the use-claude-api label flip is issue-scoped
        # and only fires on Tier 2 *escalation* (where a target issue is known),
        # not on Tier 2 *primary* (where Issue=0 by construction). Owner must
        # set CLAUDE_MODEL/CLAUDE_BASE_URL/ANTHROPIC_API_KEY env vars to make
        # Claude the configured provider when Tier 2 primary is desired with
        # Anthropic instead of DeepSeek.
        $script:calls[0].Token | Should Be 'ds-key'
    }

    It 'AC #955: Tier 2 billing failure (DeepSeek 402) -> provider-billing outcome + Telegram alert' {
        Mock Invoke-Sandcastle {
            [pscustomobject]@{ ok = $false; exitCode = 1; result = $null; reason = 'exit=1' }
        }
        Mock Get-LogTail {
            "agent boot`nAPI Error: 402 {`"error`":{`"message`":`"Insufficient Balance`"}}"
        }
        Mock Test-IsOOM { $false }

        { Invoke-Watchdog -Repo 'jarvis' -MaxIterations 1 -Model 'qwen-large' `
              -Tier1Model 'qwen-small' -Tier2Provider 'deepseek' -Tier2AsPrimary `
              -WindowEnd '' -DockerTimeoutSec 5 -OllamaTimeoutSec 5 } | Should Throw

        Assert-MockCalled Write-OutcomeRecord -Times 1 -Exactly -Scope It `
            -ParameterFilter {
                $Status -eq 'failure' -and
                $Summary -like '*provider-billing*' -and
                $LlmMetrics.provider_billing -eq $true -and
                -not $LlmMetrics.error_class
            }
        Assert-MockCalled Send-TelegramAlert -Times 1 -Exactly -Scope It `
            -ParameterFilter { $Message -like '*provider-billing*' }
        Assert-MockCalled Add-IssueLabel -Times 0 -Exactly -Scope It
    }

    It 'AC #955: generic Tier 2 failure without billing signature stays silent (no Telegram)' {
        Mock Invoke-Sandcastle {
            [pscustomobject]@{ ok = $false; exitCode = 1; result = $null; reason = 'exit=1' }
        }
        Mock Get-LogTail { 'AgentError: agent exited non-zero' }
        Mock Test-IsOOM { $false }

        { Invoke-Watchdog -Repo 'jarvis' -MaxIterations 1 -Model 'qwen-large' `
              -Tier1Model 'qwen-small' -Tier2Provider 'deepseek' -Tier2AsPrimary `
              -WindowEnd '' -DockerTimeoutSec 5 -OllamaTimeoutSec 5 } | Should Throw

        Assert-MockCalled Write-OutcomeRecord -Times 1 -Exactly -Scope It `
            -ParameterFilter { $Status -eq 'failure' -and $Summary -notlike '*provider-billing*' }
        Assert-MockCalled Send-TelegramAlert -Times 0 -Exactly -Scope It
    }

    It 'AC #955: pre-flight balance probe exhausted -> fail fast before any container run + alert' {
        Mock Test-DeepSeekBalance { $false }
        Mock Invoke-Sandcastle {
            [pscustomobject]@{ ok = $true; exitCode = 0; reason = $null
                result = [pscustomobject]@{ branch = 'feat/955-x'; commits = @(); iterations = @() } }
        }

        { Invoke-Watchdog -Repo 'jarvis' -MaxIterations 1 -Model 'qwen-large' `
              -Tier1Model 'qwen-small' -Tier2Provider 'deepseek' -Tier2AsPrimary `
              -WindowEnd '' -DockerTimeoutSec 5 -OllamaTimeoutSec 5 } | Should Throw

        Assert-MockCalled Invoke-Sandcastle -Times 0 -Exactly -Scope It
        Assert-MockCalled Write-OutcomeRecord -Times 1 -Exactly -Scope It `
            -ParameterFilter { $Status -eq 'failure' -and $Summary -like 'provider-billing*pre-flight*' -and $LlmMetrics.provider_billing -eq $true }
        Assert-MockCalled Send-TelegramAlert -Times 1 -Exactly -Scope It `
            -ParameterFilter { $Message -like '*provider-billing*' }
        Assert-MockCalled Add-IssueLabel -Times 0 -Exactly -Scope It
    }

    It 'AC #955: balance probe is deepseek-only -- claude primary never consults it' {
        Mock Test-DeepSeekBalance { $false }   # would block if consulted
        Mock Invoke-Sandcastle {
            [pscustomobject]@{ ok = $true; exitCode = 0; reason = $null
                result = [pscustomobject]@{ branch = 'feat/955-claude'; commits = @(); iterations = @() } }
        }
        Mock Test-IsOOM { $false }

        Invoke-Watchdog -Repo 'jarvis' -MaxIterations 1 -Model 'qwen-large' `
            -Tier1Model 'qwen-small' -Tier2Provider 'claude' -Tier2AsPrimary `
            -WindowEnd '' -DockerTimeoutSec 5 -OllamaTimeoutSec 5

        Assert-MockCalled Test-DeepSeekBalance -Times 0 -Exactly -Scope It
        Assert-MockCalled Invoke-Sandcastle    -Times 1 -Exactly -Scope It
    }
}

Describe 'Invoke-Watchdog subscription-primary (Anthropic Max Agent-SDK credit, #972)' {
    # When -SubscriptionPrimary is set, the watchdog runs the subscription tier
    # (AuthMode=subscription, no BaseUrl/Token) as primary, skips Ollama, and
    # falls back to the DeepSeek endpoint on any subscription failure.
    BeforeEach {
        Mock Test-DockerRunning { $true }
        Mock Test-OllamaRunning { $true }
        Mock Start-DockerDesktop { }
        Mock Start-OllamaServer  { }
        Mock Get-RepoRoot { $env:TEMP }
        Mock Read-DotEnvFile {
            @{
                SUPABASE_URL       = 'https://x'
                SUPABASE_KEY       = 'k'
                TELEGRAM_BOT_TOKEN = 't'
                TELEGRAM_CHAT_ID   = '1'
                DEEPSEEK_API_KEY   = 'ds-key'
            }
        }
        Mock New-RuntimeDir { Join-Path $env:TEMP "sandcastle-subprimary-$([guid]::NewGuid())" }
        Mock Invoke-RuntimeSweep { @() }
        Mock Write-OutcomeRecord { 'mocked' }
        Mock Send-TelegramAlert  { 'mocked' }
        Mock Add-IssueLabel      { $true }
        Mock Get-IssueLabels     { @() }
    }

    It 'AC: subscription primary -> single call with AuthMode=subscription, no BaseUrl/Token, no Ollama' {
        $script:calls = @()
        Mock Invoke-Sandcastle {
            $script:calls += @{ Model = $Model; Mode = $AuthMode; Effort = $Effort; BaseUrl = $BaseUrl; Token = $AuthToken }
            [pscustomobject]@{
                ok = $true; exitCode = 0; reason = $null
                result = [pscustomobject]@{ branch = 'feat/900-sub'; commits = @(); iterations = @() }
            }
        }
        Mock Test-IsOOM { $false }

        Invoke-Watchdog -Repo 'jarvis' -MaxIterations 1 -Model 'qwen-large' `
            -Tier1Model 'qwen-small' -Tier2Provider 'deepseek' -SubscriptionPrimary `
            -SubscriptionModel 'claude-opus-4-8' -SubscriptionEffort 'medium' `
            -WindowEnd '' -DockerTimeoutSec 5 -OllamaTimeoutSec 5

        Assert-MockCalled Invoke-Sandcastle -Times 1 -Exactly -Scope It
        $script:calls[0].Model   | Should Be 'claude-opus-4-8'
        $script:calls[0].Mode    | Should Be 'subscription'
        $script:calls[0].Effort  | Should Be 'medium'
        $script:calls[0].BaseUrl | Should BeNullOrEmpty
        $script:calls[0].Token   | Should BeNullOrEmpty
        Assert-MockCalled Start-OllamaServer -Times 0 -Exactly -Scope It
        Assert-MockCalled Write-OutcomeRecord -Times 1 -Exactly -Scope It `
            -ParameterFilter { $Status -eq 'success' -and $LlmMetrics.tier -eq 'subscription' }
    }

    It 'AC: subscription primary across 2 iterations -> re-invokes subscription each iter, accumulates usage' {
        # Guards the multi-iteration loop state for the subscription path: with
        # no completionSignal the watchdog must re-run the subscription tier on
        # iteration 2 (a fresh free-pick), and $totalUsage must accumulate across
        # both iterations rather than reset. A regression in iteration-N reuse or
        # token accumulation would otherwise pass silently (all other subscription
        # tests use MaxIterations=1).
        $script:calls = @()
        Mock Invoke-Sandcastle {
            $script:calls += @{ Mode = $AuthMode; Target = $TargetIssue }
            [pscustomobject]@{
                ok = $true; exitCode = 0; reason = $null
                result = [pscustomobject]@{
                    branch = "feat/90$($script:calls.Count)-iter"; commits = @('c'); iterations = @(
                        [pscustomobject]@{ usage = [pscustomobject]@{
                            inputTokens = 10; outputTokens = 5
                            cacheReadInputTokens = 0; cacheCreationInputTokens = 0 } }
                    )
                    # no completionSignal -> loop continues to iteration 2
                }
            }
        }
        Mock Test-IsOOM { $false }

        Invoke-Watchdog -Repo 'jarvis' -MaxIterations 2 -Model 'qwen-large' `
            -Tier1Model 'qwen-small' -Tier2Provider 'deepseek' -SubscriptionPrimary `
            -SubscriptionModel 'claude-opus-4-8' -SubscriptionEffort 'medium' `
            -WindowEnd '' -DockerTimeoutSec 5 -OllamaTimeoutSec 5

        Assert-MockCalled Invoke-Sandcastle -Times 2 -Exactly -Scope It
        $script:calls[0].Mode | Should Be 'subscription'
        $script:calls[1].Mode | Should Be 'subscription'   # iteration 2 stays on the subscription primary
        # Usage accumulated across both iterations (10+10 in, 5+5 out), tier preserved.
        Assert-MockCalled Write-OutcomeRecord -Times 1 -Exactly -Scope It `
            -ParameterFilter {
                $Status -eq 'success' -and $LlmMetrics.tier -eq 'subscription' -and
                $LlmMetrics.input_tokens -eq 20 -and $LlmMetrics.output_tokens -eq 10
            }
    }

    It 'AC: subscription primary skips Ollama daemon check (Ollama down does not matter)' {
        Mock Test-OllamaRunning { $false }
        Mock Invoke-Sandcastle {
            [pscustomobject]@{
                ok = $true; exitCode = 0; reason = $null
                result = [pscustomobject]@{ branch = 'feat/901-no-ollama'; commits = @(); iterations = @() }
            }
        }

        Invoke-Watchdog -Repo 'jarvis' -MaxIterations 1 -Model 'qwen-large' `
            -Tier1Model 'qwen-small' -Tier2Provider 'deepseek' -SubscriptionPrimary `
            -WindowEnd '' -DockerTimeoutSec 5 -OllamaTimeoutSec 5

        Assert-MockCalled Start-OllamaServer -Times 0 -Exactly -Scope It
        Assert-MockCalled Invoke-Sandcastle  -Times 1 -Exactly -Scope It
    }

    It 'AC: subscription failure -> falls back to DeepSeek for that iteration' {
        $script:calls = @()
        Mock Invoke-Sandcastle {
            $script:calls += @{ Model = $Model; Mode = $AuthMode; BaseUrl = $BaseUrl; Token = $AuthToken }
            if ($AuthMode -eq 'subscription') {
                [pscustomobject]@{ ok = $false; exitCode = 1; reason = 'exit=1'; result = $null }
            } else {
                [pscustomobject]@{
                    ok = $true; exitCode = 0; reason = $null
                    result = [pscustomobject]@{ branch = 'feat/902-fallback'; commits = @(); iterations = @() }
                }
            }
        }
        Mock Test-IsOOM { $false }

        Invoke-Watchdog -Repo 'jarvis' -MaxIterations 1 -Model 'qwen-large' `
            -Tier1Model 'qwen-small' -Tier2Provider 'deepseek' -SubscriptionPrimary `
            -WindowEnd '' -DockerTimeoutSec 5 -OllamaTimeoutSec 5

        Assert-MockCalled Invoke-Sandcastle -Times 2 -Exactly -Scope It
        $script:calls[0].Mode    | Should Be 'subscription'
        $script:calls[1].Mode    | Should Be 'endpoint'
        $script:calls[1].Token   | Should Be 'ds-key'
        $script:calls[1].BaseUrl | Should Be 'https://api.deepseek.com/anthropic'
        # Compound tier: the subscription tier was attempted first, then the
        # DeepSeek fallback succeeded -- the outcome row must show BOTH so a
        # trace can distinguish this from a pure DeepSeek-primary success.
        Assert-MockCalled Write-OutcomeRecord -Times 1 -Exactly -Scope It `
            -ParameterFilter { $Status -eq 'success' -and $LlmMetrics.tier -eq 'subscription>tier2:deepseek' }
    }

    It 'AC: subscription failure with no DeepSeek fallback configured -> throws (no silent skip)' {
        Mock Read-DotEnvFile {
            @{
                SUPABASE_URL       = 'https://x'
                SUPABASE_KEY       = 'k'
                TELEGRAM_BOT_TOKEN = 't'
                TELEGRAM_CHAT_ID   = '1'
                # no DEEPSEEK_API_KEY -> no fallback
            }
        }
        $script:calls = @()
        Mock Invoke-Sandcastle {
            $script:calls += @{ Mode = $AuthMode }
            [pscustomobject]@{ ok = $false; exitCode = 1; reason = 'exit=1'; result = $null }
        }
        Mock Test-IsOOM { $false }

        { Invoke-Watchdog -Repo 'jarvis' -MaxIterations 1 -Model 'qwen-large' `
              -Tier1Model 'qwen-small' -Tier2Provider 'deepseek' -SubscriptionPrimary `
              -WindowEnd '' -DockerTimeoutSec 5 -OllamaTimeoutSec 5 } | Should Throw

        Assert-MockCalled Invoke-Sandcastle -Times 1 -Exactly -Scope It
        $script:calls[0].Mode | Should Be 'subscription'
    }

    It 'AC: subscription failure AND DeepSeek fallback also fails -> records failure + throws' {
        # Both tiers down: subscription credit exhausted *and* DeepSeek 402/down.
        # Must not silently swallow — the shared no-result handler records a
        # 'failure' outcome and rethrows so the iteration is visibly broken.
        $script:calls = @()
        Mock Invoke-Sandcastle {
            $script:calls += @{ Mode = $AuthMode }
            # ok=$false on BOTH the subscription call and the DeepSeek fallback.
            [pscustomobject]@{ ok = $false; exitCode = 1; reason = 'exit=1'; result = $null }
        }
        Mock Test-IsOOM { $false }

        { Invoke-Watchdog -Repo 'jarvis' -MaxIterations 1 -Model 'qwen-large' `
              -Tier1Model 'qwen-small' -Tier2Provider 'deepseek' -SubscriptionPrimary `
              -WindowEnd '' -DockerTimeoutSec 5 -OllamaTimeoutSec 5 } | Should Throw

        # Subscription first, then the DeepSeek fallback — two attempts, both failed.
        Assert-MockCalled Invoke-Sandcastle -Times 2 -Exactly -Scope It
        $script:calls[0].Mode | Should Be 'subscription'
        $script:calls[1].Mode | Should Be 'endpoint'
        # The failure record must carry the COMPOUND tier so the outcome row
        # shows subscription was attempted first THEN the DeepSeek fallback ran
        # — a bare "tier2:deepseek" would be indistinguishable from a plain
        # DeepSeek-primary double-failure (the round-5 reviewer's point).
        Assert-MockCalled Write-OutcomeRecord -Times 1 -Exactly -Scope It `
            -ParameterFilter { $Status -eq 'failure' -and $LlmMetrics.tier -eq 'subscription>tier2:deepseek' }
    }

    It 'AC: -SubscriptionPrimary supersedes -Tier2AsPrimary (both set -> subscription wins)' {
        $script:calls = @()
        Mock Invoke-Sandcastle {
            $script:calls += @{ Model = $Model; Mode = $AuthMode }
            [pscustomobject]@{
                ok = $true; exitCode = 0; reason = $null
                result = [pscustomobject]@{ branch = 'feat/903-super'; commits = @(); iterations = @() }
            }
        }
        Mock Test-IsOOM { $false }

        Invoke-Watchdog -Repo 'jarvis' -MaxIterations 1 -Model 'qwen-large' `
            -Tier1Model 'qwen-small' -Tier2Provider 'deepseek' -Tier2AsPrimary -SubscriptionPrimary `
            -WindowEnd '' -DockerTimeoutSec 5 -OllamaTimeoutSec 5

        Assert-MockCalled Invoke-Sandcastle -Times 1 -Exactly -Scope It
        $script:calls[0].Mode  | Should Be 'subscription'
        $script:calls[0].Model | Should Be 'claude-opus-4-8'
    }
}

Describe 'Invoke-Watchdog daemon-state matrix' {
    BeforeEach {
        Mock Start-DockerDesktop { }
        Mock Start-OllamaServer  { }
        Mock Invoke-Sandcastle {
            [pscustomobject]@{
                ok       = $true
                exitCode = 0
                result   = [pscustomobject]@{
                    branch     = 'feat/test'
                    commits    = @(@{ sha = 'abc123' })
                    iterations = @(
                        [pscustomobject]@{
                            usage = [pscustomobject]@{
                                inputTokens               = 1000
                                outputTokens              = 200
                                cacheReadInputTokens      = 50
                                cacheCreationInputTokens  = 10
                            }
                        }
                    )
                }
            }
        }
        Mock Write-OutcomeRecord { 'mocked-outcome-id' }
        Mock Get-RepoRoot { $env:TEMP }
        Mock Read-DotEnvFile {
            @{
                SUPABASE_URL       = 'https://x'
                SUPABASE_KEY       = 'k'
                TELEGRAM_BOT_TOKEN = 'test-token'
                TELEGRAM_CHAT_ID   = '12345'
            }
        }
        # No real .sandcastle/runtime/* directories or HTTP calls during tests.
        Mock New-RuntimeDir { Join-Path $env:TEMP "sandcastle-test-$([guid]::NewGuid())" }
        Mock Invoke-RuntimeSweep { @() }   # #572: keep sweep no-op in matrix tests
        Mock Send-TelegramAlert { 'mocked-tg-response' }
    }

    It 'records success when both daemons are up (no Telegram alert)' {
        Mock Test-DockerRunning { $true }
        Mock Test-OllamaRunning { $true }

        Invoke-Watchdog -Repo 'jarvis' -MaxIterations 1 -Model 'qwen2.5-coder:14b' `
            -WindowEnd '' -DockerTimeoutSec 5 -OllamaTimeoutSec 5

        Assert-MockCalled Start-DockerDesktop -Times 0 -Exactly -Scope It
        Assert-MockCalled Start-OllamaServer  -Times 0 -Exactly -Scope It
        Assert-MockCalled Invoke-Sandcastle   -Times 1 -Exactly -Scope It
        Assert-MockCalled Write-OutcomeRecord -Times 1 -Exactly -Scope It `
            -ParameterFilter { $Status -eq 'success' }
        Assert-MockCalled Send-TelegramAlert  -Times 0 -Exactly -Scope It
    }

    It 'records failure: docker-down when Docker never starts (fires one Telegram alert)' {
        Mock Test-DockerRunning { $false }
        Mock Test-OllamaRunning { $true }

        { Invoke-Watchdog -Repo 'jarvis' -MaxIterations 1 -Model 'm' `
            -WindowEnd '' -DockerTimeoutSec 1 -OllamaTimeoutSec 1 } | Should Throw 'docker-down'

        Assert-MockCalled Start-DockerDesktop -Times 1 -Exactly -Scope It
        Assert-MockCalled Invoke-Sandcastle   -Times 0 -Exactly -Scope It
        Assert-MockCalled Write-OutcomeRecord -Times 1 -Exactly -Scope It `
            -ParameterFilter { $Status -eq 'failure' -and $Summary -like '*docker-down*' }
        # AC: Docker autostart timeout produces exactly one Telegram message
        # with run id, repo, and (via log path) timestamp.
        Assert-MockCalled Send-TelegramAlert -Times 1 -Exactly -Scope It `
            -ParameterFilter {
                $Message -like '*docker-down*' -and
                $Message -like '*sandcastle:jarvis*' -and
                $Message -like '*run=jarvis-watchdog-*' -and
                $Message -like '*log=*run.log*' -and
                $Message.Length -le 200
            }
    }

    It 'records failure: ollama-down when Ollama never starts (fires one Telegram alert)' {
        Mock Test-DockerRunning { $true }
        Mock Test-OllamaRunning { $false }

        { Invoke-Watchdog -Repo 'jarvis' -MaxIterations 1 -Model 'm' `
            -WindowEnd '' -DockerTimeoutSec 1 -OllamaTimeoutSec 1 } | Should Throw 'ollama-down'

        Assert-MockCalled Start-OllamaServer  -Times 1 -Exactly -Scope It
        Assert-MockCalled Invoke-Sandcastle   -Times 0 -Exactly -Scope It
        Assert-MockCalled Write-OutcomeRecord -Times 1 -Exactly -Scope It `
            -ParameterFilter { $Status -eq 'failure' -and $Summary -like '*ollama-down*' }
        Assert-MockCalled Send-TelegramAlert  -Times 1 -Exactly -Scope It `
            -ParameterFilter { $Message -like '*ollama-down*' }
    }

    It 'records failure when both daemons time out (Docker fails first, fail-fast)' {
        Mock Test-DockerRunning { $false }
        Mock Test-OllamaRunning { $false }

        { Invoke-Watchdog -Repo 'jarvis' -MaxIterations 1 -Model 'm' `
            -WindowEnd '' -DockerTimeoutSec 1 -OllamaTimeoutSec 1 } | Should Throw

        Assert-MockCalled Start-DockerDesktop -Times 1 -Exactly -Scope It
        Assert-MockCalled Start-OllamaServer  -Times 0 -Exactly -Scope It
        Assert-MockCalled Invoke-Sandcastle   -Times 0 -Exactly -Scope It
    }

    It 'soft-stops with partial:window-expired before iteration starts (no Telegram)' {
        Mock Test-DockerRunning { $true }
        Mock Test-OllamaRunning { $true }

        Invoke-Watchdog -Repo 'jarvis' -MaxIterations 3 -Model 'm' `
            -WindowEnd ((Get-Date).AddMinutes(-1).ToString('s')) `
            -DockerTimeoutSec 5 -OllamaTimeoutSec 5

        Assert-MockCalled Invoke-Sandcastle   -Times 0 -Exactly -Scope It
        Assert-MockCalled Write-OutcomeRecord -Times 1 -Exactly -Scope It `
            -ParameterFilter { $Status -eq 'partial' -and $Summary -like '*window-expired*' }
        Assert-MockCalled Send-TelegramAlert  -Times 0 -Exactly -Scope It
    }

    It 'AC: 5-iteration AFK run with one OOM-escalated success produces zero Telegram calls' {
        # Slice 5 self-recovers OOM via model fallback. From the watchdog's
        # vantage point that iteration still returns ok=true. Routine
        # iteration outcomes (success / partial) MUST stay silent.
        Mock Test-DockerRunning { $true }
        Mock Test-OllamaRunning { $true }

        Invoke-Watchdog -Repo 'jarvis' -MaxIterations 5 -Model 'm' `
            -WindowEnd '' -DockerTimeoutSec 5 -OllamaTimeoutSec 5

        Assert-MockCalled Invoke-Sandcastle  -Times 5 -Exactly -Scope It
        Assert-MockCalled Send-TelegramAlert -Times 0 -Exactly -Scope It
    }

    It 'AC: agent-side exit failure (non-infra reason) does NOT fire Telegram' {
        Mock Test-DockerRunning { $true }
        Mock Test-OllamaRunning { $true }
        Mock Invoke-Sandcastle {
            [pscustomobject]@{ ok = $false; exitCode = 7; result = $null; reason = 'exit=7' }
        }

        { Invoke-Watchdog -Repo 'jarvis' -MaxIterations 1 -Model 'm' `
            -WindowEnd '' -DockerTimeoutSec 5 -OllamaTimeoutSec 5 } | Should Throw

        Assert-MockCalled Write-OutcomeRecord -Times 1 -Exactly -Scope It `
            -ParameterFilter { $Status -eq 'failure' }
        Assert-MockCalled Send-TelegramAlert -Times 0 -Exactly -Scope It
    }

    It 'AC: no-result-file surfaces as infra-down and fires Telegram' {
        Mock Test-DockerRunning { $true }
        Mock Test-OllamaRunning { $true }
        Mock Invoke-Sandcastle {
            [pscustomobject]@{ ok = $false; exitCode = 0; result = $null; reason = 'no-result-file' }
        }

        { Invoke-Watchdog -Repo 'jarvis' -MaxIterations 1 -Model 'm' `
            -WindowEnd '' -DockerTimeoutSec 5 -OllamaTimeoutSec 5 } | Should Throw

        Assert-MockCalled Send-TelegramAlert -Times 1 -Exactly -Scope It `
            -ParameterFilter { $Message -like '*no-result-file*' }
    }

    It 'AC: npm-not-found surfaces as infra-down and fires Telegram' {
        Mock Test-DockerRunning { $true }
        Mock Test-OllamaRunning { $true }
        Mock Invoke-Sandcastle {
            [pscustomobject]@{ ok = $false; exitCode = -1; result = $null; reason = 'npm-not-found' }
        }

        { Invoke-Watchdog -Repo 'jarvis' -MaxIterations 1 -Model 'm' `
            -WindowEnd '' -DockerTimeoutSec 5 -OllamaTimeoutSec 5 } | Should Throw

        Assert-MockCalled Send-TelegramAlert -Times 1 -Exactly -Scope It `
            -ParameterFilter { $Message -like '*npm-not-found*' }
    }

    It 'accumulates commits and usage across multiple successful iterations' {
        Mock Test-DockerRunning { $true }
        Mock Test-OllamaRunning { $true }

        Invoke-Watchdog -Repo 'jarvis' -MaxIterations 3 -Model 'm' `
            -WindowEnd '' -DockerTimeoutSec 5 -OllamaTimeoutSec 5

        # 3 iterations × the BeforeEach mock (1k input, 200 output, 1 commit each)
        Assert-MockCalled Invoke-Sandcastle -Times 3 -Exactly -Scope It
        Assert-MockCalled Write-OutcomeRecord -Times 1 -Exactly -Scope It `
            -ParameterFilter {
                $Status -eq 'success' -and
                $Summary -like '*iterations=3*' -and
                $Summary -like '*commits=3*' -and
                $LlmMetrics.input_tokens -eq 3000 -and
                $LlmMetrics.output_tokens -eq 600
            }
    }

    It 'records failure when npm is not on PATH (npm-not-found surfaces through Record)' {
        Mock Test-DockerRunning { $true }
        Mock Test-OllamaRunning { $true }
        Mock Invoke-Sandcastle {
            [pscustomobject]@{ ok = $false; exitCode = -1; result = $null; reason = 'npm-not-found' }
        }

        { Invoke-Watchdog -Repo 'jarvis' -MaxIterations 1 -Model 'm' `
            -WindowEnd '' -DockerTimeoutSec 5 -OllamaTimeoutSec 5 } |
            Should Throw 'npm-not-found'

        Assert-MockCalled Write-OutcomeRecord -Times 1 -Exactly -Scope It `
            -ParameterFilter { $Status -eq 'failure' -and $Summary -like '*npm-not-found*' }
    }

    It 'records failure and stops loop when sandcastle invocation returns ok=false' {
        Mock Test-DockerRunning { $true }
        Mock Test-OllamaRunning { $true }
        Mock Invoke-Sandcastle {
            [pscustomobject]@{ ok = $false; exitCode = 0; result = $null; reason = 'no-result-file' }
        }

        { Invoke-Watchdog -Repo 'jarvis' -MaxIterations 3 -Model 'm' `
            -WindowEnd '' -DockerTimeoutSec 5 -OllamaTimeoutSec 5 } |
            Should Throw 'no-result-file'

        Assert-MockCalled Invoke-Sandcastle   -Times 1 -Exactly -Scope It
        Assert-MockCalled Write-OutcomeRecord -Times 1 -Exactly -Scope It `
            -ParameterFilter { $Status -eq 'failure' -and $Summary -like '*no-result-file*' }
    }

    It 'soft-stops mid-run when window expires after iteration N succeeds' {
        Mock Test-DockerRunning { $true }
        Mock Test-OllamaRunning { $true }

        # Window expires partway through. First call → not expired; subsequent
        # calls → expired. With MaxIterations=3 the watchdog should run
        # exactly one iteration then bail with partial:window-expired.
        $script:windowChecks = 0
        Mock Test-WindowExpired {
            $script:windowChecks++
            return ($script:windowChecks -gt 1)
        }

        Invoke-Watchdog -Repo 'jarvis' -MaxIterations 3 -Model 'm' `
            -WindowEnd '23:59' -DockerTimeoutSec 5 -OllamaTimeoutSec 5

        Assert-MockCalled Invoke-Sandcastle   -Times 1 -Exactly -Scope It
        Assert-MockCalled Write-OutcomeRecord -Times 1 -Exactly -Scope It `
            -ParameterFilter {
                $Status -eq 'partial' -and
                $Summary -like '*window-expired*' -and
                $Summary -like '*iterations=1*'
            }
    }

    It 'stops looping early when the agent signals the queue is drained' {
        # Completion signal (prompt.md §Done) => queue empty. The watchdog must
        # NOT keep re-invoking on an empty queue (those redundant runs are the
        # source of the spurious nightly `failure` rows). One invocation, then
        # a clean success:idle heartbeat -- not five rolls of the crash dice.
        Mock Test-DockerRunning { $true }
        Mock Test-OllamaRunning { $true }
        Mock Test-WindowExpired { $false }   # isolate from leaked window mocks (Pester 3.4 It-scope leak)
        Mock Invoke-Sandcastle {
            [pscustomobject]@{
                ok = $true; exitCode = 0
                result = [pscustomobject]@{
                    branch           = $null
                    commits          = @()
                    completionSignal = '<promise>COMPLETE</promise>'
                    iterations       = @()
                }
            }
        }

        Invoke-Watchdog -Repo 'jarvis' -MaxIterations 5 -Model 'm' `
            -WindowEnd '' -DockerTimeoutSec 5 -OllamaTimeoutSec 5

        Assert-MockCalled Invoke-Sandcastle   -Times 1 -Exactly -Scope It
        Assert-MockCalled Write-OutcomeRecord -Times 1 -Exactly -Scope It `
            -ParameterFilter { $Status -eq 'success' -and $Summary -like '*success:idle*' }
        Assert-MockCalled Send-TelegramAlert  -Times 0 -Exactly -Scope It
    }

    It 'does NOT stop early when a productive iteration omits the completion signal' {
        # Regression guard for the multi-issue-per-night flow: an iteration that
        # did work but left issues in the queue carries no completion signal, so
        # the watchdog must run the full MaxIterations (one issue per iteration).
        Mock Test-DockerRunning { $true }
        Mock Test-OllamaRunning { $true }
        Mock Test-WindowExpired { $false }   # isolate from leaked window mocks (Pester 3.4 It-scope leak)
        # BeforeEach mock returns a result WITHOUT completionSignal => no early stop.

        Invoke-Watchdog -Repo 'jarvis' -MaxIterations 3 -Model 'm' `
            -WindowEnd '' -DockerTimeoutSec 5 -OllamaTimeoutSec 5

        Assert-MockCalled Invoke-Sandcastle   -Times 3 -Exactly -Scope It
        Assert-MockCalled Write-OutcomeRecord -Times 1 -Exactly -Scope It `
            -ParameterFilter { $Status -eq 'success' -and $Summary -notlike '*success:idle*' }
    }

    It 'redacts the tier2 auth token from diag_tail on a billing failure path' {
        Mock Test-DockerRunning { $true }
        Mock Test-OllamaRunning { $true }
        Mock Test-WindowExpired { $false }
        $dsKey = 'sk-' + ('a' * 40)
        Mock Read-DotEnvFile {
            @{
                SUPABASE_URL       = 'https://x'
                SUPABASE_KEY       = 'k'
                TELEGRAM_BOT_TOKEN = 't'
                TELEGRAM_CHAT_ID   = '1'
                DEEPSEEK_API_KEY   = $dsKey
            }
        }
        Mock Invoke-Sandcastle {
            [pscustomobject]@{ ok = $false; exitCode = 1; result = $null; reason = 'exit=1' }
        }
        Mock Get-LogTail { "Authorization: Bearer $dsKey returned 402" }
        Mock Test-IsOOM { $false }
        Mock Test-DeepSeekBalance { $true }

        { Invoke-Watchdog -Repo 'jarvis' -MaxIterations 1 -Model 'qwen-large' `
            -Tier1Model '' -Tier2Provider 'deepseek' -Tier2AsPrimary `
            -WindowEnd '' -DockerTimeoutSec 5 -OllamaTimeoutSec 5 } | Should Throw

        Assert-MockCalled Write-OutcomeRecord -Times 1 -Exactly -Scope It `
            -ParameterFilter {
                $LlmMetrics.diag_tail -notlike "*$dsKey*"
            }
    }

    It 'enriches the failure outcome with the tagged-error class and a redacted log tail' {
        # An opaque `exit=1` must arrive in Supabase carrying the crash class
        # (so Worktree vs idle vs API is distinguishable) and a secret-scrubbed
        # tail of the run.log.
        Mock Test-DockerRunning { $true }
        Mock Test-OllamaRunning { $true }
        Mock Test-WindowExpired { $false }   # isolate from leaked window mocks (Pester 3.4 It-scope leak)
        Mock Invoke-Sandcastle {
            [pscustomobject]@{ ok = $false; exitCode = 1; result = $null; reason = 'exit=1' }
        }
        Mock Get-LogTail {
            "agent noise`nWorktreeError: git worktree remove failed: NTFS reparse point`ntrailing"
        }

        { Invoke-Watchdog -Repo 'jarvis' -MaxIterations 1 -Model 'm' `
            -WindowEnd '' -DockerTimeoutSec 5 -OllamaTimeoutSec 5 } | Should Throw

        Assert-MockCalled Write-OutcomeRecord -Times 1 -Exactly -Scope It `
            -ParameterFilter {
                $Status -eq 'failure' -and
                $Summary -like '*class=WorktreeError*' -and
                $LlmMetrics.error_class -eq 'WorktreeError' -and
                $LlmMetrics.diag_tail -like '*WorktreeError*'
            }
    }
}

# ---------------------------------------------------------------------------
# Invoke-Sandcastle internals (#572) -- previously only tested via the daemon
# matrix wholesale-mock. Cover env save/restore, stale-file cleanup,
# json-parse-error, exit-vs-no-result paths directly.
# ---------------------------------------------------------------------------

Describe 'Invoke-Sandcastle' {
    BeforeEach {
        $script:tmpRoot = Join-Path $env:TEMP "sandcastle-inv-$([guid]::NewGuid())"
        New-Item -ItemType Directory -Path $script:tmpRoot | Out-Null
        $script:resultFile = Join-Path $script:tmpRoot 'result.json'
        $script:logFile    = Join-Path $script:tmpRoot 'run.log'
    }
    AfterEach {
        if (Test-Path -LiteralPath $script:tmpRoot) {
            Remove-Item -LiteralPath $script:tmpRoot -Recurse -Force -ErrorAction SilentlyContinue
        }
    }

    It 'removes a stale result.json before invoking npm' {
        '{"stale":true}' | Out-File -FilePath $script:resultFile -Encoding utf8
        # npm exits cleanly but writes nothing -> ok=false, reason=no-result-file,
        # AND the stale file must be gone (proving the pre-clean ran).
        Mock Invoke-NpmSandcastle { [pscustomobject]@{ cmdNotFound = $false; exitCode = 0 } }
        $r = Invoke-Sandcastle -RepoRoot $script:tmpRoot -Model 'm' -MaxIterations 1 `
            -ResultFile $script:resultFile -LogFile $script:logFile -RunId 'run1' -TargetIssue ''
        $r.ok     | Should Be $false
        $r.reason | Should Be 'no-result-file'
        Test-Path -LiteralPath $script:resultFile | Should Be $false
    }

    It 'restores env vars after the call (save-and-restore symmetry)' {
        $env:SANDCASTLE_RESULT_FILE      = 'prev-result'
        $env:SANDCASTLE_MAX_ITERATIONS   = 'prev-max'
        $env:SANDCASTLE_RUN_ID           = 'prev-run'
        $env:SANDCASTLE_AGENT_MODEL      = 'prev-model'
        $env:SANDCASTLE_AGENT_BASE_URL   = 'prev-url'
        $env:SANDCASTLE_AGENT_AUTH_TOKEN = 'prev-token'
        $env:SANDCASTLE_AGENT_AUTH_MODE  = 'prev-mode'
        $env:SANDCASTLE_AGENT_EFFORT     = 'prev-effort'
        $env:SANDCASTLE_TARGET_ISSUE     = 'prev-target'
        $env:OLLAMA_MODEL                = 'prev-ollama'
        Mock Invoke-NpmSandcastle { [pscustomobject]@{ cmdNotFound = $false; exitCode = 0 } }
        Invoke-Sandcastle -RepoRoot $script:tmpRoot -Model 'new-m' -MaxIterations 5 `
            -ResultFile $script:resultFile -LogFile $script:logFile -RunId 'new-run' `
            -BaseUrl 'http://new' -AuthToken 'new-tok' -AuthMode 'subscription' `
            -Effort 'max' -TargetIssue '42' | Out-Null
        $env:SANDCASTLE_RESULT_FILE      | Should Be 'prev-result'
        $env:SANDCASTLE_MAX_ITERATIONS   | Should Be 'prev-max'
        $env:SANDCASTLE_RUN_ID           | Should Be 'prev-run'
        $env:SANDCASTLE_AGENT_MODEL      | Should Be 'prev-model'
        $env:SANDCASTLE_AGENT_BASE_URL   | Should Be 'prev-url'
        $env:SANDCASTLE_AGENT_AUTH_TOKEN | Should Be 'prev-token'
        $env:SANDCASTLE_AGENT_AUTH_MODE  | Should Be 'prev-mode'
        $env:SANDCASTLE_AGENT_EFFORT     | Should Be 'prev-effort'
        $env:SANDCASTLE_TARGET_ISSUE     | Should Be 'prev-target'
        $env:OLLAMA_MODEL                | Should Be 'prev-ollama'
    }

    It 'injects AUTH_MODE=subscription + EFFORT during a subscription-tier call' {
        # The real-money guard inverted: when -AuthMode subscription is passed,
        # main.mts must see SANDCASTLE_AGENT_AUTH_MODE=subscription and the
        # effort value -- assert via Invoke-NpmSandcastle observing live env.
        $script:seen = $null
        Mock Invoke-NpmSandcastle {
            $script:seen = @{
                Mode   = $env:SANDCASTLE_AGENT_AUTH_MODE
                Effort = $env:SANDCASTLE_AGENT_EFFORT
                Model  = $env:SANDCASTLE_AGENT_MODEL
            }
            [pscustomobject]@{ cmdNotFound = $false; exitCode = 0 }
        }
        Invoke-Sandcastle -RepoRoot $script:tmpRoot -Model 'claude-opus-4-8' -MaxIterations 1 `
            -ResultFile $script:resultFile -LogFile $script:logFile -RunId 'sub' `
            -AuthMode 'subscription' -Effort 'medium' -TargetIssue '' | Out-Null
        $script:seen.Mode   | Should Be 'subscription'
        $script:seen.Effort | Should Be 'medium'
        $script:seen.Model  | Should Be 'claude-opus-4-8'
    }

    It 'leaves SANDCASTLE_AGENT_EFFORT unset in the child when -Effort is omitted' {
        # A prior run's value must not leak: omitting -Effort means the child
        # process sees no effort override, not a stale carry-over from $env.
        $env:SANDCASTLE_AGENT_EFFORT = 'stale-leak'
        $script:seenEffort = 'sentinel'
        Mock Invoke-NpmSandcastle {
            $script:seenEffort = $env:SANDCASTLE_AGENT_EFFORT
            [pscustomobject]@{ cmdNotFound = $false; exitCode = 0 }
        }
        Invoke-Sandcastle -RepoRoot $script:tmpRoot -Model 'qwen-large' -MaxIterations 1 `
            -ResultFile $script:resultFile -LogFile $script:logFile -RunId 'no-eff' `
            -BaseUrl 'http://ollama' -TargetIssue '' | Out-Null
        $script:seenEffort | Should BeNullOrEmpty
        # And the prior host value is restored afterwards (save/restore symmetry).
        $env:SANDCASTLE_AGENT_EFFORT | Should Be 'stale-leak'
    }

    It 'defaults AUTH_MODE to endpoint (real-money guard) when -AuthMode is omitted' {
        # Any Ollama/DeepSeek caller that does NOT pass -AuthMode must be pinned
        # to endpoint so the presence of CLAUDE_CODE_OAUTH_TOKEN in .env cannot
        # silently bill the subscription credit.
        $script:seenMode = $null
        Mock Invoke-NpmSandcastle {
            $script:seenMode = $env:SANDCASTLE_AGENT_AUTH_MODE
            [pscustomobject]@{ cmdNotFound = $false; exitCode = 0 }
        }
        Invoke-Sandcastle -RepoRoot $script:tmpRoot -Model 'qwen-large' -MaxIterations 1 `
            -ResultFile $script:resultFile -LogFile $script:logFile -RunId 'ep' `
            -BaseUrl 'http://ollama' -TargetIssue '' | Out-Null
        $script:seenMode | Should Be 'endpoint'
    }

    It 'returns json-parse-error when result.json is malformed' {
        Mock Invoke-NpmSandcastle {
            # Simulate npm writing a bad result.json before exiting 0.
            'not json {' | Out-File -FilePath $script:resultFile -Encoding utf8
            [pscustomobject]@{ cmdNotFound = $false; exitCode = 0 }
        }
        $r = Invoke-Sandcastle -RepoRoot $script:tmpRoot -Model 'm' -MaxIterations 1 `
            -ResultFile $script:resultFile -LogFile $script:logFile -RunId 'run1' -TargetIssue ''
        $r.ok     | Should Be $false
        $r.reason | Should Match '^json-parse-error'
    }

    It 'distinguishes exit!=0 (reason=exit=N) from missing result file (reason=no-result-file)' {
        Mock Invoke-NpmSandcastle { [pscustomobject]@{ cmdNotFound = $false; exitCode = 137 } }
        $r1 = Invoke-Sandcastle -RepoRoot $script:tmpRoot -Model 'm' -MaxIterations 1 `
            -ResultFile $script:resultFile -LogFile $script:logFile -RunId 'r1' -TargetIssue ''
        $r1.ok       | Should Be $false
        $r1.exitCode | Should Be 137
        $r1.reason   | Should Be 'exit=137'

        Mock Invoke-NpmSandcastle { [pscustomobject]@{ cmdNotFound = $false; exitCode = 0 } }
        $r2 = Invoke-Sandcastle -RepoRoot $script:tmpRoot -Model 'm' -MaxIterations 1 `
            -ResultFile $script:resultFile -LogFile $script:logFile -RunId 'r2' -TargetIssue ''
        $r2.ok     | Should Be $false
        $r2.reason | Should Be 'no-result-file'
    }

    It 'returns npm-not-found when npm is absent' {
        Mock Invoke-NpmSandcastle { [pscustomobject]@{ cmdNotFound = $true; exitCode = -1 } }
        $r = Invoke-Sandcastle -RepoRoot $script:tmpRoot -Model 'm' -MaxIterations 1 `
            -ResultFile $script:resultFile -LogFile $script:logFile -RunId 'r' -TargetIssue ''
        $r.ok     | Should Be $false
        $r.reason | Should Be 'npm-not-found'
    }

    It 'returns ok with parsed result on success' {
        Mock Invoke-NpmSandcastle {
            '{"branch":"feat/1-x","commits":[],"iterations":[]}' | Out-File -FilePath $script:resultFile -Encoding utf8
            [pscustomobject]@{ cmdNotFound = $false; exitCode = 0 }
        }
        $r = Invoke-Sandcastle -RepoRoot $script:tmpRoot -Model 'm' -MaxIterations 1 `
            -ResultFile $script:resultFile -LogFile $script:logFile -RunId 'r' -TargetIssue ''
        $r.ok            | Should Be $true
        $r.result.branch | Should Be 'feat/1-x'
    }
}

# ---------------------------------------------------------------------------
# Runtime-dir retention sweep (#572). Default keeps 30 most-recent dirs.
# ---------------------------------------------------------------------------

Describe 'Invoke-RuntimeSweep' {
    BeforeEach {
        $script:rootSweep = Join-Path $env:TEMP "sandcastle-sweep-$([guid]::NewGuid())"
        New-Item -ItemType Directory -Path $script:rootSweep | Out-Null
    }
    AfterEach {
        if (Test-Path -LiteralPath $script:rootSweep) {
            Remove-Item -LiteralPath $script:rootSweep -Recurse -Force -ErrorAction SilentlyContinue
        }
    }

    function New-StampDir([string]$Stamp) {
        $d = Join-Path $script:rootSweep $Stamp
        New-Item -ItemType Directory -Path $d | Out-Null
        return $d
    }

    It 'returns empty array when runtime root does not exist' {
        Invoke-RuntimeSweep -RuntimeRoot (Join-Path $script:rootSweep 'nope') -Keep 3 | Should BeNullOrEmpty
    }

    It 'returns empty array when count <= Keep' {
        New-StampDir '20260101-000000' | Out-Null
        New-StampDir '20260102-000000' | Out-Null
        Invoke-RuntimeSweep -RuntimeRoot $script:rootSweep -Keep 5 | Should BeNullOrEmpty
        (Get-ChildItem -LiteralPath $script:rootSweep -Directory).Count | Should Be 2
    }

    It 'prunes oldest dirs and keeps the N most recent (lexicographic by name)' {
        $stamps = @(
            '20260101-000000','20260102-000000','20260103-000000',
            '20260104-000000','20260105-000000'
        )
        foreach ($s in $stamps) { New-StampDir $s | Out-Null }
        $pruned = Invoke-RuntimeSweep -RuntimeRoot $script:rootSweep -Keep 2
        $pruned.Count | Should Be 3
        ($pruned -contains '20260101-000000') | Should Be $true
        ($pruned -contains '20260102-000000') | Should Be $true
        ($pruned -contains '20260103-000000') | Should Be $true
        $kept = Get-ChildItem -LiteralPath $script:rootSweep -Directory | Select-Object -ExpandProperty Name
        ($kept -contains '20260104-000000') | Should Be $true
        ($kept -contains '20260105-000000') | Should Be $true
        $kept.Count | Should Be 2
    }

    It 'disables sweep when Keep is negative' {
        New-StampDir '20260101-000000' | Out-Null
        New-StampDir '20260102-000000' | Out-Null
        Invoke-RuntimeSweep -RuntimeRoot $script:rootSweep -Keep -1 | Should BeNullOrEmpty
        (Get-ChildItem -LiteralPath $script:rootSweep -Directory).Count | Should Be 2
    }
}

# ---------------------------------------------------------------------------
# Write-OutcomeRecord HTTP path (#572). Asserts URL, headers, and body shape
# against a mocked Invoke-RestMethod so endpoint/auth regressions are caught
# in CI rather than at the first AFK run.
# ---------------------------------------------------------------------------

Describe 'Write-OutcomeRecord HTTP path' {
    It 'posts to <SupabaseUrl>/rest/v1/task_outcomes with apikey + Bearer headers and required body keys' {
        $script:captured = $null
        Mock Invoke-RestMethod {
            $script:captured = @{
                Uri     = $Uri
                Method  = $Method
                Headers = $Headers
                Body    = ($Body | ConvertFrom-Json)
            }
            return [pscustomobject]@{ id = 'rec-1' }
        }

        $r = Write-OutcomeRecord -SupabaseUrl 'https://example.supabase.co/' `
            -SupabaseKey 'sk-test' -Repo 'jarvis' -Status 'success' `
            -Summary 'all green' `
            -LlmMetrics @{ input_tokens = 10; output_tokens = 20; model = 'm' } `
            -RunId 'run-xyz'

        Assert-MockCalled Invoke-RestMethod -Times 1 -Exactly -Scope It
        $script:captured.Uri    | Should Be 'https://example.supabase.co/rest/v1/task_outcomes'
        $script:captured.Method | Should Be 'Post'
        $script:captured.Headers['apikey']        | Should Be 'sk-test'
        $script:captured.Headers['Authorization'] | Should Be 'Bearer sk-test'
        $script:captured.Headers['Content-Type']  | Should Be 'application/json'

        $body = $script:captured.Body
        $body.task_type         | Should Be 'autonomous'
        $body.outcome_status    | Should Be 'success'
        $body.project           | Should Be 'jarvis'
        $body.source_provenance | Should Be 'sandcastle:watchdog:run-xyz'
        # pattern_tags must include the baseline sandcastle/afk tags.
        ($body.pattern_tags -contains 'sandcastle') | Should Be $true
        ($body.pattern_tags -contains 'afk')        | Should Be $true
        # lessons carries serialised LLM metrics (JSON string for now, until
        # task_outcomes gains a dedicated llm jsonb column).
        $body.lessons | Should Not BeNullOrEmpty
        $lessons = $body.lessons | ConvertFrom-Json
        $lessons.input_tokens  | Should Be 10
        $lessons.output_tokens | Should Be 20
    }

    It 'short-circuits without calling Invoke-RestMethod when credentials are missing' {
        Mock Invoke-RestMethod { throw 'should not be called' }
        $r = Write-OutcomeRecord -SupabaseUrl '' -SupabaseKey '' -Repo 'jarvis' `
            -Status 'success' -Summary 's' -LlmMetrics @{} -RunId 'r'
        $r | Should BeNullOrEmpty
        Assert-MockCalled Invoke-RestMethod -Times 0 -Exactly -Scope It
    }
}

# ---------------------------------------------------------------------------
# Pytest gate for your-second-repo (#630)
# ---------------------------------------------------------------------------

Describe 'Get-RelatedTestFiles' {
    BeforeEach {
        $script:tmpRoot = Join-Path $env:TEMP "pytest-gate-test-$([guid]::NewGuid())"
        New-Item -ItemType Directory -Path $script:tmpRoot | Out-Null
        # Create a src dir with module.py
        New-Item -ItemType Directory -Path (Join-Path $script:tmpRoot 'src') | Out-Null
        New-Item -ItemType File -Path (Join-Path $script:tmpRoot 'src\module.py') | Out-Null
        New-Item -ItemType File -Path (Join-Path $script:tmpRoot 'src\test_module.py') | Out-Null
        # Create a tests dir with test_other.py
        New-Item -ItemType Directory -Path (Join-Path $script:tmpRoot 'tests') | Out-Null
        New-Item -ItemType File -Path (Join-Path $script:tmpRoot 'tests\test_other.py') | Out-Null
    }
    AfterEach {
        Remove-Item -LiteralPath $script:tmpRoot -Recurse -Force -ErrorAction SilentlyContinue
    }

    It 'finds co-located test file' {
        $files = Get-RelatedTestFiles -ChangedFiles @('src/module.py') -RepoRoot $script:tmpRoot
        $files.Count | Should Be 1
        $files[0] | Should Match 'test_module\.py$'
    }

    It 'finds test in tests/ mirror' {
        $files = Get-RelatedTestFiles -ChangedFiles @('src/other.py') -RepoRoot $script:tmpRoot
        $files.Count | Should Be 1
        $files[0] | Should Match 'test_other\.py$'
    }

    It 'returns empty array when no test files match' {
        $files = Get-RelatedTestFiles -ChangedFiles @('src/nonexistent.py') -RepoRoot $script:tmpRoot
        $files.Count | Should Be 0
    }

    It 'deduplicates when multiple files map to same test' {
        # Two files in different dirs both fall back to tests/test_module.py when
        # no colocated test exists -- the function's own Select-Object -Unique must
        # deduplicate them so the caller gets exactly one path, not two.
        Remove-Item -LiteralPath (Join-Path $script:tmpRoot 'src\test_module.py') -ErrorAction SilentlyContinue
        New-Item -ItemType Directory -Path (Join-Path $script:tmpRoot 'lib') | Out-Null
        New-Item -ItemType File     -Path (Join-Path $script:tmpRoot 'lib\module.py') | Out-Null
        New-Item -ItemType File     -Path (Join-Path $script:tmpRoot 'tests\test_module.py') | Out-Null
        # src/module.py: no src/test_module.py -> falls back to tests/test_module.py
        # lib/module.py: no lib/test_module.py -> falls back to tests/test_module.py (same path)
        $files = Get-RelatedTestFiles -ChangedFiles @('src/module.py', 'lib/module.py') -RepoRoot $script:tmpRoot
        $files.Count | Should Be 1
    }
}

Describe 'Stop-AgentPR' {
    It 'returns false when branch is empty' {
        Stop-AgentPR -Branch '' -RepoSlug 'x/y' | Should Be $false
    }

    It 'returns false when repo slug is empty' {
        Stop-AgentPR -Branch 'feat/test' -RepoSlug '' | Should Be $false
    }

    It 'returns false when no PR exists for branch (Invoke-Gh returns null)' {
        # C2: mock Invoke-Gh wrapper instead of native gh exe (Pester 3.4 cannot intercept native exes)
        Mock Invoke-Gh { $null }
        Stop-AgentPR -Branch 'feat/test' -RepoSlug 'x/y' | Should Be $false
    }

    It 'calls gh pr close when PR exists' {
        $script:ghCalls = @()
        # C2: mock Invoke-Gh; use comma-prefix to capture each call's args as a sub-array
        Mock Invoke-Gh {
            $script:ghCalls += ,$args
            if ($args[0] -eq 'pr' -and $args[1] -eq 'list') {
                $global:LASTEXITCODE = 0
                return '42'
            }
            $global:LASTEXITCODE = 0
            return $null
        }
        $result = Stop-AgentPR -Branch 'feat/630-test' -RepoSlug 'your-username/your-second-repo'
        $result | Should Be $true
        # Verify close was called (each sub-array is one call's positional args)
        $closeCall = $script:ghCalls | Where-Object { $_[0] -eq 'pr' -and $_[1] -eq 'close' }
        $closeCall | Should Not BeNullOrEmpty
    }
}

Describe 'Invoke-PytestGate' {
    BeforeEach {
        $script:tmpRoot = Join-Path $env:TEMP "pytest-invoke-$([guid]::NewGuid())"
        New-Item -ItemType Directory -Path $script:tmpRoot | Out-Null
        New-Item -ItemType Directory -Path (Join-Path $script:tmpRoot 'src') | Out-Null
        New-Item -ItemType File -Path (Join-Path $script:tmpRoot 'src\module.py') | Out-Null
        Mock Write-Host { }  # suppress output
    }
    AfterEach {
        Remove-Item -LiteralPath $script:tmpRoot -Recurse -Force -ErrorAction SilentlyContinue
    }

    It 'AC: green path -- pytest exits 0 -> passed=true' {
        Mock Push-Location { }
        Mock Pop-Location { }
        # B4: set LASTEXITCODE so git diff appears to succeed
        Mock git { $global:LASTEXITCODE = 0; 'src/module.py' }
        # Return test file exists
        New-Item -ItemType File -Path (Join-Path $script:tmpRoot 'src\test_module.py') | Out-Null
        # B4: set LASTEXITCODE = 0 (green)
        Mock pytest { $global:LASTEXITCODE = 0; '1 passed' }
        # B3: filter on LiteralPath (callers use -LiteralPath, not -Path)
        Mock Test-Path { $true } -ParameterFilter { $LiteralPath -like '*test_module*' }

        $r = Invoke-PytestGate -RepoRoot $script:tmpRoot -Branch 'feat/test'

        $r.passed          | Should Be $true
        $r.collectionError | Should Be $false
    }

    It 'AC: red path -- pytest exits 1 -> passed=false' {
        Mock Push-Location { }
        Mock Pop-Location { }
        # B4: git diff succeeds, pytest exits 1
        Mock git { $global:LASTEXITCODE = 0; 'src/module.py' }
        Mock Test-Path { $false }
        # M5: return real pytest-formatted output so Select-String.Line works on MatchInfo,
        # not on a bare string whose .Line is always $null.
        Mock pytest { $global:LASTEXITCODE = 1; @('FAILED test_module.py::test_something', '1 failed in 0.1s') }

        $r = Invoke-PytestGate -RepoRoot $script:tmpRoot -Branch 'feat/test'

        $r.passed          | Should Be $false
        $r.collectionError | Should Be $false
        $r.failures.Count  | Should Be 1
    }

    It 'AC: collection error -- pytest exits 2 -> collectionError=true' {
        Mock Push-Location { }
        Mock Pop-Location { }
        # B4: git diff succeeds, pytest exits 2 (collection error)
        Mock git { $global:LASTEXITCODE = 0; 'src/module.py' }
        Mock Test-Path { $false }
        Mock pytest { $global:LASTEXITCODE = 2; 'ERROR: could not collect test files' }

        $r = Invoke-PytestGate -RepoRoot $script:tmpRoot -Branch 'feat/test'

        $r.passed          | Should Be $false
        $r.collectionError | Should Be $true
    }

    It 'returns collection error when repo root does not exist' {
        $r = Invoke-PytestGate -RepoRoot "Nope:\missing" -Branch 'feat/test'

        $r.passed          | Should Be $false
        $r.collectionError | Should Be $true
        $r.summary         | Should Match 'not found'
    }

    It 'falls back to full suite when git diff fails (base branch missing)' {
        Mock Push-Location { }
        Mock Pop-Location { }
        # B4: git exits non-zero (diff fails), pytest exits 0 (full suite green)
        Mock git { $global:LASTEXITCODE = 1; $null }
        Mock pytest { $global:LASTEXITCODE = 0; '42 passed' }

        $r = Invoke-PytestGate -RepoRoot $script:tmpRoot -Branch 'feat/test'

        $r.passed  | Should Be $true
        $r.testsRun | Should Be 42
    }
}

Describe 'Invoke-Watchdog pytest gate integration (your-second-repo)' {
    BeforeEach {
        Mock Test-DockerRunning { $true }
        Mock Test-OllamaRunning { $true }
        Mock Start-DockerDesktop { }
        Mock Start-OllamaServer  { }
        Mock Get-RepoRoot { $env:TEMP }
        Mock Read-DotEnvFile { @{ SUPABASE_URL = 'https://x'; SUPABASE_KEY = 'k'; TELEGRAM_BOT_TOKEN = 't'; TELEGRAM_CHAT_ID = '1' } }
        Mock New-RuntimeDir { Join-Path $env:TEMP "sandcastle-pytest-int-$([guid]::NewGuid())" }
        Mock Invoke-RuntimeSweep { @() }
        Mock Write-OutcomeRecord { 'mocked' }
        Mock Send-TelegramAlert  { 'mocked' }
        Mock Add-IssueLabel      { $true }
        Mock Get-IssueLabels     { @() }
        # M4: renamed Close-AgentPR -> Stop-AgentPR
        Mock Stop-AgentPR        { $true }
        Mock Invoke-PytestGate   { $null }  # overridden per test
        # M6: mock decision memory write so tests don't hit Supabase
        Mock Write-SandcastleDecisionMemory { }
        # C2: Invoke-Gh used for gh issue edit in watchdog; mock to no-op
        Mock Invoke-Gh { }
    }

    It 'AC: your-second-repo green gate -> outcome=success, gates summary includes pytest' {
        Mock Invoke-Sandcastle {
            [pscustomobject]@{
                ok = $true; exitCode = 0; reason = $null
                result = [pscustomobject]@{
                    branch = 'feat/630-pytest'; commits = @(); iterations = @()
                }
            }
        }
        Mock Test-IsOOM { $false }
        Mock Invoke-PytestGate {
            [pscustomobject]@{
                passed = $true; collectionError = $false; summary = '5 passed'; testsRun = 5
            }
        }

        Invoke-Watchdog -Repo 'your-second-repo' -MaxIterations 1 -Model 'm' `
            -WindowEnd '' -DockerTimeoutSec 5 -OllamaTimeoutSec 5

        Assert-MockCalled Invoke-PytestGate -Times 1 -Exactly -Scope It
        Assert-MockCalled Write-OutcomeRecord -Times 1 -Exactly -Scope It `
            -ParameterFilter { $Status -eq 'success' }
        # M4: renamed to Stop-AgentPR
        Assert-MockCalled Stop-AgentPR -Times 0 -Exactly -Scope It
        # M6: no decision memory write on green gate
        Assert-MockCalled Write-SandcastleDecisionMemory -Times 0 -Exactly -Scope It
    }

    It 'AC: your-second-repo red gate -> outcome=partial, PR closed, tests-failing label' {
        Mock Invoke-Sandcastle {
            [pscustomobject]@{
                ok = $true; exitCode = 0; reason = $null
                result = [pscustomobject]@{
                    branch = 'feat/630-fail'; commits = @(); iterations = @()
                }
            }
        }
        Mock Test-IsOOM { $false }
        Mock Invoke-PytestGate {
            [pscustomobject]@{
                passed = $false; collectionError = $false; summary = '2 total, 1 failed: test_foo'; testsRun = 2; failures = @('test_foo')
            }
        }

        Invoke-Watchdog -Repo 'your-second-repo' -MaxIterations 1 -Model 'm' `
            -WindowEnd '' -DockerTimeoutSec 5 -OllamaTimeoutSec 5

        Assert-MockCalled Invoke-PytestGate -Times 1 -Exactly -Scope It
        # M4: renamed to Stop-AgentPR
        Assert-MockCalled Stop-AgentPR -Times 1 -Exactly -Scope It
        Assert-MockCalled Add-IssueLabel -Times 1 -Exactly -Scope It `
            -ParameterFilter { $Label -eq 'tests-failing' }
        Assert-MockCalled Write-OutcomeRecord -Times 1 -Exactly -Scope It `
            -ParameterFilter { $Status -eq 'partial' -and $Summary -like '*pytest-gate:tests-failing*' }
        # M6: decision memory must be written on gate failure
        Assert-MockCalled Write-SandcastleDecisionMemory -Times 1 -Exactly -Scope It
    }

    It 'AC: your-second-repo collection error -> outcome=partial, tests-broken label' {
        Mock Invoke-Sandcastle {
            [pscustomobject]@{
                ok = $true; exitCode = 0; reason = $null
                result = [pscustomobject]@{
                    branch = 'feat/630-broken'; commits = @(); iterations = @()
                }
            }
        }
        Mock Test-IsOOM { $false }
        Mock Invoke-PytestGate {
            [pscustomobject]@{
                passed = $false; collectionError = $true; summary = 'pytest collection error (exit=2)'; testsRun = 0
            }
        }

        Invoke-Watchdog -Repo 'your-second-repo' -MaxIterations 1 -Model 'm' `
            -WindowEnd '' -DockerTimeoutSec 5 -OllamaTimeoutSec 5

        Assert-MockCalled Add-IssueLabel -Times 1 -Exactly -Scope It `
            -ParameterFilter { $Label -eq 'tests-broken' }
        Assert-MockCalled Write-OutcomeRecord -Times 1 -Exactly -Scope It `
            -ParameterFilter { $Status -eq 'partial' -and $Summary -like '*pytest-gate:tests-broken*' }
        # M6: decision memory must be written on gate failure
        Assert-MockCalled Write-SandcastleDecisionMemory -Times 1 -Exactly -Scope It
    }

    It 'AC: jarvis repo skips pytest gate entirely' {
        Mock Invoke-Sandcastle {
            [pscustomobject]@{
                ok = $true; exitCode = 0; reason = $null
                result = [pscustomobject]@{
                    branch = 'feat/630-jarvis'; commits = @(); iterations = @()
                }
            }
        }
        Mock Test-IsOOM { $false }

        Invoke-Watchdog -Repo 'jarvis' -MaxIterations 1 -Model 'm' `
            -WindowEnd '' -DockerTimeoutSec 5 -OllamaTimeoutSec 5

        Assert-MockCalled Invoke-PytestGate -Times 0 -Exactly -Scope It
        Assert-MockCalled Write-OutcomeRecord -Times 1 -Exactly -Scope It `
            -ParameterFilter { $Status -eq 'success' }
    }
}
