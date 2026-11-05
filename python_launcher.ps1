# Shared by setup and launchers. Resolve the interpreter rather than a Store alias.
function Get-AgtPython {
    foreach ($name in @('python', 'py', 'python3')) {
        $command = Get-Command $name -ErrorAction SilentlyContinue
        if (-not $command) { continue }
        $prefix = @()
        if ($name -eq 'py') { $prefix = @('-3') }
        try {
            $result = & $command.Source @prefix -c "import sys; sys.exit('Python 3.10+ required') if sys.version_info < (3,10) else print(sys.executable)" 2>$null
            if ($LASTEXITCODE -eq 0 -and $result -and (Test-Path -LiteralPath ([string]$result))) {
                return ([string]$result).Trim()
            }
        } catch { }
    }
    throw 'Python 3.10+ was not found. Install Python, reopen the terminal, then run setup.ps1 again.'
}
