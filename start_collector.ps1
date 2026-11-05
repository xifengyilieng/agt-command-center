param([switch]$OpenBrowser)
$ErrorActionPreference = 'Stop'
$appRoot = $PSScriptRoot
$dashboardUrl = 'http://127.0.0.1:8767'
$serverFile = Join-Path $appRoot 'server.py'
. (Join-Path $appRoot 'python_launcher.ps1')
$pythonPath = Get-AgtPython
& $pythonPath (Join-Path $appRoot 'build_screen.py')
if ($LASTEXITCODE -ne 0) { throw 'AGT build failed.' }
$healthy = $false
try {
    $health = Invoke-RestMethod -Uri "$dashboardUrl/api/health" -TimeoutSec 2
    $healthy = $health.app -eq 'agt-command-center' -and $health.ok -and $health.version -ge 10
} catch {}
if (-not $healthy) {
    $ownedServers = Get-CimInstance Win32_Process | Where-Object {
        $_.Name -match '^python(w)?\.exe$' -and ([string]$_.CommandLine).Contains($serverFile)
    }
    foreach ($instance in $ownedServers) { Stop-Process -Id $instance.ProcessId -ErrorAction Stop }
    $serverArguments = @(('"{0}"' -f $serverFile), '--port', '8767')
    Start-Process -FilePath $pythonPath -ArgumentList $serverArguments -WorkingDirectory $appRoot -WindowStyle Hidden -RedirectStandardOutput (Join-Path $appRoot 'server.log') -RedirectStandardError (Join-Path $appRoot 'server-error.log')
    for ($attempt=0; $attempt -lt 30; $attempt++) {
        Start-Sleep -Milliseconds 300
        try {
            $health = Invoke-RestMethod -Uri "$dashboardUrl/api/health" -TimeoutSec 1
            if ($health.app -eq 'agt-command-center' -and $health.ok -and $health.version -ge 10) { $healthy=$true; break }
        } catch {}
    }
}
if (-not $healthy) { throw 'AGT did not start. Check server-error.log or port 8767.' }
if ($OpenBrowser) { Start-Process "$dashboardUrl/?v=3" }
