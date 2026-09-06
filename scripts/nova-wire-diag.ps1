# nova-wire-diag.ps1 —— Windows 线上读取诊断（不需要模型）
#
# 直接 spawn nova-server，发 initialize + createSession + pkgCheckUpdates
# （真实网络操作，耗时数秒——用来验证"零输入时响应会不会自己回来"）。
#
# 用法：powershell -ExecutionPolicy Bypass -File nova-wire-diag.ps1
$ErrorActionPreference = 'Stop'

$server = Join-Path $HOME '.nova\agent\install\current\runtime\nova-server.exe'
if (-not (Test-Path $server)) { $server = Join-Path $HOME '.nova\agent\install\current\runtime\nova-server.exe' }
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

# 启动后先干等 2 秒——看有没有自发帧（不该有）
$spontaneous = @()
$sw = [System.Diagnostics.Stopwatch]::StartNew()
while ($sw.ElapsedMilliseconds -lt 2000) {
    if ($proc.StandardOutput.Peek() -ge 0) {
        $line = $proc.StandardOutput.ReadLine()
        if ($line) { $spontaneous += $line }
    }
    Start-Sleep -Milliseconds 50
}
Write-Host "启动 2s 内的自发帧数: $($spontaneous.Count)（期望 0）"

$id1 = Send-Rpc 'initialize' @{ client = @{ name = 'wire-diag'; version = '0' } }
$line = $proc.StandardOutput.ReadLine()
Write-Host "initialize 应答: $($line.Substring(0, [Math]::Min(160, $line.Length)))"

$id2 = Send-Rpc 'createSession' @{ cwd = $PWD.Path }
$line = $proc.StandardOutput.ReadLine()
Write-Host "createSession 应答: $($line.Substring(0, [Math]::Min(120, $line.Length)))"

# 关键：发一个会产生多帧进度通知（突发）+ 长耗时的调用，然后纯干等——
# 不碰任何输入。pkgInstall 重装已装包是幂等无副作用的。
$id3 = Send-Rpc 'pkgInstall' @{ source = 'npm:nova-coding-agent' }
Write-Host "已发 pkgInstall（多帧进度 + 长耗时），干等帧自行到达..."
$sw = [System.Diagnostics.Stopwatch]::StartNew()
$frames = 0
$got = $false
$firstFrameAt = $null
while ($sw.Elapsed.TotalSeconds -lt 60) {
    $readTask = $proc.StandardOutput.ReadLineAsync()
    if ($readTask.Wait(500)) {
        $line = $readTask.Result
        if ($line) {
            $frames += 1
            if ($null -eq $firstFrameAt) { $firstFrameAt = $sw.Elapsed.TotalSeconds }
            if ($line.Contains('"id":3')) { $got = $true; break }
        }
    }
}
Write-Host "干等期间到达帧数: $frames（首帧于 $([Math]::Round([double]($firstFrameAt ?? -1), 1))s）"
if ($got) {
    Write-Host "PASS: 零输入下帧流+响应自行到达（用时 $([Math]::Round($sw.Elapsed.TotalSeconds, 1))s）——线上读取不卡"
} else {
    Write-Host "FAIL: 60s 内响应没有自行到达（到达 $frames 帧）——线上读取/后端写出在 Windows 上停滞"
}

$proc.StandardInput.Close()
if (-not $proc.WaitForExit(5000)) { $proc.Kill() }
