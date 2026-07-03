# Layer 2 probe -- "Does Ollama's Anthropic-compat shim emit a real tool_use block?"
#
# Sends /v1/messages with a tool definition and asks the model to call it.
# Pass criteria: response.stop_reason == "tool_use" AND content contains a
# block with type=="tool_use".
#
# Fail modes we are explicitly looking for (per qwen3-coder:30b post-mortem,
# memory ollama_bench_must_measure_tool_use_fidelity):
#   - markdown fence with JSON inside an assistant text block
#   - Hermes-XML <tool_call>...</tool_call> in text
#   - OpenAI-style tool_calls in choices[0].message (wrong API surface)
#   - stop_reason "end_turn" with no tool_use block (model declined)
#
# Usage: ./scripts/probe-ollama-toolcall.ps1 -Model gemma4:26b

[CmdletBinding()]
param(
    [Parameter(Mandatory)][string]$Model,
    [string]$BaseUrl = 'http://localhost:11434',
    [int]$TimeoutSec = 180
)

$ErrorActionPreference = 'Stop'

# Minimal Claude-compatible tool spec + user message that should obviously
# trigger a tool call. Keep the schema small to rule out "model got confused".
$body = @{
    model      = $Model
    max_tokens = 512
    tools      = @(
        @{
            name         = 'get_weather'
            description  = 'Get the current weather for a city.'
            input_schema = @{
                type       = 'object'
                properties = @{
                    city = @{ type = 'string'; description = 'City name' }
                }
                required   = @('city')
            }
        }
    )
    messages   = @(
        @{
            role    = 'user'
            content = 'What is the weather in Tokyo right now? Use the get_weather tool.'
        }
    )
} | ConvertTo-Json -Depth 10

Write-Host "POST $BaseUrl/v1/messages  model=$Model" -ForegroundColor Cyan
$sw = [System.Diagnostics.Stopwatch]::StartNew()
try {
    $resp = Invoke-RestMethod `
        -Uri "$BaseUrl/v1/messages" `
        -Method Post `
        -Headers @{ 'anthropic-version' = '2023-06-01' } `
        -ContentType 'application/json' `
        -Body $body `
        -TimeoutSec $TimeoutSec
} catch {
    $sw.Stop()
    Write-Host "REQUEST FAILED after $($sw.Elapsed.TotalSeconds)s: $_" -ForegroundColor Red
    exit 2
}
$sw.Stop()

Write-Host ""
Write-Host "Wall time: $([math]::Round($sw.Elapsed.TotalSeconds, 2))s" -ForegroundColor DarkGray
Write-Host "stop_reason : $($resp.stop_reason)" -ForegroundColor Yellow
Write-Host "content blocks:" -ForegroundColor Yellow
foreach ($block in $resp.content) {
    Write-Host "  - type=$($block.type)" -ForegroundColor Yellow
    if ($block.type -eq 'tool_use') {
        Write-Host "    name=$($block.name)  input=$($block.input | ConvertTo-Json -Compress)" -ForegroundColor Green
    } elseif ($block.type -eq 'text') {
        $preview = ($block.text -replace '\s+', ' ').Substring(0, [math]::Min(200, $block.text.Length))
        Write-Host "    text=`"$preview...`"" -ForegroundColor DarkYellow
    }
}

$toolUseBlock = $resp.content | Where-Object { $_.type -eq 'tool_use' } | Select-Object -First 1
$pass = ($resp.stop_reason -eq 'tool_use') -and ($null -ne $toolUseBlock)

Write-Host ""
if ($pass) {
    Write-Host "LAYER 2 PASS  ($Model)" -ForegroundColor Green
    exit 0
} else {
    Write-Host "LAYER 2 FAIL  ($Model)" -ForegroundColor Red
    Write-Host "  stop_reason expected 'tool_use', got '$($resp.stop_reason)'" -ForegroundColor Red
    if (-not $toolUseBlock) {
        # Diagnose the known failure templates so the issue body can record
        # which one this candidate emits.
        $textBlocks = $resp.content | Where-Object { $_.type -eq 'text' }
        foreach ($t in $textBlocks) {
            if ($t.text -match '```\s*(json|tool)') {
                Write-Host "  failure-mode: markdown-fence (JSON inside text block)" -ForegroundColor Red
            } elseif ($t.text -match '<tool_call>|<function|<tool>') {
                Write-Host "  failure-mode: hermes-xml (XML tags inside text block)" -ForegroundColor Red
            } elseif ($t.text -match '"tool_calls"\s*:') {
                Write-Host "  failure-mode: openai-style tool_calls passthrough" -ForegroundColor Red
            } else {
                Write-Host "  failure-mode: text-only response (no tool template detected)" -ForegroundColor Red
            }
        }
    }
    exit 1
}
