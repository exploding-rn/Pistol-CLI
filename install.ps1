$ErrorActionPreference = "Stop"

# ------------------------------------------------------------
# Pistol CLI Installer
# ------------------------------------------------------------

$RepoUrl = "https://github.com/exploding-rn/Pistol-CLI.git"
$InstallRoot = Join-Path $env:LOCALAPPDATA "Pistol"
$InstallDir = Join-Path $InstallRoot "source"
$VenvDir = Join-Path $InstallDir ".venv"
$ScriptsDir = Join-Path $VenvDir "Scripts"
$PythonExe = Join-Path $ScriptsDir "python.exe"

$script:InstallStart = Get-Date
$script:BarWidth = 32

function Show-Banner {
    Clear-Host

    $banner = @"
██████╗ ██╗███████╗████████╗ ██████╗ ██╗
██╔══██╗██║██╔════╝╚══██╔══╝██╔═══██╗██║
██████╔╝██║███████╗   ██║   ██║   ██║██║
██╔═══╝ ██║╚════██║   ██║   ██║   ██║██║
██║     ██║███████║   ██║   ╚██████╔╝███████╗
╚═╝     ╚═╝╚══════╝   ╚═╝    ╚═════╝ ╚══════╝
"@

    Write-Host $banner
    Write-Host "Pistol CLI Installer"
    Write-Host ""
}

function Get-TimeText {
    param(
        [double]$Seconds
    )

    if ($Seconds -lt 0) {
        $Seconds = 0
    }

    $span = [TimeSpan]::FromSeconds($Seconds)

    if ($span.TotalHours -ge 1) {
        return "{0:hh\:mm\:ss}" -f $span
    }

    return "{0:mm\:ss}" -f $span
}

function Show-ProgressBar {
    param(
        [int]$Percent,
        [string]$Status
    )

    $Percent = [Math]::Max(0, [Math]::Min(100, $Percent))

    $filled = [Math]::Floor(($Percent / 100.0) * $script:BarWidth)
    $empty = $script:BarWidth - $filled

    $bar = ("█" * $filled) + ("░" * $empty)

    $elapsed = ((Get-Date) - $script:InstallStart).TotalSeconds

    if ($Percent -gt 1 -and $Percent -lt 100) {
        $estimatedTotal = $elapsed / ($Percent / 100.0)
        $remaining = [Math]::Max(0, $estimatedTotal - $elapsed)
        $etaText = Get-TimeText $remaining
    }
    else {
        $etaText = "--:--"
    }

    $line = "[$bar] $Percent% $Status ETA $etaText"

    # Pad line so older, longer progress text gets overwritten cleanly.
    $width = [Math]::Max($Host.UI.RawUI.WindowSize.Width - 1, 80)
    if ($line.Length -lt $width) {
        $line = $line.PadRight($width)
    }

    Write-Host -NoNewline "`r$line"
}

function Show-Complete {
    $bar = "█" * $script:BarWidth
    $elapsed = ((Get-Date) - $script:InstallStart).TotalSeconds
    $elapsedText = Get-TimeText $elapsed

    $line = "[$bar] Installed in $elapsedText"

    $width = [Math]::Max($Host.UI.RawUI.WindowSize.Width - 1, 80)
    if ($line.Length -lt $width) {
        $line = $line.PadRight($width)
    }

    Write-Host "`r$line"
}

function Fail-Install {
    param(
        [string]$Message
    )

    Write-Host ""
    Write-Host ""
    Write-Host "Installation failed." -ForegroundColor Red
    Write-Host $Message -ForegroundColor Red
    exit 1
}

function Test-CommandExists {
    param(
        [string]$Name
    )

    return $null -ne (Get-Command $Name -ErrorAction SilentlyContinue)
}

function Add-ToUserPath {
    param(
        [string]$PathToAdd
    )

    $currentUserPath = [Environment]::GetEnvironmentVariable("Path", "User")

    if ([string]::IsNullOrWhiteSpace($currentUserPath)) {
        $currentUserPath = ""
    }

    $entries = $currentUserPath -split ";" | Where-Object {
        -not [string]::IsNullOrWhiteSpace($_)
    }

    $alreadyPresent = $false

    foreach ($entry in $entries) {
        try {
            if (
                [System.IO.Path]::GetFullPath($entry.TrimEnd("\")) -eq
                [System.IO.Path]::GetFullPath($PathToAdd.TrimEnd("\"))
            ) {
                $alreadyPresent = $true
                break
            }
        }
        catch {
            # Ignore malformed PATH entries.
        }
    }

    if (-not $alreadyPresent) {
        if ([string]::IsNullOrWhiteSpace($currentUserPath)) {
            $newPath = $PathToAdd
        }
        else {
            $newPath = "$currentUserPath;$PathToAdd"
        }

        [Environment]::SetEnvironmentVariable(
            "Path",
            $newPath,
            "User"
        )
    }

    # Also update PATH for the current installer process.
    if ($env:Path -notlike "*$PathToAdd*") {
        $env:Path = "$env:Path;$PathToAdd"
    }
}

Show-Banner

try {
    # --------------------------------------------------------
    # System checks
    # --------------------------------------------------------

    Show-ProgressBar 5 "Checking system..."

    if (-not (Test-CommandExists "git")) {
        throw "Git was not found. Install Git for Windows and make sure 'git' is on PATH."
    }

    if (-not (Test-CommandExists "py")) {
        throw "The Windows Python launcher 'py' was not found. Install Python 3.11 or newer."
    }

    $pythonVersion = & py -c "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}')"

    if (-not $pythonVersion) {
        throw "Python could not be started."
    }

    Show-ProgressBar 10 "Python $pythonVersion detected..."

    # --------------------------------------------------------
    # Install directory
    # --------------------------------------------------------

    Show-ProgressBar 15 "Preparing installation directory..."

    if (-not (Test-Path $InstallRoot)) {
        New-Item -ItemType Directory -Path $InstallRoot -Force | Out-Null
    }

    # --------------------------------------------------------
    # Clone / update source
    # --------------------------------------------------------

    if (Test-Path (Join-Path $InstallDir ".git")) {
        Show-ProgressBar 25 "Updating Pistol..."

        & git -C $InstallDir fetch --quiet

        if ($LASTEXITCODE -ne 0) {
            throw "Git fetch failed."
        }

        & git -C $InstallDir pull --ff-only --quiet

        if ($LASTEXITCODE -ne 0) {
            throw "Git pull failed."
        }
    }
    elseif (Test-Path $InstallDir) {
        Show-ProgressBar 20 "Cleaning incomplete installation..."

        Remove-Item $InstallDir -Recurse -Force
        Show-ProgressBar 25 "Downloading Pistol..."

        & git clone --quiet $RepoUrl $InstallDir

        if ($LASTEXITCODE -ne 0) {
            throw "Git clone failed."
        }
    }
    else {
        Show-ProgressBar 25 "Downloading Pistol..."

        & git clone --quiet $RepoUrl $InstallDir

        if ($LASTEXITCODE -ne 0) {
            throw "Git clone failed."
        }
    }

    # --------------------------------------------------------
    # Create virtual environment
    # --------------------------------------------------------

    Show-ProgressBar 45 "Creating isolated environment..."

    if (-not (Test-Path $PythonExe)) {
        & py -m venv $VenvDir

        if ($LASTEXITCODE -ne 0) {
            throw "Failed to create Pistol virtual environment."
        }
    }

    if (-not (Test-Path $PythonExe)) {
        throw "Pistol virtual environment was created, but python.exe could not be found."
    }

    # --------------------------------------------------------
    # Upgrade packaging tools
    # --------------------------------------------------------

    Show-ProgressBar 58 "Preparing Python environment..."

    & $PythonExe -m pip install --upgrade pip --quiet

    if ($LASTEXITCODE -ne 0) {
        throw "Failed to update pip."
    }

    # --------------------------------------------------------
    # Install Pistol
    # --------------------------------------------------------

    Show-ProgressBar 70 "Installing Pistol..."

    & $PythonExe -m pip install -e $InstallDir --quiet

    if ($LASTEXITCODE -ne 0) {
        throw "Pistol package installation failed."
    }

    # --------------------------------------------------------
    # PATH
    # --------------------------------------------------------

    Show-ProgressBar 86 "Configuring command access..."

    Add-ToUserPath $ScriptsDir

    # --------------------------------------------------------
    # Verify
    # --------------------------------------------------------

    Show-ProgressBar 94 "Verifying installation..."

    $PistolExe = Join-Path $ScriptsDir "pistol.exe"

    if (-not (Test-Path $PistolExe)) {
        throw "pistol.exe was not created during installation."
    }

    & $PistolExe --version | Out-Null

    if ($LASTEXITCODE -ne 0) {
        throw "Pistol was installed but failed its verification check."
    }

    # --------------------------------------------------------
    # Complete
    # --------------------------------------------------------

    Show-ProgressBar 99 "Finalizing..."
    Start-Sleep -Milliseconds 200

    Show-Complete

    Write-Host ""
    Write-Host "Pistol CLI installed successfully." -ForegroundColor Green
    Write-Host ""
    Write-Host "Installed to:"
    Write-Host "  $InstallDir"
    Write-Host ""
    Write-Host "Try:"
    Write-Host "  pistol --help"
    Write-Host "  pistol doctor"
    Write-Host "  pistol shrimp"
    Write-Host ""
    Write-Host "If 'pistol' is not recognized in this terminal,"
    Write-Host "open a new terminal window so Windows reloads your PATH."
    Write-Host ""
}
catch {
    Fail-Install $_.Exception.Message
}