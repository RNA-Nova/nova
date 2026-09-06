# nova-stream-probe.ps1 —— bash 流式帧的到达时刻探针（真实模型，零输入）
#
# 完整链路（冻结后端 + 真实模型 + bash 工具流式更新），只缺 bun 前端：
# 帧到达时刻摊开看——分布开 = 后端零卡顿（嫌疑归 bun 前端管道读）；
# 挤在最后 = 后端在 Windows 上真的卡住了。
#
# 用法：powershell -ExecutionPolicy Bypass -File nova-stream-probe.ps1
$ErrorActionPreference = 'Stop'

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

$sw = [System.Diagnostics.Stopwatch]::StartNew()
function Read-Frame {
    $task = $proc.StandardOutput.ReadLineAsync()
    if ($task.Wait(30000)) { return $task.Result }
    return $null
}

Send-Rpc 'initialize' @{ client = @{ name = 'stream-probe'; version = '0' } } | Out-Null
$null = Read-Frame
Send-Rpc 'createSession' @{ cwd = $PWD.Path } | Out-Null
$null = Read-Frame
Write-Host "握手完成，发起任务（echo A && sleep 3 && echo B）..."

$promptId = Send-Rpc 'prompt' @{ message = '用 bash 工具执行这个命令：echo PROBE_A && sleep 3 && echo PROBE_B。然后只回复 done' }

# 零输入干等，记录每帧到达时刻
$rows = @()
$deadline = 90
while ($sw.Elapsed.TotalSeconds -lt $deadline) {
    $line = Read-Frame
    if ($null -eq $line) { break }
    $t = [Math]::Round($sw.Elapsed.TotalSeconds, 1)
    $kind = '?'
    if ($line -match '"type"\s*:\s*"([a-z_]+)"') { $kind = $Matches[1] }
    $isResult = $line.Contains([string]::Format('"id":{0}', $promptId))
    $rows += [PSCustomObject]@{ t = $t; kind = $(if ($isResult) { 'RPC-RESULT' } else { $kind }) }
    if ($isResult) { break }
}

Write-Host ""
Write-Host "帧到达时刻表（t=秒，自任务发起）："
$rows | ForEach-Object { Write-Host ("  {0,6}s  {1}" -f $_.t, $_.kind) }

$bashFrames = $rows | Where-Object { $_.kind -match 'tool' }
if ($rows.Count -gt 0 -and $bashFrames.Count -gt 0) {
    $spread = ($rows[-1].t - $rows[0].t)
    Write-Host ""
    Write-Host "总跨度: ${spread}s / 帧数: $($rows.Count)（tool 相关 $($bashFrames.Count) 帧）"
    if ($spread -gt 2) {
        Write-Host "结论：帧流时间分布开——后端在 Windows 上零卡顿，嫌疑归前端（bun 管道读层）"
    } else {
        Write-Host "结论：帧挤在一小段——后端在 Windows 上卡住了（引擎/写泵方向）"
    }
} else {
    Write-Host "帧数过少，无法判定——把上面整张表发给我"
}

$proc.StandardInput.Close()
if (-not $proc.WaitForExit(5000)) { $proc.Kill() }
