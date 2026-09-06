# nova-wire-diag.ps1 —— Windows 线上读取诊断（不需要模型）
#
# 直接 spawn nova-server，发 initialize + createSession + pkgInstall
# （真实网络操作，多帧进度突发 + 长耗时——验证"零输入时帧流会不会
# 自己回来"）。pkgInstall 重装已装包是幂等无副作用的。
#
# 用法：powershell -ExecutionPolicy Bypass -File nova-wire-diag.ps1
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

# 启动后先干等 2 秒——看有没有自发帧（不该有）。
# 注意：StreamReader.Peek() 在空管道上会阻塞填充缓冲（.NET 文档陷阱），
# 探测必须用 ReadLineAsync + Wait 超时
$spontaneous = @()
$peekTask = $proc.StandardOutput.ReadLineAsync()
if ($peekTask.Wait(2000)) {
    $line = $peekTask.Result
    if ($line) { $spontaneous += $line }
}
Write-Host "spontaneous frames in first 2s: $($spontaneous.Count) (expect 0)"

$id1 = Send-Rpc 'initialize' @{ client = @{ name = 'wire-diag'; version = '0' } }
$line = $proc.StandardOutput.ReadLine()
Write-Host "initialize reply: $($line.Substring(0, [Math]::Min(160, $line.Length)))"

$id2 = Send-Rpc 'createSession' @{ cwd = $PWD.Path }
$line = $proc.StandardOutput.ReadLine()
Write-Host "createSession reply: $($line.Substring(0, [Math]::Min(120, $line.Length)))"

# 关键：发一个会产生多帧进度通知（突发）+ 长耗时的调用，然后纯干等
$id3 = Send-Rpc 'pkgInstall' @{ source = 'npm:nova-coding-agent' }
Write-Host "pkgInstall sent (burst + long op), waiting with zero input..."
$sw = [System.Diagnostics.Stopwatch]::StartNew()
$frames = 0
$got = $false
$firstFrameAt = -1.0
while ($sw.Elapsed.TotalSeconds -lt 60) {
    $readTask = $proc.StandardOutput.ReadLineAsync()
    if ($readTask.Wait(500)) {
        $line = $readTask.Result
        if ($line) {
            $frames += 1
            if ($firstFrameAt -lt 0) { $firstFrameAt = $sw.Elapsed.TotalSeconds }
            if ($line.Contains('"id":3')) { $got = $true; break }
        }
    }
}
Write-Host "frames arrived while idle: $frames (first at $([Math]::Round($firstFrameAt, 1))s)"
if ($got) {
    Write-Host "PASS: frames + response arrived with zero input (took $([Math]::Round($sw.Elapsed.TotalSeconds, 1))s) — wire reads fine"
} else {
    Write-Host "FAIL: response never arrived within 60s ($frames frames) — wire read/backend write stalls on Windows"
}

$proc.StandardInput.Close()
if (-not $proc.WaitForExit(5000)) { $proc.Kill() }
