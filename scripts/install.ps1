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
#   6b. Git Bash 供给（bash 工具的 Windows 依赖：管理态 PortableGit 装进
#       agent 目录 + settings shell_path 指向；已有 Git Bash 直接使用）
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
    # 读机器环境注册表而非进程变量——ARM64 Windows 跑 x64 PowerShell 时
    # $env:PROCESSOR_ARCHITECTURE 是进程模拟值（x64），注册表才是机器真值
    $arch = (Get-ItemProperty "HKLM:\SYSTEM\CurrentControlSet\Control\Session Manager\Environment").PROCESSOR_ARCHITECTURE
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
            # 显式 curl.exe：Windows PowerShell 里 curl 是 Invoke-WebRequest
            # 的别名且慢得多；curl.exe 失败再回退 IWR
            curl.exe "-#SfLo" $dest $url
            if ($LASTEXITCODE -ne 0) {
                Invoke-WebRequest -Uri $url -OutFile $dest -UseBasicParsing
            }
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

# —— 用户环境变量（PATH）——————————————————————————————————————————————
# 注册表直写而不用 [Environment]::SetEnvironmentVariable——后者读出时会把
# 既有的 %VAR% 引用展开成实值再写回（REG_SZ 化），破坏用户自己的变量引用。
# 写后广播 WM_SETTINGCHANGE（新终端立即可见）+ 进程内同步更新（本安装器
# 后续步骤直接可用）。

function Get-UserEnv([string]$Key) {
    $rk = (Get-Item 'HKCU:').OpenSubKey('Environment')
    $rk.GetValue($Key, $null, [Microsoft.Win32.RegistryValueOptions]::DoNotExpandEnvironmentNames)
}

function Publish-EnvChange {
    if (-not ('Win32.EnvBroadcast' -as [Type])) {
        Add-Type -Namespace Win32 -Name EnvBroadcast -MemberDefinition @"
[DllImport("user32.dll", SetLastError = true, CharSet = CharSet.Auto)]
public static extern System.IntPtr SendMessageTimeout(
    System.IntPtr hWnd, uint Msg, System.UIntPtr wParam, string lParam,
    uint fuFlags, uint uTimeout, out System.UIntPtr lpdwResult);
"@
    }
    $result = [UIntPtr]::Zero
    [Win32.EnvBroadcast]::SendMessageTimeout([IntPtr]0xffff, 0x1a, [UIntPtr]::Zero, 'Environment', 2, 5000, [ref]$result) | Out-Null
}

function Set-UserEnv([string]$Key, [string]$Value) {
    $rk = (Get-Item 'HKCU:').OpenSubKey('Environment', $true)
    if ($null -eq $Value) {
        $rk.DeleteValue($Key, $false)
    }
    else {
        # 含 % 的值保持 ExpandString（否则 %USERPROFILE% 类引用被写死）
        $kind = [Microsoft.Win32.RegistryValueKind]::String
        if ($Value.Contains('%')) { $kind = [Microsoft.Win32.RegistryValueKind]::ExpandString }
        elseif ($rk.GetValue($Key)) { $kind = $rk.GetValueKind($Key) }
        $rk.SetValue($Key, $Value, $kind)
    }
    Publish-EnvChange
}

function Add-UserPathEntry([string]$Dir) {
    $entries = @()
    $existing = Get-UserEnv 'Path'
    if ($existing) { $entries = @($existing -split ';' | Where-Object { $_ -and $_ -ne $Dir }) }
    $entries += $Dir
    Set-UserEnv 'Path' ($entries -join ';')
    $processEntries = @($env:PATH -split ';' | Where-Object { $_ -and $_ -ne $Dir })
    $env:PATH = (($processEntries + $Dir) -join ';')
}

function Remove-UserPathEntry([string]$Dir) {
    $existing = Get-UserEnv 'Path'
    if (-not $existing) { return }
    $kept = @($existing -split ';' | Where-Object { $_ -and $_ -ne $Dir })
    Set-UserEnv 'Path' ($kept -join ';')
}

function Set-PathEntry([string]$root) {
    $current = Join-Path $root 'current'
    $existing = Get-UserEnv 'Path'
    if ($existing -and (@($existing -split ';') -contains $current)) {
        Say "PATH: $current 已在用户 PATH"
        return
    }
    Add-UserPathEntry $current
    Say "PATH: 已写入用户 PATH（$current）——新开的终端立即可用"
}

# —— Git Bash 供给（bash 工具的 Windows 依赖） ————————————————————————————
# coding_agent 的 bash 工具在 Windows 上必须有 bash（Git Bash）。没有的
# 机器在装完能力包后由这里补齐：管理态 PortableGit 装进 agent 目录 +
# settings 的 shell_path 指向它（工具链读取链：ToolContext.settings
# .get_shell_path() → shell 解析）。

$GitForWindowsLatestReleaseApi = 'https://api.github.com/repos/git-for-windows/git/releases/latest'

function Get-NovaAgentDir {
    return Join-Path $HOME '.nova\agent'
}

function Get-NovaSettingsPath {
    return Join-Path (Get-NovaAgentDir) 'settings.json'
}

function Get-ManagedGitBashDir {
    return Join-Path (Get-NovaAgentDir) 'win-git-bash'
}

function Get-ManagedGitBashPath {
    return Join-Path (Get-ManagedGitBashDir) 'bin\bash.exe'
}

function Get-SettingsShellPath {
    $p = Get-NovaSettingsPath
    if (-not (Test-Path $p -PathType Leaf)) { return '' }
    try { $s = Get-Content $p -Raw | ConvertFrom-Json } catch { return '' }
    if ($s -and ($s.PSObject.Properties.Name -contains 'shell_path')) { return [string]$s.shell_path }
    return ''
}

function Set-SettingsShellPath([string]$ShellPath) {
    $p = Get-NovaSettingsPath
    New-Item -ItemType Directory -Force -Path (Split-Path $p -Parent) | Out-Null
    if (Test-Path $p -PathType Leaf) {
        $s = Get-Content $p -Raw | ConvertFrom-Json
    }
    else {
        $s = [PSCustomObject]@{}
    }
    if ($null -eq $s) { $s = [PSCustomObject]@{} }
    $s | Add-Member -MemberType NoteProperty -Name 'shell_path' -Value $ShellPath -Force
    $json = $s | ConvertTo-Json -Depth 100
    $utf8NoBom = New-Object System.Text.UTF8Encoding $false
    [System.IO.File]::WriteAllText($p, "$json`r`n", $utf8NoBom)
}

function Find-GitBash {
    # 已配置的 shell_path 优先；已配置但文件不在了——返回空串走重装
    $configured = Get-SettingsShellPath
    if ($configured) {
        if (Test-Path $configured -PathType Leaf) { return $configured }
        return ''
    }
    $candidates = @()
    if ($env:ProgramFiles) { $candidates += Join-Path $env:ProgramFiles 'Git\bin\bash.exe' }
    if (${env:ProgramFiles(x86)}) { $candidates += Join-Path ${env:ProgramFiles(x86)} 'Git\bin\bash.exe' }
    foreach ($c in $candidates) {
        if (Test-Path $c -PathType Leaf) { return $c }
    }
    $onPath = Get-Command bash.exe -ErrorAction SilentlyContinue | Select-Object -First 1
    if ($onPath -and $onPath.Source -and (Test-Path $onPath.Source -PathType Leaf)) { return $onPath.Source }
    return ''
}

function Get-PortableGitAsset {
    $platform = Test-Platform  # windows-x64 / windows-arm64
    $assetSuffix = '64-bit.7z.exe'
    if ($platform -eq 'windows-arm64') { $assetSuffix = 'arm64.7z.exe' }

    Say "解析 Portable Git 最新发布…"
    $release = Invoke-RestMethod -Uri $GitForWindowsLatestReleaseApi -Headers @{ 'User-Agent' = 'nova-installer' }
    $asset = $release.assets | Where-Object { $_.name -like "PortableGit-*$assetSuffix" } | Select-Object -First 1
    if (-not $asset) { Err "未找到 Portable Git 资产（$assetSuffix）"; exit 1 }

    # sha256 在 release 正文的资产表格里（"文件名 | sha256" 行）
    $escaped = [regex]::Escape($asset.name)
    $m = [regex]::Match($release.body, "(?m)^$escaped\s+\|\s+([a-fA-F0-9]{64})\s*$")
    if (-not $m.Success) { Err "未找到 $($asset.name) 的 sha256 记录"; exit 1 }
    return [PSCustomObject]@{ Name = $asset.name; Url = $asset.browser_download_url; Sha256 = $m.Groups[1].Value.ToLowerInvariant() }
}

function Install-GitBashManaged {
    $gitDir = Get-ManagedGitBashDir
    $bashPath = Get-ManagedGitBashPath
    if (Test-Path $bashPath -PathType Leaf) {
        Set-SettingsShellPath $bashPath
        Say "Git Bash 已就位于 $bashPath（shell_path 已写入）"
        return
    }

    $tmp = Join-Path ([System.IO.Path]::GetTempPath()) "nova-git-bash-$PID"
    $extractDir = Join-Path $tmp 'extract'
    Remove-Item $tmp -Recurse -Force -ErrorAction SilentlyContinue
    New-Item -ItemType Directory -Force -Path $tmp, $extractDir, (Get-NovaAgentDir) | Out-Null

    $asset = Get-PortableGitAsset
    $pkg = Join-Path $tmp $asset.Name
    Say "下载 Portable Git $($asset.Name)…"
    Fetch $asset.Url $pkg
    Say "校验 sha256…"
    $actual = (Get-FileHash $pkg -Algorithm SHA256).Hash.ToLowerInvariant()
    if ($actual -ne $asset.Sha256) {
        Err "Portable Git 的 sha256 校验失败（期望 $($asset.Sha256)，实际 $actual）"
        exit 1
    }

    Say "解压到 $gitDir"
    # PortableGit-*.7z.exe 是自解压包：-y 静默 -o 指定目标
    $proc = Start-Process -FilePath $pkg -ArgumentList @('-y', "-o`"$extractDir`"") -PassThru -Wait -WindowStyle Hidden
    if ($proc.ExitCode -ne 0) { Err "Portable Git 解压失败（exit $($proc.ExitCode)）"; exit $proc.ExitCode }
    if (-not (Test-Path (Join-Path $extractDir 'bin\bash.exe') -PathType Leaf)) {
        Err "Portable Git 解压产物缺 bin\bash.exe"
        exit 1
    }
    Remove-Item $gitDir -Recurse -Force -ErrorAction SilentlyContinue
    Move-Item $extractDir $gitDir -Force
    Remove-Item $tmp -Recurse -Force -ErrorAction SilentlyContinue

    Set-SettingsShellPath $bashPath
    Say "Git Bash 已装到 $gitDir（管理态，settings 的 shell_path 已指向）"
}

function Install-GitBashWithWinget {
    Say "用 winget 全局安装 Git for Windows…"
    & winget.exe install --id Git.Git -e --source winget --accept-source-agreements --accept-package-agreements
    if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
    $found = Find-GitBash
    if ($found) {
        if ((Get-SettingsShellPath) -and -not (Test-Path (Get-SettingsShellPath) -PathType Leaf)) {
            Set-SettingsShellPath $found
        }
        Say "Git Bash 已装到 $found"
    }
    else {
        Say "Git 已装但当前终端还找不到 bash——重开终端后生效"
    }
}

function Ensure-GitBash {
    $existing = Find-GitBash
    if ($existing) {
        Say "Git Bash: $existing（bash 工具就绪）"
        return
    }
    if ([Console]::IsInputRedirected) {
        Say "提示：未检测到 Git Bash——coding_agent 的 bash 工具在 Windows 上需要它。"
        Say "  装法：winget install Git.Git，或重跑本安装器选管理态安装。"
        return
    }
    Say ""
    Say "未检测到 Git Bash（coding_agent 的 bash 工具在 Windows 上需要它）。"
    $gitDir = Get-ManagedGitBashDir
    $winget = [bool](Get-Command winget.exe -ErrorAction SilentlyContinue)
    Say "  Y  管理态安装 Portable Git 到 $gitDir（默认，不影响系统 Git）"
    if ($winget) { Say "  w  winget 全局安装 Git for Windows" }
    Say "  n  跳过（bash 工具不可用，其余能力不受影响）"
    $prompt = '选择 [Y/n]'
    if ($winget) { $prompt = '选择 [Y/w/n]' }
    $answer = Read-Host $prompt
    if (-not $answer -or $answer -match '^(y|yes)$') {
        Install-GitBashManaged
    }
    elseif ($winget -and $answer -match '^(w|winget)$') {
        Install-GitBashWithWinget
    }
    else {
        Say "跳过 Git Bash 安装——bash 工具不可用，其余能力不受影响。"
    }
}

# —— 卸载 —————————————————————————————————————————————————————————————

function Do-Uninstall {
    $root = Install-Root
    $removed = $false

    # 摘 PATH 条目
    $current = Join-Path $root 'current'
    $existing = Get-UserEnv 'Path'
    if ($existing -and (@($existing -split ';') -contains $current)) {
        Remove-UserPathEntry $current
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

    Ensure-GitBash

    Set-PathEntry $root

    Say ""
    Say "安装完成。重开终端后运行 nova 启动（编程能力已随行——bash/edit/grep 等工具与 coding_agent 角色）。"
    Say ""
}

Main
