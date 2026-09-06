# install.ps1 - Nova Windows installer (PowerShell port of install.sh)
#
# Usage:
#   irm https://github.com/RNA-Nova/nova/releases/latest/download/install.ps1 | iex
#   Uninstall: iex "& { $(irm https://github.com/RNA-Nova/nova/releases/latest/download/install.ps1) } uninstall"
#   Local file form: powershell -ExecutionPolicy Bypass -File install.ps1 [uninstall]
#
# What it does:
#   1. Preflight (Windows + architecture)
#   2. Resolve the latest release version (or pin with NOVA_VERSION)
#   3. Download the platform zip + SHA256SUMS and verify sha256
#   4. Extract to <install root>/releases/<version>/, flip the 'current' junction (NTFS link, no admin)
#   5. Post-install self check (nova.exe --version must match the target version)
#   6. Install the official coding pack (npm:nova-coding-agent - warn only, never blocks)
#   6b. Git Bash provisioning (Windows dependency of the bash tool: managed PortableGit
#       into the agent dir + settings shell_path pointed at it; an existing Git Bash is used as-is)
#   7. Add the current dir to the user PATH (skipped when already present)
#
# Environment: NOVA_VERSION / NOVA_INSTALLER_RELEASES_BASE (file:// drills supported) /
#   NOVA_RELEASES_API_BASE / NOVA_INSTALL_DIR (default ~\.nova\agent\install) /
#   NOVA_NO_CODING=1 / NOVA_OFFLINE=1
#
# The file is pure ASCII (comments included) and stored WITHOUT a BOM. Two execution
# forms must both work: irm | iex (PS 5.1 decodes the HTTP body without charset
# awareness - any non-ASCII byte, BOM included, turns into mojibake that breaks parsing)

$ErrorActionPreference = 'Stop'
# Windows PowerShell 5.1 defaults to a TLS version GitHub rejects - switch to 1.2 first
[Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12
# Script-level args (shared by the irm|iex and -File forms) - $args inside a function is
# the function's own; pass them explicitly here
$ScriptArgs = $args

$Repo = 'RNA-Nova/nova'
$ReleasesBase = "$env:NOVA_INSTALLER_RELEASES_BASE"
if (-not $ReleasesBase) { $ReleasesBase = "https://github.com/$Repo/releases" }

function Say([string]$msg) { Write-Host $msg }
function Err([string]$msg) { Write-Host "error: $msg" -ForegroundColor Red }

# ------------------------------ Preflight ------------------------------

function Test-Platform {
    if ($PSVersionTable.PSEdition -eq 'Core' -and -not $IsWindows) {
        Err "This installer is Windows-only (use install.sh on macOS/Linux)"
        exit 1
    }
# Read the machine env registry, not the process env - on ARM64 Windows running x64
# PowerShell, $env:PROCESSOR_ARCHITECTURE is the emulated (x64) value; the registry
    $arch = (Get-ItemProperty "HKLM:\SYSTEM\CurrentControlSet\Control\Session Manager\Environment").PROCESSOR_ARCHITECTURE
    switch ($arch) {
        'AMD64' { return 'windows-x64' }
        'ARM64' { return 'windows-arm64' }
        default { Err "Unsupported CPU architecture: $arch"; exit 1 }
    }
}

# ------------------------------ Paths ------------------------------

function Install-Root {
    if ($env:NOVA_INSTALL_DIR) { return $env:NOVA_INSTALL_DIR }
    return (Join-Path $HOME '.nova\agent\install')
}

function Read-CurrentVersion {
    $marker = Join-Path (Install-Root) 'current-version.txt'
    if (Test-Path $marker) { return (Get-Content $marker -Raw).Trim() }
    return $null
}

# ----------------------- Version resolve & download -----------------------

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
        Err "Could not resolve the latest release version (set NOVA_VERSION to pin one): $_"
        exit 1
    }
    if (-not $rel.tag_name) { Err "Could not resolve the latest release version (set NOVA_VERSION to pin one)"; exit 1 }
    return $rel.tag_name
}

function Fetch([string]$url, [string]$dest) {
    try {
        if ($url.StartsWith('file://')) {
            $local = ([Uri]$url).LocalPath
# Windows file:///C:/... -> LocalPath yields /C:/... (strip the leading slash)
            if ($local -match '^/[A-Za-z]:') { $local = $local.Substring(1) }
            Copy-Item $local $dest -Force
        }
        else {
# Explicit curl.exe: in Windows PowerShell "curl" is an Invoke-WebRequest alias and much
# slower; fall back to IWR if curl.exe fails
            curl.exe "-#SfLo" $dest $url
            if ($LASTEXITCODE -ne 0) {
                Invoke-WebRequest -Uri $url -OutFile $dest -UseBasicParsing
            }
        }
    }
    catch {
        if (Test-Path $dest) { Remove-Item $dest -Force }
        Err "Download failed: $url"
        exit 1
    }
}

function Test-Sha256([string]$file, [string]$sumsFile) {
    $name = Split-Path $file -Leaf
# Last entry wins for duplicate names (SHA256SUMS is append-only - reruns append the fresh line at the tail)
    $line = Get-Content $sumsFile | Where-Object { $_ -match "  $([regex]::Escape($name))$" } | Select-Object -Last 1
    if (-not $line) { Err "SHA256SUMS has no entry for $name"; exit 1 }
    $expected = ($line -split '\s+')[0].ToLowerInvariant()
    $actual = (Get-FileHash $file -Algorithm SHA256).Hash.ToLowerInvariant()
    if ($expected -ne $actual) {
        Err "$name failed sha256 verification - corrupted or tampered download; nothing installed"
        exit 1
    }
    Say "Verified: $name sha256 OK"
}

# ------------------------------ Assembly ------------------------------

function Activate-Release([string]$root, [string]$version, [string]$releaseDir) {
    $current = Join-Path $root 'current'
    if (Test-Path $current) {
# junction removal: cmd rmdir removes only the link (PowerShell 5.1's
# Remove-Item -Recurse can descend into the target directory - a known trap)
        cmd /c rmdir "$current" | Out-Null
    }
    New-Item -ItemType Junction -Path $current -Target $releaseDir | Out-Null
    Set-Content -Path (Join-Path $root 'current-version.txt') -Value $version -NoNewline
}

function Install-CodingBundle([string]$root) {
    if ($env:NOVA_NO_CODING -eq '1' -or $env:NOVA_OFFLINE -eq '1') {
        Say "Skipping the coding pack (NOVA_NO_CODING/NOVA_OFFLINE) - install later: nova-server.exe pkg install npm:nova-coding-agent"
        return
    }
    $server = Join-Path $root 'current\runtime\nova-server.exe'
    Say ""
    Say "Installing the official coding pack (npm:nova-coding-agent)..."
    & $server pkg install npm:nova-coding-agent
    if ($LASTEXITCODE -ne 0) {
        Say "Warning: coding pack installation failed (install it later):"
        Say "  & `"$server`" pkg install npm:nova-coding-agent"
        Say "  Without it nova still has session infrastructure (nova-base is built in)."
    }
}

# ----------------------- User environment (PATH) -----------------------
# Write the registry directly instead of [Environment]::SetEnvironmentVariable - the
# latter expands existing %VAR% references into literal values on read-back
# (REG_SZ-ified), clobbering the user's own variable references.
# Broadcast WM_SETTINGCHANGE after writing (new terminals see it immediately) + update

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
# values containing % stay ExpandString (otherwise %USERPROFILE%-style refs get baked in)
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
        Say "PATH: $current is already on the user PATH"
        return
    }
    Add-UserPathEntry $current
    Say "PATH: added to user PATH ($current) - new terminals see it immediately"
}

# ------------------- Git Bash provisioning (bash tool dependency) --------------------
# coding_agent's bash tool requires bash (Git Bash) on Windows. Machines without it get
# a managed PortableGit installed into the agent dir + settings shell_path pointed at it
# (toolchain read path: ToolContext.settings.get_shell_path() -> shell resolution).
# .get_shell_path() -> shell resolution).

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
# configured shell_path wins; configured but missing on disk -> return empty (re-provision)
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

    Say "Resolving the latest Portable Git release..."
    $release = Invoke-RestMethod -Uri $GitForWindowsLatestReleaseApi -Headers @{ 'User-Agent' = 'nova-installer' }
    $asset = $release.assets | Where-Object { $_.name -like "PortableGit-*$assetSuffix" } | Select-Object -First 1
    if (-not $asset) { Err "No Portable Git asset found ($assetSuffix)"; exit 1 }

# sha256 lives in the release body's asset table ("filename | sha256" rows)
    $escaped = [regex]::Escape($asset.name)
    $m = [regex]::Match($release.body, "(?m)^$escaped\s+\|\s+([a-fA-F0-9]{64})\s*$")
    if (-not $m.Success) { Err "No sha256 record found for $($asset.name)"; exit 1 }
    return [PSCustomObject]@{ Name = $asset.name; Url = $asset.browser_download_url; Sha256 = $m.Groups[1].Value.ToLowerInvariant() }
}

function Install-GitBashManaged {
    $gitDir = Get-ManagedGitBashDir
    $bashPath = Get-ManagedGitBashPath
    if (Test-Path $bashPath -PathType Leaf) {
        Set-SettingsShellPath $bashPath
        Say "Git Bash already in place at $bashPath (shell_path written)"
        return
    }

    $tmp = Join-Path ([System.IO.Path]::GetTempPath()) "nova-git-bash-$PID"
    $extractDir = Join-Path $tmp 'extract'
    Remove-Item $tmp -Recurse -Force -ErrorAction SilentlyContinue
    New-Item -ItemType Directory -Force -Path $tmp, $extractDir, (Get-NovaAgentDir) | Out-Null

    $asset = Get-PortableGitAsset
    $pkg = Join-Path $tmp $asset.Name
    Say "Downloading Portable Git $($asset.Name)..."
    Fetch $asset.Url $pkg
    Say "Verifying sha256..."
    $actual = (Get-FileHash $pkg -Algorithm SHA256).Hash.ToLowerInvariant()
    if ($actual -ne $asset.Sha256) {
        Err "Portable Git failed sha256 verification (expected $($asset.Sha256), got $actual)"
        exit 1
    }

    Say "Extracting to $gitDir"
# PortableGit-*.7z.exe is a self-extracting archive: -y silent, -o target directory
    $proc = Start-Process -FilePath $pkg -ArgumentList @('-y', "-o`"$extractDir`"") -PassThru -Wait -WindowStyle Hidden
    if ($proc.ExitCode -ne 0) { Err "Portable Git extraction failed (exit $($proc.ExitCode))"; exit $proc.ExitCode }
    if (-not (Test-Path (Join-Path $extractDir 'bin\bash.exe') -PathType Leaf)) {
        Err "Portable Git extraction produced no bin\bash.exe"
        exit 1
    }
    Remove-Item $gitDir -Recurse -Force -ErrorAction SilentlyContinue
    Move-Item $extractDir $gitDir -Force
    Remove-Item $tmp -Recurse -Force -ErrorAction SilentlyContinue

    Set-SettingsShellPath $bashPath
    Say "Git Bash installed at $gitDir (managed; settings shell_path points to it)"
}

function Install-GitBashWithWinget {
    Say "Installing Git for Windows globally via winget..."
    & winget.exe install --id Git.Git -e --source winget --accept-source-agreements --accept-package-agreements
    if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
    $found = Find-GitBash
    if ($found) {
        if ((Get-SettingsShellPath) -and -not (Test-Path (Get-SettingsShellPath) -PathType Leaf)) {
            Set-SettingsShellPath $found
        }
        Say "Git Bash installed at $found"
    }
    else {
        Say "Git installed but bash is not visible in this terminal yet - it will be after a restart"
    }
}

function Ensure-GitBash {
    $existing = Find-GitBash
    if ($existing) {
        Say "Git Bash: $existing (bash tool ready)"
        return
    }
    if ([Console]::IsInputRedirected) {
        Say "Note: Git Bash not found - coding_agent's bash tool needs it on Windows."
        Say "  Install: winget install Git.Git, or rerun this installer for the managed option."
        return
    }
    Say ""
    Say "Git Bash not found (coding_agent's bash tool needs it on Windows)."
    $gitDir = Get-ManagedGitBashDir
    $winget = [bool](Get-Command winget.exe -ErrorAction SilentlyContinue)
    Say "  Y  managed Portable Git into $gitDir (default; leaves system Git alone)"
    if ($winget) { Say "  w  install Git for Windows globally via winget" }
    Say "  n  skip (bash tool unavailable; everything else works)"
    $prompt = 'Choose [Y/n]'
    if ($winget) { $prompt = 'Choose [Y/w/n]' }
    $answer = Read-Host $prompt
    if (-not $answer -or $answer -match '^(y|yes)$') {
        Install-GitBashManaged
    }
    elseif ($winget -and $answer -match '^(w|winget)$') {
        Install-GitBashWithWinget
    }
    else {
        Say "Skipping Git Bash - the bash tool will be unavailable; everything else works."
    }
}

# ------------------------------ Uninstall ------------------------------

function Do-Uninstall {
    $root = Install-Root
    $removed = $false

# remove PATH entry
    $current = Join-Path $root 'current'
    $existing = Get-UserEnv 'Path'
    if ($existing -and (@($existing -split ';') -contains $current)) {
        Remove-UserPathEntry $current
        Say "Removed $current from user PATH"
        $removed = $true
    }

    if (Test-Path $current) { cmd /c rmdir "$current" | Out-Null }
    if (Test-Path $root) {
        Remove-Item $root -Recurse -Force
        Say "Deleted $root"
        $removed = $true
    }
    if (-not $removed) { Say "No install trace of this installer found ($root)" }
    Say ""
    Say "User data remains at ~\.nova\agent (settings/sessions/packages)."
    Say "For full removal: Remove-Item -Recurse -Force ~\.nova\agent"
}

# ------------------------------ Main ------------------------------

function Main {
    if ($script:ScriptArgs -and $script:ScriptArgs[0] -eq 'uninstall') {
        Do-Uninstall
        exit 0
    }

    Say ""
    Write-Host "  Nova Installer" -ForegroundColor Cyan
    Write-Host "  Framework + TUI + official bundles as static dual binaries (Windows)" -ForegroundColor DarkGray
    Say ""

    $platform = Test-Platform
    Say "Platform: $platform"

    $version = Resolve-Version
    $root = Install-Root
    $releaseDir = Join-Path $root "releases\$version"
    $asset = "nova-$platform.zip"

    $currentVersion = Read-CurrentVersion
    $novaExe = Join-Path $releaseDir 'nova.exe'
    $serverExe = Join-Path $releaseDir 'runtime\nova-server.exe'
    if ($currentVersion -eq $version -and (Test-Path $novaExe) -and (Test-Path $serverExe)) {
        Say "Already at $version ($releaseDir) - skipping reinstall, activating"
    }
    else {
        $stage = Join-Path $root "staging\$version.$PID"
        if (Test-Path $stage) { Remove-Item $stage -Recurse -Force }
        New-Item -ItemType Directory -Path $stage -Force | Out-Null
        try {
            Say "Downloading: $ReleasesBase/download/$version/$asset"
            Fetch "$ReleasesBase/download/$version/$asset" (Join-Path $stage $asset)
            Fetch "$ReleasesBase/download/$version/SHA256SUMS" (Join-Path $stage 'SHA256SUMS')
            Test-Sha256 (Join-Path $stage $asset) (Join-Path $stage 'SHA256SUMS')

            New-Item -ItemType Directory -Path $releaseDir -Force | Out-Null
            Expand-Archive -Path (Join-Path $stage $asset) -DestinationPath $releaseDir -Force
            Say "Extracted: $releaseDir"
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

# post-install self check: --version must equal the target version (catches corrupt/mismatched packages)
    $reported = (& (Join-Path $root 'current\nova.exe') --version 2>$null | Out-String).Trim()
    $expected = $version.TrimStart('v')
    if ($reported -ne $expected) {
        Err "Self-check failed: nova --version reported '$reported', expected '$expected'"
        exit 1
    }
    Say "Self-check: nova --version = $reported"

    Install-CodingBundle $root

    Ensure-GitBash

    Set-PathEntry $root

    Say ""
    Say "Install complete. Open a new terminal and run: nova (coding capability included - bash/edit/grep tools + coding_agent role)."
    Say ""
}

Main
