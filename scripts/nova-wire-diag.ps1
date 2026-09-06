# nova-wire-diag.ps1 -- Windows 线上读取诊断（不需要模型）
#
# 直接 spawn nova-server，发 initialize + createSession + pkgInstall
# （真实网络操作，多帧进度突发 + 长耗时--验证"零输入时帧流会不会
# 自己回来"）。pkgInstall 重装已装包是幂等无副作用的。
#
# 用法：powershell -ExecutionPolicy Bypass -File nova-wire-diag.ps1
$ErrorActionPreference = 'Stop'
Write-Host "nova-wire-diag v3"

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

# 读帧统一走 ReadLineAsync + Wait 超时；**超时不弃单**--pending 的读
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

# 启动后先干等 2 秒--看有没有自发帧（不该有）
$spontaneous = Read-Frame 2000
Write-Host "spontaneous frames in first 2s: $(if ($spontaneous) { 1 } else { 0 }) (expect 0)"

$id1 = Send-Rpc 'initialize' @{ client = @{ name = 'wire-diag'; version = '0' } }
$line = Read-Frame 15000
Write-Host "initialize reply: $(if ($line) { $line.Substring(0, [Math]::Min(160, $line.Length)) } else { '<TIMEOUT>' })"

$id2 = Send-Rpc 'createSession' @{ cwd = $PWD.Path }
$line = Read-Frame 15000
Write-Host "createSession reply: $(if ($line) { $line.Substring(0, [Math]::Min(120, $line.Length)) } else { '<TIMEOUT>' })"

# 宣告 notify 能力——否则 package_progress 进度帧被按设计丢弃（降级语义）
$idCaps = Send-Rpc 'system/capabilities' @{ capabilities = @('notify') }
$null = Read-Frame 15000

# 关键：发一个会产生多帧进度通知（突发）+ 长耗时的调用，然后纯干等
$id3 = Send-Rpc 'pkgInstall' @{ source = 'npm:nova-coding-agent' }
Write-Host "pkgInstall sent (burst + long op), waiting with zero input (up to 180s)..."
$sw = [System.Diagnostics.Stopwatch]::StartNew()
$frames = 0
$got = $false
$firstFrameAt = -1.0
while ($sw.Elapsed.TotalSeconds -lt 180) {
    $line = Read-Frame 500
    if ($line) {
        $frames += 1
        if ($firstFrameAt -lt 0) { $firstFrameAt = $sw.Elapsed.TotalSeconds }
        if ($line.Contains('"id":3')) { $got = $true; break }
    }
}
Write-Host "frames arrived while idle: $frames (first at $([Math]::Round($firstFrameAt, 1))s)"
if ($got) {
    Write-Host "PASS: frames + response arrived with zero input (took $([Math]::Round($sw.Elapsed.TotalSeconds, 1))s) -- wire reads fine"
} else {
    Write-Host "FAIL: response never arrived within 180s ($frames frames) -- wire read/backend write stalls on Windows"
}

$proc.StandardInput.Close()
if (-not $proc.WaitForExit(5000)) { $proc.Kill() }
