$ErrorActionPreference = "Stop"

# ============================================================
# Pistol CLI Installer
# ============================================================

$RepoUrl = "https://github.com/exploding-rn/Pistol-CLI.git"

$InstallRoot = Join-Path $env:LOCALAPPDATA "Pistol"
$InstallDir  = Join-Path $InstallRoot "source"
$VenvDir     = Join-Path $InstallDir ".venv"
$ScriptsDir  = Join-Path $VenvDir "Scripts"
$PythonExe   = Join-Path $ScriptsDir "python.exe"
$PistolExe   = Join-Path $ScriptsDir "pistol.exe"

$script:Progress       = 0.0
$script:TargetProgress = 0.0
$script:InstallStart   = Get-Date
$script:BarWidth       = 34

# Used for smoother ETA calculation
$script:ProgressHistory = New-Object System.Collections.Generic.List[object]

# ============================================================
# ANSI COLORS
# ============================================================

$ESC = [char]27
$RESET = "$ESC[0m"

function Write-GradientText {
    param(
        [string]$Text,
        [int[]]$Start = @(60, 190, 110),
        [int[]]$End   = @(90, 170, 255)
    )

    if ($Text.Length -eq 0) {
        Write-Host ""
        return
    }

    $out = ""

    for ($i = 0; $i -lt $Text.Length; $i++) {
        $t = 0

        if ($Text.Length -gt 1) {
            $t = $i / ($Text.Length - 1)
        }

        $r = [int]($Start[0] + (($End[0] - $Start[0]) * $t))
        $g = [int]($Start[1] + (($End[1] - $Start[1]) * $t))
        $b = [int]($Start[2] + (($End[2] - $Start[2]) * $t))

        $out += "$ESC[38;2;$r;$g;${b}m$($Text[$i])"
    }

    Write-Host "$out$RESET"
}

function Show-Banner {
    Clear-Host

    $banner = @(
        "██████╗ ██╗███████╗████████╗ ██████╗ ██╗"
        "██╔══██╗██║██╔════╝╚══██╔══╝██╔═══██╗██║"
        "██████╔╝██║███████╗   ██║   ██║   ██║██║"
        "██╔═══╝ ██║╚════██║   ██║   ██║   ██║██║"
        "██║     ██║███████║   ██║   ╚██████╔╝███████╗"
        "╚═╝     ╚═╝╚══════╝   ╚═╝    ╚═════╝ ╚══════╝"
    )

    foreach ($line in $banner) {
        Write-GradientText $line
    }

    Write-Host ""
    Write-GradientText "Pistol CLI Installer"
    Write-Host ""
}

# ============================================================
# TIME HELPERS
# ============================================================

function Format-Time {
    param(
        [double]$Seconds
    )

    $Seconds = [Math]::Max(0, $Seconds)

    $span = [TimeSpan]::FromSeconds($Seconds)

    if ($span.TotalHours -ge 1) {
        return "{0:hh\:mm\:ss}" -f $span
    }

    return "{0:mm\:ss}" -f $span
}

# ============================================================
# PROGRESS / ETA
# ============================================================

function Add-ProgressSample {
    $now = Get-Date

    $sample = [PSCustomObject]@{
        Time     = $now
        Progress = $script:Progress
    }

    $script:ProgressHistory.Add($sample)

    # Keep only recent samples.
    while ($script:ProgressHistory.Count -gt 15) {
        $script:ProgressHistory.RemoveAt(0)
    }
}

function Get-EstimatedSecondsRemaining {

    if ($script:Progress -lt 2) {
        return $null
    }

    if ($script:ProgressHistory.Count -lt 3) {
        return $null
    }

    $first = $script:ProgressHistory[0]
    $last  = $script:ProgressHistory[$script:ProgressHistory.Count - 1]

    $deltaProgress = $last.Progress - $first.Progress
    $deltaSeconds  = ($last.Time - $first.Time).TotalSeconds

    if ($deltaProgress -le 0.2 -or $deltaSeconds -le 0) {
        return $null
    }

    $speed = $deltaProgress / $deltaSeconds

    if ($speed -le 0) {
        return $null
    }

    $remaining = 100 - $script:Progress

    return $remaining / $speed
}

function Draw-Progress {
    param(
        [string]$Status
    )

    $percent = [Math]::Floor($script:Progress)

    $filled = [Math]::Floor(
        ($script:Progress / 100.0) * $script:BarWidth
    )

    $filled = [Math]::Min($script:BarWidth, $filled)

    $empty = $script:BarWidth - $filled

    $bar = ("█" * $filled) + ("░" * $empty)

    $etaSeconds = Get-EstimatedSecondsRemaining

    if ($null -eq $etaSeconds) {
        $eta = "--:--"
    }
    else {
        # Clamp ETA so one weird measurement does not explode it.
        $etaSeconds = [Math]::Min($etaSeconds, 599)
        $eta = Format-Time $etaSeconds
    }

    $line = "[$bar] $percent% $Status  ETA $eta"

    try {
        $width = $Host.UI.RawUI.WindowSize.Width - 1

        if ($width -gt $line.Length) {
            $line = $line.PadRight($width)
        }
    }
    catch {
    }

    Write-Host -NoNewline "`r$line"
}

function Set-ProgressTarget {
    param(
        [double]$Target
    )

    # Progress must never move backwards.
    if ($Target -gt $script:TargetProgress) {
        $script:TargetProgress = [Math]::Min(100, $Target)
    }
}

function Advance-Progress {
    param(
        [string]$Status,
        [double]$Maximum,
        [double]$Speed = 0.55
    )

    if ($Maximum -gt $script:TargetProgress) {
        Set-ProgressTarget $Maximum
    }

    # Slowly approach the current target.
    if ($script:Progress -lt $script:TargetProgress) {

        $distance = $script:TargetProgress - $script:Progress

        # Move quickly at first, then slow as we approach target.
        $step = [Math]::Max(
            0.08,
            [Math]::Min($Speed, $distance * 0.16)
        )

        $script:Progress += $step

        if ($script:Progress -gt $script:TargetProgress) {
            $script:Progress = $script:TargetProgress
        }
    }

    Add-ProgressSample
    Draw-Progress $Status
}

# ============================================================
# RUN LONG OPERATION WITH ANIMATED PROGRESS
# ============================================================

function Invoke-WithProgress {
    param(
        [scriptblock]$Action,
        [string]$Status,
        [double]$StartPercent,
        [double]$CreepToPercent
    )

    if ($script:Progress -lt $StartPercent) {
        $script:Progress = $StartPercent
    }

    Set-ProgressTarget $CreepToPercent

    $job = Start-Job -ScriptBlock $Action

    try {
        while ($job.State -eq "Running") {

            Advance-Progress `
                -Status $Status `
                -Maximum $CreepToPercent `
                -Speed 0.35

            Start-Sleep -Seconds 1

            $job = Get-Job -Id $job.Id
        }

        $output = Receive-Job -Job $job

        if ($job.State -ne "Completed") {
            throw "Operation failed."
        }

        return $output
    }
    finally {
        Remove-Job -Job $job -Force -ErrorAction SilentlyContinue
    }
}

# ============================================================
# COMMAND CHECK
# ============================================================

function Test-CommandExists {
    param([string]$Name)

    return $null -ne (
        Get-Command $Name -ErrorAction SilentlyContinue
    )
}

# ============================================================
# PATH
# ============================================================

function Add-ToUserPath {
    param(
        [string]$PathToAdd
    )

    $userPath = [Environment]::GetEnvironmentVariable(
        "Path",
        "User"
    )

    if ([string]::IsNullOrWhiteSpace($userPath)) {
        $userPath = ""
    }

    $parts = @(
        $userPath -split ";" |
        Where-Object {
            -not [string]::IsNullOrWhiteSpace($_)
        }
    )

    $exists = $false

    foreach ($part in $parts) {
        try {

            $a = [IO.Path]::GetFullPath(
                $part.Trim()
            ).TrimEnd("\")

            $b = [IO.Path]::GetFullPath(
                $PathToAdd
            ).TrimEnd("\")

            if ($a -ieq $b) {
                $exists = $true
                break
            }

        }
        catch {
        }
    }

    if (-not $exists) {

        if ([string]::IsNullOrWhiteSpace($userPath)) {
            $newPath = $PathToAdd
        }
        else {
            $newPath = "$userPath;$PathToAdd"
        }

        [Environment]::SetEnvironmentVariable(
            "Path",
            $newPath,
            "User"
        )
    }

    if ($env:Path -notlike "*$PathToAdd*") {
        $env:Path = "$env:Path;$PathToAdd"
    }
}

# ============================================================
# FAILURE
# ============================================================

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

# ============================================================
# MAIN INSTALL
# ============================================================

Show-Banner

try {

    # --------------------------------------------------------
    # 0 → 8%
    # --------------------------------------------------------

    Set-ProgressTarget 8

    for ($i = 0; $i -lt 4; $i++) {
        Advance-Progress "Checking system..." 8 1.5
        Start-Sleep -Milliseconds 250
    }

    if (-not (Test-CommandExists "git")) {
        throw "Git is not installed or not available on PATH."
    }

    if (-not (Test-CommandExists "py")) {
        throw "Python launcher 'py' is not installed or not available on PATH."
    }

    $pythonVersion = & py -c `
        "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}')"

    if ($LASTEXITCODE -ne 0) {
        throw "Python could not be started."
    }

    $script:Progress = 8

    # --------------------------------------------------------
    # 8 → 15%
    # --------------------------------------------------------

    Set-ProgressTarget 15

    while ($script:Progress -lt 14.8) {
        Advance-Progress "Preparing installation..." 15 1.1
        Start-Sleep -Milliseconds 100
    }

    New-Item `
        -ItemType Directory `
        -Path $InstallRoot `
        -Force |
        Out-Null

    $script:Progress = 15

    # --------------------------------------------------------
    # 15 → 42%
    # DOWNLOAD / UPDATE
    # --------------------------------------------------------

    if (Test-Path (Join-Path $InstallDir ".git")) {

        Invoke-WithProgress `
            -Status "Updating Pistol..." `
            -StartPercent 15 `
            -CreepToPercent 40 `
            -Action {

                param($dir)

                git -C $dir fetch --quiet

                if ($LASTEXITCODE -ne 0) {
                    throw "git fetch failed"
                }

                git -C $dir pull --ff-only --quiet

                if ($LASTEXITCODE -ne 0) {
                    throw "git pull failed"
                }

            }.GetNewClosure()

    }
    else {

        if (Test-Path $InstallDir) {
            Remove-Item $InstallDir -Recurse -Force
        }

        $job = Start-Job -ScriptBlock {
            param($repo, $dir)

            git clone --quiet $repo $dir

            if ($LASTEXITCODE -ne 0) {
                throw "git clone failed"
            }

        } -ArgumentList $RepoUrl, $InstallDir

        try {

            while ($job.State -eq "Running") {

                Set-ProgressTarget 40

                Advance-Progress `
                    "Downloading Pistol..." `
                    40 `
                    0.35

                Start-Sleep -Seconds 1

                $job = Get-Job $job.Id
            }

            Receive-Job $job | Out-Null

            if ($job.State -ne "Completed") {
                throw "Git clone failed."
            }

        }
        finally {
            Remove-Job $job -Force -ErrorAction SilentlyContinue
        }
    }

    $script:Progress = 42
    Draw-Progress "Source ready..."

    # --------------------------------------------------------
    # 42 → 57%
    # VENV
    # --------------------------------------------------------

    if (-not (Test-Path $PythonExe)) {

        $job = Start-Job -ScriptBlock {
            param($venv)

            py -m venv $venv

            if ($LASTEXITCODE -ne 0) {
                throw "venv creation failed"
            }

        } -ArgumentList $VenvDir

        try {

            while ($job.State -eq "Running") {

                Set-ProgressTarget 56

                Advance-Progress `
                    "Creating environment..." `
                    56 `
                    0.30

                Start-Sleep -Seconds 1

                $job = Get-Job $job.Id
            }

            Receive-Job $job | Out-Null

            if ($job.State -ne "Completed") {
                throw "Failed to create virtual environment."
            }

        }
        finally {
            Remove-Job $job -Force -ErrorAction SilentlyContinue
        }
    }

    $script:Progress = 57

    # --------------------------------------------------------
    # 57 → 82%
    # INSTALL
    # --------------------------------------------------------

    $job = Start-Job -ScriptBlock {

        param($python, $installDir)

        & $python -m pip install `
            --disable-pip-version-check `
            --quiet `
            -e $installDir

        if ($LASTEXITCODE -ne 0) {
            throw "pip install failed"
        }

    } -ArgumentList $PythonExe, $InstallDir

    try {

        while ($job.State -eq "Running") {

            Set-ProgressTarget 81

            Advance-Progress `
                "Installing Pistol..." `
                81 `
                0.28

            Start-Sleep -Seconds 1

            $job = Get-Job $job.Id
        }

        Receive-Job $job | Out-Null

        if ($job.State -ne "Completed") {
            throw "Pistol installation failed."
        }

    }
    finally {
        Remove-Job $job -Force -ErrorAction SilentlyContinue
    }

    $script:Progress = 82

    # --------------------------------------------------------
    # 82 → 91%
    # PATH
    # --------------------------------------------------------

    Set-ProgressTarget 91

    while ($script:Progress -lt 88) {

        Advance-Progress `
            "Configuring command access..." `
            91 `
            0.5

        Start-Sleep -Milliseconds 100
    }

    Add-ToUserPath $ScriptsDir

    $script:Progress = 91

    # --------------------------------------------------------
    # 91 → 97%
    # VERIFY
    # --------------------------------------------------------

    Set-ProgressTarget 97

    while ($script:Progress -lt 94) {

        Advance-Progress `
            "Verifying installation..." `
            97 `
            0.5

        Start-Sleep -Milliseconds 100
    }

    if (-not (Test-Path $PistolExe)) {
        throw "pistol.exe was not created."
    }

    & $PistolExe --version | Out-Null

    if ($LASTEXITCODE -ne 0) {
        throw "Pistol failed its verification check."
    }

    $script:Progress = 97

    # --------------------------------------------------------
    # 97 → 100%
    # --------------------------------------------------------

    Set-ProgressTarget 100

    while ($script:Progress -lt 99.8) {

        Advance-Progress `
            "Finalizing..." `
            100 `
            0.8

        Start-Sleep -Milliseconds 80
    }

    $script:Progress = 100

    $bar = "█" * $script:BarWidth

    $elapsed = (
        (Get-Date) - $script:InstallStart
    ).TotalSeconds

    $elapsedText = Format-Time $elapsed

    Write-Host "`r[$bar] Installed in $elapsedText                    "

    Write-Host ""
    Write-GradientText "Pistol CLI installed successfully."
    Write-Host ""

    Write-Host "Installed to:"
    Write-Host "  $InstallDir"
    Write-Host ""

    Write-Host "Try:"
    Write-Host "  pistol --help"
    Write-Host "  pistol doctor"
    Write-Host "  pistol shrimp"
    Write-Host ""

    Write-Host "If this terminal does not recognize 'pistol',"
    Write-Host "open a new terminal so Windows reloads PATH."
    Write-Host ""
}
catch {

    Fail-Install $_.Exception.Message
}
