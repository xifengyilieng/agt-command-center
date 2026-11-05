$ErrorActionPreference='Stop'
$startupDirectory=[Environment]::GetFolderPath('Startup')
$shortcutPath=Join-Path $startupDirectory 'AGT Task Collector.lnk'
$shell=New-Object -ComObject WScript.Shell
$shortcut=$shell.CreateShortcut($shortcutPath)
$shortcut.TargetPath=Join-Path $env:SystemRoot 'System32\WindowsPowerShell\v1.0\powershell.exe'
$collectorScript=Join-Path $PSScriptRoot 'start_collector.ps1'
$shortcut.Arguments='-NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File "'+$collectorScript+'"'
$shortcut.WorkingDirectory=$PSScriptRoot
$shortcut.WindowStyle=7
$shortcut.Description='Local AGT task collector for the Wallpaper Engine live desktop.'
$shortcut.Save()
Write-Output $shortcutPath
