$ErrorActionPreference = "Continue"
Set-Location -LiteralPath $PSScriptRoot

# 服务器到 GitHub 的网络被挡死了（HTTPS 各段 IP、SSH over 443 全部 connect timeout），
# 网页上的「从 GitHub 更新」在这台机器上永远拉不动。
# 这台 Windows 是唯一同时够得到 GitHub 和服务器的机器，所以由它中转：
# 代码仍然以 GitHub 为准，只是最后一段路改成 scp 推过去。
Write-Host "============================================"
Write-Host " DouYinSparkFlow deploy to server"
Write-Host "============================================"
Write-Host ""

# Windows 自带的 OpenSSH 可选功能常常没装，但 Git for Windows 自带一份 ssh/scp，
# 只是不在 PATH 里。先找 PATH，再去 Git 的安装目录里捞。
function Find-Tool($name) {
    $onPath = Get-Command $name -ErrorAction SilentlyContinue
    if ($onPath) { return $onPath.Source }
    $candidates = @(
        "$env:SystemRoot\System32\OpenSSH\$name.exe",
        "$env:ProgramFiles\Git\usr\bin\$name.exe",
        "${env:ProgramFiles(x86)}\Git\usr\bin\$name.exe",
        "$env:LOCALAPPDATA\Programs\Git\usr\bin\$name.exe"
    )
    foreach ($c in $candidates) {
        if ($c -and (Test-Path $c)) { return $c }
    }
    return $null
}

$scp = Find-Tool "scp"
$ssh = Find-Tool "ssh"
if (-not $scp -or -not $ssh) {
    Write-Host "[ERROR] ssh/scp not found."
    Write-Host "Install Git for Windows: https://git-scm.com/download/win"
    Write-Host "or enable OpenSSH Client in Settings > Apps > Optional Features"
    Read-Host "Press Enter to exit"
    exit 1
}
Write-Host "Using: $scp"

# 服务器信息记在本地，下次直接回车沿用
$confFile = Join-Path $PSScriptRoot ".deploy-target.json"
$conf = @{ host = ""; port = "22"; user = "root"; path = "/opt/DouYinSparkFlow" }
if (Test-Path $confFile) {
    try {
        (Get-Content $confFile -Raw | ConvertFrom-Json).PSObject.Properties |
            ForEach-Object { $conf[$_.Name] = [string]$_.Value }
    } catch {
        Write-Host "[WARN] .deploy-target.json unreadable, asking again."
    }
}

function Ask($label, $current) {
    $hint = if ($current) { " [$current]" } else { "" }
    $typed = Read-Host "$label$hint"
    if ([string]::IsNullOrWhiteSpace($typed)) { return $current }
    return $typed.Trim()
}

$conf.host = Ask "Server IP" $conf.host
if (-not $conf.host) {
    Write-Host "[ERROR] Server IP is required."
    Read-Host "Press Enter to exit"
    exit 1
}
$conf.port = Ask "SSH port" $conf.port
$conf.user = Ask "SSH user" $conf.user
$conf.path = Ask "Remote path" $conf.path
$conf | ConvertTo-Json | Set-Content -Path $confFile -Encoding utf8

$version = (Get-Content -Path (Join-Path $PSScriptRoot "VERSION") -Raw).Trim()
$target = "{0}@{1}:{2}/" -f $conf.user, $conf.host, $conf.path.TrimEnd("/")

# 只推代码。config/ logs/ data/ 一律不碰 —— 那里面是 .env、账号 Cookie、
# 卡密和登录快照，覆盖过去等于把所有号弄掉线。
$payload = @("webui", "core", "utils", "tests", "deploy", "VERSION", "requirements.txt", "main.py", "start.sh")
$missing = $payload | Where-Object { -not (Test-Path (Join-Path $PSScriptRoot $_)) }
if ($missing) {
    Write-Host "[ERROR] Missing: $($missing -join ', ')"
    Read-Host "Press Enter to exit"
    exit 1
}

Write-Host ""
Write-Host "Deploying v$version -> $target"
Write-Host "(config/ logs/ data/ are NOT touched)"
Write-Host ""

& $scp -P $conf.port -r @payload $target
if ($LASTEXITCODE -ne 0) {
    Write-Host ""
    Write-Host "[ERROR] scp failed."
    Write-Host "If 'Connection refused': wrong SSH port."
    Read-Host "Press Enter to exit"
    exit 1
}

Write-Host ""
Write-Host "Restarting service..."
$remote = "cd '$($conf.path)' && cat VERSION && systemctl restart douyin-sparkflow && sleep 2 && systemctl is-active douyin-sparkflow"
& $ssh -p $conf.port ("{0}@{1}" -f $conf.user, $conf.host) $remote
if ($LASTEXITCODE -ne 0) {
    Write-Host ""
    Write-Host "[WARN] Files copied, but restart failed. Run on server:"
    Write-Host "  systemctl restart douyin-sparkflow"
    Read-Host "Press Enter to exit"
    exit 1
}

Write-Host ""
Write-Host "[OK] Server now runs v$version"
Read-Host "Press Enter to exit"
