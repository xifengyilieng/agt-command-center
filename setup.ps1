param(
    [switch]$Wallpaper,
    [string]$WeDir,
    [switch]$Startup,
    [switch]$NoBrowser,
    [switch]$CheckOnly
)
$ErrorActionPreference = 'Stop'
if ($env:OS -ne 'Windows_NT') { throw 'This installer supports Windows 10/11.' }
. (Join-Path $PSScriptRoot 'python_launcher.ps1')
$pythonPath = Get-AgtPython
Write-Output "Python: $pythonPath"
& $pythonPath (Join-Path $PSScriptRoot 'check_environment.py')
if ($LASTEXITCODE -ne 0) { throw 'Environment check failed.' }
if ($CheckOnly) { return }

& $pythonPath (Join-Path $PSScriptRoot 'build_screen.py')
if ($LASTEXITCODE -ne 0) { throw 'Build failed.' }
if ($Wallpaper -or $WeDir) {
    $wallpaperArguments = @((Join-Path $PSScriptRoot 'install_wallpaper.py'))
    if ($WeDir) { $wallpaperArguments += @('--we-dir', $WeDir) }
    & $pythonPath @wallpaperArguments
    if ($LASTEXITCODE -ne 0) { throw 'Wallpaper installation failed. Browser mode remains available.' }
}
& (Join-Path $PSScriptRoot 'start_collector.ps1') -OpenBrowser:(-not $NoBrowser)
if ($Startup) { & (Join-Path $PSScriptRoot 'install_startup.ps1') }
$health = Invoke-RestMethod -Uri 'http://127.0.0.1:8767/api/health' -TimeoutSec 5
if ($health.app -ne 'agt-command-center' -or -not $health.ok) { throw 'Health check failed.' }
Write-Output 'AGT is ready: http://127.0.0.1:8767'
if ($Wallpaper -or $WeDir) { Write-Output 'Select AGT in Wallpaper Engine > Installed to apply it to your preferred monitor.' }
