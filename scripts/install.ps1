# install.ps1 —— Nova Windows 安装器（install.sh 的 PowerShell 对位译本）
#
# 用法：
#   irm https://github.com/RNA-Nova/nova/releases/latest/download/install.ps1 | iex
#   卸载：iex "& { $(irm https://github.com/RNA-Nova/nova/releases/latest/download/install.ps1) } uninstall"
#   本地脚本形态：powershell -ExecutionPolicy Bypass -File install.ps1 [uninstall]
#
# 做的事：
#   1. 预检（Windows + 架构）
#   2. 解析最新发布版本（或 NOVA_VERSION 钉版）
#   3. 下载对应架构 zip + SHA256SUMS 并校验 sha256
#   4. 解压到 <安装根>/releases/<版本>/，junction 翻转 current（NTFS 目录链接，免管理员）
#   5. 装后自检（nova.exe --version 报号与目标版本一致）
#   6. 安装官方编程能力包（npm:nova-coding-agent——失败只警告不阻断）
#   7. current 目录写入用户 PATH（已在则跳过）
#
# 环境变量：NOVA_VERSION / NOVA_INSTALLER_RELEASES_BASE（支持 file:// 本地演练）/
#   NOVA_RELEASES_API_BASE / NOVA_INSTALL_DIR（缺省 ~\.nova\agent\install）/
#   NOVA_NO_CODING=1 / NOVA_OFFLINE=1

$ErrorActionPreference = 'Stop'
# Windows PowerShell 5.1 缺省 TLS 版本过旧，GitHub 直接拒连——先切 1.2
[Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12
# 脚本级参数（irm|iex 与 -File 两形态共用）——函数内 $args 是函数自己的，
# 经这里显式传递
$ScriptArgs = $args

$Repo = 'RNA-Nova/nova'
$ReleasesBase = "$env:NOVA_INSTALLER_RELEASES_BASE"
if (-not $ReleasesBase) { $ReleasesBase = "https://github.com/$Repo/releases" }

function Say([string]$msg) { Write-Host $msg }
function Err([string]$msg) { Write-Host "error: $msg" -ForegroundColor Red }

# —— 预检 ————————————————————————————————————————————————————————————

function Test-Platform {
    if ($PSVersionTable.PSEdition -eq 'Core' -and -not $IsWindows) {
        Err "本安装器是 Windows 专用（macOS/Linux 用 install.sh）"
        exit 1
    }
    $arch = $env:PROCESSOR_ARCHITECTURE
    switch ($arch) {
        'AMD64' { return 'windows-x64' }
        'ARM64' { return 'windows-arm64' }
        default { Err "不支持的 CPU 架构: $arch"; exit 1 }
    }
}

# —— 路径 ————————————————————————————————————————————————————————————

function Install-Root {
    if ($env:NOVA_INSTALL_DIR) { return $env:NOVA_INSTALL_DIR }
    return (Join-Path $HOME '.nova\agent\install')
}

function Read-CurrentVersion {
    $marker = Join-Path (Install-Root) 'current-version.txt'
    if (Test-Path $marker) { return (Get-Content $marker -Raw).Trim() }
    return $null
}

# —— 版本解析与下载 ———————————————————————————————————————————————————

function Resolve-Version {
    if ($env:NOVA_VERSION) {
        $v = $env:NOVA_VERSION
        if ($v.StartsWith('v')) { return $v }
        return "v$v"
    }
    $api = "$env:NOVA_RELEASES_API_BASE"
    if (-not $api) { $api = "https://api.github.com/repos/$Repo/releases" }
    try {
        $rel = Invoke-RestMethod -Uri "$api/latest" -Headers @{ 'User-Agent' = 'nova-installer' }
    }
    catch {
        Err "无法解析最新发布版本（可设 NOVA_VERSION 显式指定）：$_"
        exit 1
    }
    if (-not $rel.tag_name) { Err "无法解析最新发布版本（可设 NOVA_VERSION 显式指定）"; exit 1 }
    return $rel.tag_name
}

function Fetch([string]$url, [string]$dest) {
    try {
        if ($url.StartsWith('file://')) {
            $local = ([Uri]$url).LocalPath
            # Windows 的 file:///C:/... → LocalPath 是 /C:/...（前导斜杠要摘）
            if ($local -match '^/[A-Za-z]:') { $local = $local.Substring(1) }
            Copy-Item $local $dest -Force
        }
        else {
            Invoke-WebRequest -Uri $url -OutFile $dest -UseBasicParsing
        }
    }
    catch {
        if (Test-Path $dest) { Remove-Item $dest -Force }
        Err "下载失败: $url"
        exit 1
    }
}

function Test-Sha256([string]$file, [string]$sumsFile) {
    $name = Split-Path $file -Leaf
    # 同名多行取最后一笔（SHA256SUMS 是追加语义——重跑同平台最新条目在尾部）
    $line = Get-Content $sumsFile | Where-Object { $_ -match "  $([regex]::Escape($name))$" } | Select-Object -Last 1
    if (-not $line) { Err "SHA256SUMS 中没有 $name 的记录"; exit 1 }
    $expected = ($line -split '\s+')[0].ToLowerInvariant()
    $actual = (Get-FileHash $file -Algorithm SHA256).Hash.ToLowerInvariant()
    if ($expected -ne $actual) {
        Err "$name 的 sha256 校验失败——下载损坏或被篡改，未安装"
        exit 1
    }
    Say "校验: $name sha256 通过"
}

# —— 装配 ————————————————————————————————————————————————————————————

function Activate-Release([string]$root, [string]$version, [string]$releaseDir) {
    $current = Join-Path $root 'current'
    if (Test-Path $current) {
        # junction 删除：cmd rmdir 只摘链接不碰目标（PowerShell 5.1 的
        # Remove-Item -Recurse 对 junction 有递归进目标目录的坑）
        cmd /c rmdir "$current" | Out-Null
    }
    New-Item -ItemType Junction -Path $current -Target $releaseDir | Out-Null
    Set-Content -Path (Join-Path $root 'current-version.txt') -Value $version -NoNewline
}

function Install-CodingBundle([string]$root) {
    if ($env:NOVA_NO_CODING -eq '1' -or $env:NOVA_OFFLINE -eq '1') {
        Say "跳过编程能力包（NOVA_NO_CODING/NOVA_OFFLINE）——手动装：nova-server.exe pkg install npm:nova-coding-agent"
        return
    }
    $server = Join-Path $root 'current\runtime\nova-server.exe'
    Say ""
    Say "安装官方编程能力包（npm:nova-coding-agent）…"
    & $server pkg install npm:nova-coding-agent
    if ($LASTEXITCODE -ne 0) {
        Say "警告：编程能力包安装未成功（可稍后手动装）："
        Say "  & `"$server`" pkg install npm:nova-coding-agent"
        Say "  编程能力缺失时 nova 仍有会话基础设施（nova-base 内建）。"
    }
}

function Set-PathEntry([string]$root) {
    $current = Join-Path $root 'current'
    $userPath = [Environment]::GetEnvironmentVariable('Path', 'User')
    $entries = @()
    if ($userPath) { $entries = $userPath -split ';' | Where-Object { $_ } }
    if ($entries -contains $current) {
        Say "PATH: $current 已在用户 PATH"
        return
    }
    [Environment]::SetEnvironmentVariable('Path', ($entries + $current) -join ';', 'User')
    Say "PATH: 已写入用户 PATH（$current）——重开终端生效"
}

# —— 卸载 ————————————————————————————————————————————————————————————

function Do-Uninstall {
    $root = Install-Root
    $removed = $false

    # 摘 PATH 条目
    $current = Join-Path $root 'current'
    $userPath = [Environment]::GetEnvironmentVariable('Path', 'User')
    if ($userPath -and ($userPath -split ';') -contains $current) {
        $kept = ($userPath -split ';' | Where-Object { $_ -and $_ -ne $current }) -join ';'
        [Environment]::SetEnvironmentVariable('Path', $kept, 'User')
        Say "已从用户 PATH 摘除 $current"
        $removed = $true
    }

    if (Test-Path $current) { cmd /c rmdir "$current" | Out-Null }
    if (Test-Path $root) {
        Remove-Item $root -Recurse -Force
        Say "已删 $root"
        $removed = $true
    }
    if (-not $removed) { Say "未发现本安装器的安装痕迹（$root）" }
    Say ""
    Say "用户数据保留在 ~\.nova\agent（settings/sessions/packages 等）。"
    Say "如需彻底清除：Remove-Item -Recurse -Force ~\.nova\agent"
}

# —— 主流程 ———————————————————————————————————————————————————————————

function Main {
    if ($script:ScriptArgs -and $script:ScriptArgs[0] -eq 'uninstall') {
        Do-Uninstall
        exit 0
    }

    Say ""
    Write-Host "  Nova Installer" -ForegroundColor Cyan
    Write-Host "  框架 + TUI + 官方 bundle 的静态双二进制分发（Windows）" -ForegroundColor DarkGray
    Say ""

    $platform = Test-Platform
    Say "平台: $platform"

    $version = Resolve-Version
    $root = Install-Root
    $releaseDir = Join-Path $root "releases\$version"
    $asset = "nova-$platform.zip"

    $currentVersion = Read-CurrentVersion
    $novaExe = Join-Path $releaseDir 'nova.exe'
    $serverExe = Join-Path $releaseDir 'runtime\nova-server.exe'
    if ($currentVersion -eq $version -and (Test-Path $novaExe) -and (Test-Path $serverExe)) {
        Say "已存在 $version（$releaseDir）——跳过重装，直接激活"
    }
    else {
        $stage = Join-Path $root "staging\$version.$PID"
        if (Test-Path $stage) { Remove-Item $stage -Recurse -Force }
        New-Item -ItemType Directory -Path $stage -Force | Out-Null
        try {
            Say "下载: $ReleasesBase/download/$version/$asset"
            Fetch "$ReleasesBase/download/$version/$asset" (Join-Path $stage $asset)
            Fetch "$ReleasesBase/download/$version/SHA256SUMS" (Join-Path $stage 'SHA256SUMS')
            Test-Sha256 (Join-Path $stage $asset) (Join-Path $stage 'SHA256SUMS')

            New-Item -ItemType Directory -Path $releaseDir -Force | Out-Null
            Expand-Archive -Path (Join-Path $stage $asset) -DestinationPath $releaseDir -Force
            Say "解压: $releaseDir"
        }
        finally {
            if (Test-Path $stage) { Remove-Item $stage -Recurse -Force }
        }
        $stagingParent = Join-Path $root 'staging'
        if ((Test-Path $stagingParent) -and -not (Get-ChildItem $stagingParent)) {
            Remove-Item $stagingParent -Force
        }
    }

    Activate-Release $root $version $releaseDir

    # 装后自检：--version 报号必须与目标版本一致（抓到残缺/错版包）
    $reported = (& (Join-Path $root 'current\nova.exe') --version 2>$null | Out-String).Trim()
    $expected = $version.TrimStart('v')
    if ($reported -ne $expected) {
        Err "自检失败：nova --version 报 '$reported'，期望 '$expected'"
        exit 1
    }
    Say "自检: nova --version = $reported"

    Install-CodingBundle $root

    Set-PathEntry $root

    Say ""
    Say "安装完成。重开终端后运行 nova 启动（编程能力已随行——bash/edit/grep 等工具与 coding_agent 角色）。"
    Say ""
}

Main
