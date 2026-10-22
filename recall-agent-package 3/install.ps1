param([Parameter(ValueFromRemainingArguments=$true)][string[]]$RecallArguments)
$ErrorActionPreference = "Stop"
$RecallProbe = 'import sys,sqlite3; assert sys.version_info >= (3,9); sqlite3.connect(":memory:").execute("CREATE VIRTUAL TABLE probe USING fts5(text)"); print(sys.executable)'

function Find-RecallPython {
    $RecallCandidates = @()
    if ($env:RECALL_PYTHON) { $RecallCandidates += @{ Path = $env:RECALL_PYTHON; Args = @() } }
    $RecallLauncher = Get-Command py -ErrorAction SilentlyContinue
    if ($RecallLauncher) { $RecallCandidates += @{ Path = $RecallLauncher.Source; Args = @("-3") } }
    foreach ($RecallName in @("python", "python3")) {
        $RecallCommand = Get-Command $RecallName -ErrorAction SilentlyContinue
        if ($RecallCommand -and $RecallCommand.Source -notlike "*\WindowsApps\*") {
            $RecallCandidates += @{ Path = $RecallCommand.Source; Args = @() }
        }
    }
    foreach ($RecallVersion in @("314", "313", "312", "311", "310", "39")) {
        $RecallPath = Join-Path $env:LOCALAPPDATA "Programs\Python\Python$RecallVersion\python.exe"
        if (Test-Path -LiteralPath $RecallPath) { $RecallCandidates += @{ Path = $RecallPath; Args = @() } }
    }
    foreach ($RecallCandidate in $RecallCandidates) {
        try {
            $RecallOutput = & $RecallCandidate.Path @($RecallCandidate.Args) -B -c $RecallProbe 2>$null
            if ($LASTEXITCODE -eq 0 -and $RecallOutput) { return [string]($RecallOutput | Select-Object -Last 1) }
        } catch { }
    }
    return $null
}

$RecallPython = Find-RecallPython
if (-not $RecallPython -and $RecallArguments -notcontains "--plan" -and $env:RECALL_SKIP_RUNTIME_INSTALL -ne "1") {
    $RecallWinget = Get-Command winget -ErrorAction SilentlyContinue
    if ($RecallWinget) {
        & $RecallWinget.Source install --exact --id Python.Python.3.13 --scope user --silent --accept-package-agreements --accept-source-agreements
        if ($LASTEXITCODE -ne 0) { throw "Python installation failed; install Python from python.org and rerun." }
        $RecallPython = Find-RecallPython
    }
}
if (-not $RecallPython) {
    Write-Error "Recall needs Python 3.9+ with SQLite FTS5. Install Python from python.org, then rerun. An Agent can set RECALL_PYTHON to an existing compatible interpreter."
    exit 3
}
& $RecallPython -B (Join-Path $PSScriptRoot "recallctl.py") install @RecallArguments
exit $LASTEXITCODE
