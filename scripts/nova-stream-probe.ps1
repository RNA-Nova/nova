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
# 读帧统一走 ReadLineAsync + Wait 超时；**超时不弃单**——pending 的读
# 任务留在流上，下次接着等（.NET 的 StreamReader 同时只允许一个读操作，
# 弃单再发起会撞"流正被前一操作使用"）
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

Send-Rpc 'initialize' @{ client = @{ name = 'stream-probe'; version = '0' } } | Out-Null
$null = Read-Frame 15000
Send-Rpc 'createSession' @{ cwd = $PWD.Path } | Out-Null
$null = Read-Frame 15000
Write-Host "handshake done, sending task (echo PROBE_A && sleep 3 && echo PROBE_B)..."

$promptId = Send-Rpc 'prompt' @{ message = 'Execute this with the bash tool: echo PROBE_A && sleep 3 && echo PROBE_B . Then reply with just: done' }

# 零输入干等，记录每帧到达时刻（空读不弃单，继续等到期限）
$rows = @()
$deadline = 120
while ($sw.Elapsed.TotalSeconds -lt $deadline) {
    $line = Read-Frame 500
    if ($null -eq $line) { continue }
    $t = [Math]::Round($sw.Elapsed.TotalSeconds, 1)
    $kind = '?'
    if ($line -match '"type"\s*:\s*"([a-z_]+)"') { $kind = $Matches[1] }
    $isResult = $line.Contains([string]::Format('"id":{0}', $promptId))
    $label = $kind
    if ($isResult) { $label = 'RPC-RESULT' }
    $rows += [PSCustomObject]@{ t = $t; kind = $label }
    if ($isResult) { break }
}

Write-Host ""
Write-Host "frame arrival table (t = seconds since task sent):"
$rows | ForEach-Object { Write-Host ("  {0,6}s  {1}" -f $_.t, $_.kind) }

$bashFrames = @($rows | Where-Object { $_.kind -match 'tool' })
if ($rows.Count -gt 0 -and $bashFrames.Count -gt 0) {
    $spread = ($rows[$rows.Count - 1].t - $rows[0].t)
    Write-Host ""
    Write-Host "span: ${spread}s / frames: $($rows.Count) (tool-ish: $($bashFrames.Count))"
    if ($spread -gt 2) {
        Write-Host "verdict: frames spread out over time — backend does NOT stall on Windows; suspect the bun frontend pipe read"
    } else {
        Write-Host "verdict: frames bunched at the end — backend really stalls on Windows (engine/write-pump direction)"
    }
} else {
    Write-Host "too few frames to judge — send me the whole table above"
}

$proc.StandardInput.Close()
if (-not $proc.WaitForExit(5000)) { $proc.Kill() }
