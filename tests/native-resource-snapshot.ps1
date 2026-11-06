param(
    [ValidateSet('baseline','candidate','final')][string]$Label = 'candidate',
    [ValidateRange(2,30)][int]$Samples = 4,
    [ValidateRange(2,30)][int]$IntervalSeconds = 5,
    [switch]$CheckOnly
)
$ErrorActionPreference = 'Stop'
$appRoot = Split-Path -Parent $PSScriptRoot
$serverPath = Join-Path $appRoot 'server.py'
function Get-AgtMonitorIdentity {
    $records = @(Get-CimInstance Win32_Process -Filter "Name='wallpaper32.exe' OR Name='wallpaper64.exe' OR Name='webwallpaper64.exe' OR Name='python.exe'")
    $candidates = @($records | Where-Object {
        $_.Name -eq 'webwallpaper64.exe' -and ([string]$_.CommandLine) -match '(?:^|\s)-cacheId\s+"?monitor0"?(?=\s|$)' -and
        ([string]$_.CommandLine) -notmatch '--type(?:=|\s)'
    })
    $verified = @()
    foreach ($browserRoot in $candidates) {
        if (-not $browserRoot.ExecutablePath) { continue }
        $engineDirectory = Split-Path -Parent (Split-Path -Parent $browserRoot.ExecutablePath)
        $expectedBrowser = Join-Path $engineDirectory 'bin\webwallpaper64.exe'
        if ($browserRoot.ExecutablePath -ine $expectedBrowser) { continue }
        $main = @($records | Where-Object { $_.ProcessId -eq $browserRoot.ParentProcessId -and $_.Name -match '^wallpaper(32|64)\.exe$' -and (Split-Path -Parent $_.ExecutablePath) -ieq $engineDirectory })
        if ($main.Count -ne 1) { continue }
        try {
            # Windows PowerShell 5 otherwise decodes UTF-8 wallpaper titles as ANSI,
            # which can corrupt JSON string delimiters before ownership is checked.
            $config = Get-Content -LiteralPath (Join-Path $engineDirectory 'config.json') -Raw -Encoding UTF8 | ConvertFrom-Json
            $userConfig = @($config.PSObject.Properties | Where-Object Name -IEQ $env:USERNAME)[0].Value
            $selectedFile = [string]$userConfig.general.wallpaperconfig.selectedwallpapers.Monitor0.file
            if (-not $selectedFile) { continue }
            $selectedFull = [IO.Path]::GetFullPath($selectedFile)
            $projectDirectory = [IO.Path]::GetFullPath((Join-Path $engineDirectory 'projects\myprojects\AGT-Live-Command'))
            if (-not $selectedFull.StartsWith($projectDirectory+'\',[StringComparison]::OrdinalIgnoreCase)) { continue }
        } catch { continue }
        $ownedIds = @{}; $ownedIds[[int]$browserRoot.ProcessId] = $true
        do {
            $added = $false
            foreach ($record in $records) {
                if ($record.Name -eq 'webwallpaper64.exe' -and $record.ExecutablePath -ieq $expectedBrowser -and
                    $ownedIds.ContainsKey([int]$record.ParentProcessId) -and -not $ownedIds.ContainsKey([int]$record.ProcessId)) {
                    $ownedIds[[int]$record.ProcessId] = $true; $added = $true
                }
            }
        } while ($added)
        $ownedRecords = @($records | Where-Object {
            $ownedIds.ContainsKey([int]$_.ProcessId) -or ($_.Name -eq 'python.exe' -and ([string]$_.CommandLine).Contains($serverPath))
        })
        $verified += [pscustomobject]@{ Monitor='Monitor0'; RootId=[int]$browserRoot.ProcessId; MainId=[int]$main[0].ProcessId; SelectedFile=$selectedFull; ProjectDirectory=$projectDirectory; Records=$ownedRecords }
    }
    if ($verified.Count -ne 1) { throw 'Cannot sample: AGT must be selected on Monitor0 with exactly one verified running monitor0 browser root. Other monitors are excluded.' }
    return $verified[0]
}
$initialIdentity = Get-AgtMonitorIdentity
if ($CheckOnly) {
    $initialIdentity | Select-Object Monitor,RootId,MainId,SelectedFile,@{Name='OwnedProcessIds';Expression={@($_.Records.ProcessId)}} | ConvertTo-Json -Depth 4
    return
}
$lastCpu = @{}
$observations = for ($index=0; $index -lt $Samples; $index++) {
    if ($index -gt 0) { Start-Sleep -Seconds $IntervalSeconds }
    $identity = if ($index -eq 0) { $initialIdentity } else { Get-AgtMonitorIdentity }
    if ($identity.RootId -ne $initialIdentity.RootId -or $identity.MainId -ne $initialIdentity.MainId) { throw 'AGT browser root identity changed during sampling; discard this run and sample the stabilized process tree again.' }
    $roles = @{}
    foreach ($item in $identity.Records) {
        $kind = if ($item.Name -eq 'python.exe') { 'collector' }
            elseif ($item.CommandLine -match '--type=([^ ]+)') { $Matches[1] }
            else { 'browser' }
        $roles[[int]$item.ProcessId] = $kind
    }
    $now = Get-Date
    $processes = foreach ($processKey in $roles.Keys) {
        $instance = Get-Process -Id $processKey -ErrorAction SilentlyContinue
        if ($instance) {
            $previous = $lastCpu[$processKey]
            $cpuOneCorePercent = if ($previous) { [math]::Round(($instance.CPU-$previous.Cpu)/($now-$previous.At).TotalSeconds*100,2) } else { $null }
            $lastCpu[$processKey] = @{ Cpu=$instance.CPU; At=$now }
            [pscustomobject]@{ Id=$processKey; Role=$roles[$processKey]; WorkingMB=[math]::Round($instance.WorkingSet64/1MB,2); PrivateMB=[math]::Round($instance.PrivateMemorySize64/1MB,2); CpuOneCorePercent=$cpuOneCorePercent }
        }
    }
    $os = Get-CimInstance Win32_OperatingSystem
    $disk = Get-CimInstance Win32_PerfFormattedData_PerfDisk_PhysicalDisk | Where-Object Name -NE '_Total' |
        Select-Object Name,PercentIdleTime,DiskReadBytesPersec,DiskWriteBytesPersec,CurrentDiskQueueLength
    $healthSample = try {
        $health = Invoke-RestMethod -Uri 'http://127.0.0.1:8767/api/health' -TimeoutSec 2
        [pscustomobject]@{ Ready=($health.app -eq 'agt-command-center' -and $health.ok -eq $true -and $health.version -ge 11); App=$health.app; Version=$health.version; ControlsConnected=$health.desktopControls.connected; PublishedRendering=$health.desktopControls.rendering; RecoveryPhase=$health.rendererRecovery.phase }
    } catch { [pscustomobject]@{ Ready=$false; Error=$_.Exception.Message } }
    [pscustomobject]@{ At=$now.ToString('o'); VerifiedMonitor=$identity.Monitor; VerifiedRootId=$identity.RootId; RAMUsedPercent=[math]::Round((1-$os.FreePhysicalMemory/$os.TotalVisibleMemorySize)*100,2); Processes=@($processes); PhysicalDisks=@($disk); Health=$healthSample }
}
$report = [pscustomobject]@{ Label=$Label; Samples=$Samples; IntervalSeconds=$IntervalSeconds; Ownership=[pscustomobject]@{ Monitor=$initialIdentity.Monitor; RootId=$initialIdentity.RootId; MainId=$initialIdentity.MainId; SelectedFile=$initialIdentity.SelectedFile; ProjectDirectory=$initialIdentity.ProjectDirectory }; Note='AGT Monitor0 root and its verified webwallpaper descendants only, plus this repository collector. Other monitor wallpapers are excluded. Selection and root identity are checked each sample; a missing or changed root aborts the run. CPU is percent of one core. Physical disk counters exclude process IPC. PublishedRendering comes from authenticated wallpaper page publication through /api/health, never a simulated clock. ControlsConnected must be true for a fresh publication.'; Observations=@($observations) }
$dateStamp = Get-Date -Format yyyyMMdd
$artifactDirectory = Join-Path $appRoot 'artifacts'
New-Item -ItemType Directory -Path $artifactDirectory -Force | Out-Null
$destination = Join-Path $appRoot ('artifacts\smoothness-native-{0}-{1}.json' -f $Label,$dateStamp)
$report | ConvertTo-Json -Depth 9 | Set-Content -LiteralPath $destination -Encoding utf8
Write-Output ('Saved {0} native resource observations to {1}' -f $Samples,$destination)
