# nova-bash-probe.ps1 —— bash 引擎 + 事件流探针（无模型，零输入）
#
# 直接经 RPC 调用户 bash 工具（与 LLM bash 工具同一引擎），命令带 3s
# sleep 制造运行窗口：看 user_tool 进度帧是随时间摊开（健康）还是
# 挤在末尾/干脆不来（卡）。
#
# 用法：powershell -ExecutionPolicy Bypass -File nova-bash-probe.ps1
$ErrorActionPreference = 'Stop'
Write-Host "nova-bash-probe v4"

$server = Join-Path $HOME '.nova\agent\install\current\runtime\nova-server.exe'
Write-Host "server: $server"

$psi = [System.Diagnostics.ProcessStartInfo]::new()
$psi.FileName = $server
$psi.UseShellExecute = $false
$psi.RedirectStandardInput = $true
$psi.RedirectStandardOutput = $true
$psi.RedirectStandardError = $true
$psi.CreateNoWindow = $true
$proc = [System.Diagnostics.Process]::Start($psi)

$script:seq = 0
function Send-Rpc([string]$method, [hashtable]$params) {
    $script:seq += 1
    $frame = @{ jsonrpc = '2.0'; id = $script:seq; method = $method; params = $params } | ConvertTo-Json -Compress
    $proc.StandardInput.WriteLine($frame)
    $proc.StandardInput.Flush()
    return $script:seq
}

# 读帧：ReadLineAsync + Wait 超时；超时不弃单（pending 任务保留续等）
$script:readTask = $null
function Read-Frame([int]$timeoutMs) {
    if ($null -eq $script:readTask) {
        $script:readTask = $proc.StandardOutput.ReadLineAsync()
    }
    if ($script:readTask.Wait($timeoutMs)) {
        $r = $script:readTask.Result
        $script:readTask = $null
        return $r
    }
    return $null
}

Send-Rpc 'initialize' @{ client = @{ name = 'bash-probe'; version = '0' } } | Out-Null
$null = Read-Frame 15000
Send-Rpc 'system/capabilities' @{ capabilities = @('notify') } | Out-Null
$null = Read-Frame 15000
Send-Rpc 'createSession' @{ cwd = $PWD.Path } | Out-Null
$null = Read-Frame 15000
Write-Host "handshake done, invoking user bash (echo PROBE_A; sleep 3; echo PROBE_B)..."

$sw = [System.Diagnostics.Stopwatch]::StartNew()
$invokeId = Send-Rpc 'invokeUserTool' @{ name = 'bash'; params = @{ command = 'echo PROBE_A; sleep 3; echo PROBE_B' } }

$rows = @()
$deadline = 60
while ($sw.Elapsed.TotalSeconds -lt $deadline) {
    $line = Read-Frame 500
    if ($null -eq $line) { continue }
    $t = [Math]::Round($sw.Elapsed.TotalSeconds, 1)
    $kind = '?'
    if ($line.Contains('"agent/event"')) { $kind = 'agent/event' }
    if ($line.Contains('"user_tool"')) { $kind = 'user_tool' }
    $isResult = $line.Contains([string]::Format('"id":{0}', $invokeId))
    if ($isResult) { $kind = 'RPC-RESULT' }
    $rows += [PSCustomObject]@{ t = $t; kind = $kind }
    if ($isResult) { break }
}

Write-Host ""
Write-Host "frame arrival table (t = seconds since invoke):"
$rows | ForEach-Object { Write-Host ("  {0,6}s  {1}" -f $_.t, $_.kind) }

$got = $false
if ($rows.Count -gt 0) { $got = [bool]($rows | Where-Object { $_.kind -eq 'RPC-RESULT' }) }
Write-Host ""
if ($got) {
    $lastT = $rows[$rows.Count - 1].t
    Write-Host "RPC result arrived at ${lastT}s -- engine completed."
    if ($lastT -lt 15) {
        Write-Host "verdict: engine + event flow healthy on Windows (fast) -- suspect the bun frontend pipe-read"
    } else {
        Write-Host "verdict: engine slow/stalled on Windows even headless -- backend engine/write direction"
    }
} else {
    Write-Host "verdict: RPC result never arrived within ${deadline}s ($($rows.Count) frames) -- backend stalled mid-invoke"
}

$proc.StandardInput.Close()
if (-not $proc.WaitForExit(5000)) { $proc.Kill() }
