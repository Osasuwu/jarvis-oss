# Local Ollama benchmark — per-model cold/warm latency + sustained tok/s.
# Baselines used: Main PC (RTX 3050 6GB) 2026-05-10; Workshop PC (RTX 5080 16GB) 2026-05-13 (#538).
#
# For each model:
#   1. Unload (POST /api/generate with keep_alive=0)
#   2. Cold call → measure total + load_duration
#   3. Warm call → measure total + tok/s (eval_count / eval_duration)
#
# Output: console table + (optional) JSON file via -OutJson.

param(
    [string[]]$Models = @('qwen3:4b', 'qwen2.5-coder:7b', 'qwen3:8b'),
    [string]$Prompt = "List 5 differences between TCP and UDP. Respond as a numbered list, no preamble.",
    [int]$NumPredict = 200,
    [string]$OutJson
)

$ErrorActionPreference = 'Stop'
$base = 'http://localhost:11434'
$opts = @{ temperature = 0; num_predict = $NumPredict }
$prompt = $Prompt
$models = $Models

function Invoke-Gen($model, $keepAlive) {
    $body = @{
        model      = $model
        prompt     = $prompt
        stream     = $false
        think      = $false
        keep_alive = $keepAlive
        options    = $opts
    } | ConvertTo-Json -Depth 5

    $sw = [System.Diagnostics.Stopwatch]::StartNew()
    $resp = Invoke-RestMethod -Uri "$base/api/generate" -Method Post -Body $body -ContentType 'application/json' -TimeoutSec 600
    $sw.Stop()

    [pscustomobject]@{
        wall_s        = [math]::Round($sw.Elapsed.TotalSeconds, 2)
        load_s        = if ($resp.load_duration) { [math]::Round($resp.load_duration / 1e9, 2) } else { 0 }
        prompt_eval_s = if ($resp.prompt_eval_duration) { [math]::Round($resp.prompt_eval_duration / 1e9, 2) } else { 0 }
        eval_s        = if ($resp.eval_duration) { [math]::Round($resp.eval_duration / 1e9, 2) } else { 0 }
        eval_tokens   = $resp.eval_count
        tok_per_s     = if ($resp.eval_duration -and $resp.eval_count) {
            [math]::Round($resp.eval_count / ($resp.eval_duration / 1e9), 1)
        } else { 0 }
    }
}

function Unload($model) {
    $body = @{ model = $model; keep_alive = 0; prompt = ''; stream = $false } | ConvertTo-Json
    try {
        Invoke-RestMethod -Uri "$base/api/generate" -Method Post -Body $body -ContentType 'application/json' -TimeoutSec 60 | Out-Null
    } catch { }
    Start-Sleep -Seconds 2
}

$results = @()
foreach ($m in $models) {
    Write-Host "`n=== $m ===" -ForegroundColor Cyan

    Write-Host "  unloading..."
    Unload $m

    Write-Host "  cold call..."
    $cold = Invoke-Gen $m '5m'
    Write-Host ("    wall={0}s  load={1}s  prompt_eval={2}s  gen={3}s  tokens={4}  tok/s={5}" -f `
        $cold.wall_s, $cold.load_s, $cold.prompt_eval_s, $cold.eval_s, $cold.eval_tokens, $cold.tok_per_s)

    Write-Host "  warm call..."
    $warm = Invoke-Gen $m '5m'
    Write-Host ("    wall={0}s  load={1}s  prompt_eval={2}s  gen={3}s  tokens={4}  tok/s={5}" -f `
        $warm.wall_s, $warm.load_s, $warm.prompt_eval_s, $warm.eval_s, $warm.eval_tokens, $warm.tok_per_s)

    $results += [pscustomobject]@{
        model       = $m
        cold_wall_s = $cold.wall_s
        cold_load_s = $cold.load_s
        warm_wall_s = $warm.wall_s
        warm_tok_s  = $warm.tok_per_s
        warm_tokens = $warm.eval_tokens
    }
}

Write-Host "`n=== SUMMARY ===" -ForegroundColor Green
$results | Format-Table -AutoSize

# GPU utilization snapshot at end
Write-Host "`n=== GPU AFTER ===" -ForegroundColor Green
nvidia-smi --query-gpu=name,memory.used,memory.total,utilization.gpu --format=csv
