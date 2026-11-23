# ---------------------------------------------------------------------------
# run.ps1 -- Windows/PowerShell entry point for the Docker-based build tools.
#
# The actual logic lives in the existing .sh scripts; this dispatcher only
# prepares the Docker image, mounts and arguments.
#
# Usage:
#   .\run.ps1 build-release -Version v1.0.0
#   .\run.ps1 build-release -UpdateBin ..\update.bin -Version v1.0.0
#   .\run.ps1 build
#   .\run.ps1 build -OutDir C:\temp\payloads
#   .\run.ps1 validate
#   .\run.ps1 mock
#   .\run.ps1 bootstrap
#   .\run.ps1 build-patches
#   .\run.ps1 build-patches -UpdateBin ..\update.bin
# ---------------------------------------------------------------------------
[CmdletBinding()]
param(
    [Parameter(Position = 0, Mandatory = $true)]
    [ValidateSet("build-release", "build", "validate", "mock", "bootstrap",
        "build-patches")]
    [string]$Command,

    [string]$Version = "",
    [string]$UpdateBin = "",
    [string]$OutDir = "",
    [string]$Image = "mazda-tools",
    [switch]$BuildImage,
    [switch]$AllowUnreleased,
    [switch]$AllowVersionMismatch
)

$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location -LiteralPath $Root

function Stop-WithError([string]$Message) {
    [Console]::Error.WriteLine("ERROR: " + $Message)
    exit 1
}

function Test-DockerImage([string]$Name) {
    $previous = $ErrorActionPreference
    $ErrorActionPreference = "SilentlyContinue"
    & docker image inspect $Name *> $null
    $code = $LASTEXITCODE
    $ErrorActionPreference = $previous
    return ($code -eq 0)
}

function Resolve-ContainerLocation {
    param(
        [Parameter(Mandatory = $true)][string]$Path,
        [Parameter(Mandatory = $true)][string]$MountPoint,
        [switch]$ReadOnly
    )

    $full = [System.IO.Path]::GetFullPath($Path)
    $rootFull = (Resolve-Path -LiteralPath $Root).Path.TrimEnd('\')
    if ($full.StartsWith($rootFull + '\', [StringComparison]::OrdinalIgnoreCase)) {
        $relative = $full.Substring($rootFull.Length + 1).Replace('\', '/')
        return [pscustomobject]@{
            MountArgs = @()
            Path = "/work/$relative"
        }
    }

    $parent = Split-Path -Parent $full
    $leaf = Split-Path -Leaf $full
    $suffix = ""
    if ($ReadOnly) {
        $suffix = ":ro"
    }
    return [pscustomobject]@{
        MountArgs = @("-v", "${parent}:${MountPoint}${suffix}")
        Path = "$MountPoint/$leaf"
    }
}

# Option validation: fail early on combinations that make no sense.
switch ($Command) {
    "build-release" {
        if ($OutDir) { Stop-WithError "-OutDir is not valid for build-release" }
    }
    "build" {
        if ($Version) { Stop-WithError "-Version is only valid for build-release" }
        if ($AllowUnreleased -or $AllowVersionMismatch) {
            Stop-WithError "changelog overrides are only valid for build-release"
        }
    }
    "validate" {
        if ($Version -or $UpdateBin -or $OutDir -or $AllowUnreleased -or
            $AllowVersionMismatch) {
            Stop-WithError "validate does not accept build-release options"
        }
    }
    "mock" {
        if ($Version -or $UpdateBin -or $OutDir -or $AllowUnreleased -or
            $AllowVersionMismatch) {
            Stop-WithError "mock does not accept build-release options"
        }
    }
    "bootstrap" {
        if ($Version -or $UpdateBin -or $OutDir -or $AllowUnreleased -or
            $AllowVersionMismatch) {
            Stop-WithError "bootstrap does not accept build-release options"
        }
    }
    "build-patches" {
        if ($Version -or $OutDir -or $AllowUnreleased -or
            $AllowVersionMismatch) {
            Stop-WithError "build-patches does not accept build-release options"
        }
    }
}

if (-not (Get-Command docker -ErrorAction SilentlyContinue)) {
    Stop-WithError "docker was not found in PATH. Install Docker Desktop."
}

$previous = $ErrorActionPreference
$ErrorActionPreference = "SilentlyContinue"
& docker info *> $null
$dockerInfo = $LASTEXITCODE
$ErrorActionPreference = $previous
if ($dockerInfo -ne 0) {
    Stop-WithError "Docker daemon is not running."
}

if ($BuildImage -or -not (Test-DockerImage $Image)) {
    Write-Host "[i] building Docker image $Image ..."
    & docker build -t $Image $Root
    if ($LASTEXITCODE -ne 0) {
        exit $LASTEXITCODE
    }
}

$runArgs = @(
    "run", "--rm",
    "-v", "${Root}:/work",
    "-w", "/work"
)

# The bootstrap test mounts tmpfs filesystems to emulate the unit layouts.
if ($Command -eq "bootstrap") {
    $runArgs += "--privileged"
}

if ($Version) {
    $runArgs += @("-e", "VERSION=$Version")
}
if ($AllowUnreleased) {
    $runArgs += @("-e", "ALLOW_UNRELEASED=1")
}
if ($AllowVersionMismatch) {
    $runArgs += @("-e", "ALLOW_VERSION_MISMATCH=1")
}

$updateArg = $null
if ($UpdateBin) {
    if (-not (Test-Path -LiteralPath $UpdateBin -PathType Leaf)) {
        Stop-WithError "update image not found: $UpdateBin"
    }
    $location = Resolve-ContainerLocation -Path $UpdateBin -MountPoint "/update" -ReadOnly
    $runArgs += $location.MountArgs
    $updateArg = $location.Path
}

$outArg = $null
if ($OutDir) {
    if (Test-Path -LiteralPath $OutDir -PathType Leaf) {
        Stop-WithError "-OutDir points to a file: $OutDir"
    }
    $location = Resolve-ContainerLocation -Path $OutDir -MountPoint "/payload-out"
    $runArgs += $location.MountArgs
    $outArg = $location.Path
}

$runArgs += @($Image, "sh")

switch ($Command) {
    "build-release" {
        $runArgs += "build-release.sh"
        if ($updateArg) {
            $runArgs += $updateArg
        }
    }
    "build" {
        $runArgs += "patches/build.sh"
        if ($updateArg) {
            $runArgs += $updateArg
        } else {
            $runArgs += "/work/firmware/update.bin"
        }
        if ($outArg) {
            $runArgs += $outArg
        }
    }
    "validate" {
        $runArgs += "test/validate.sh"
    }
    "mock" {
        $runArgs += "test/mock-scenarios.sh"
    }
    "bootstrap" {
        $runArgs += "test/bootstrap.sh"
    }
    "build-patches" {
        $runArgs += "test/build-patches.sh"
        if ($updateArg) {
            $runArgs += $updateArg
        }
    }
}

Write-Host "[i] docker $($runArgs -join ' ')"
& docker @runArgs
exit $LASTEXITCODE
