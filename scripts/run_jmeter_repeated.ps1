param(
    [Parameter(Mandatory = $true)]
    [string]$Plan,

    [Parameter(Mandatory = $true)]
    [string]$HostName,

    [Parameter(Mandatory = $true)]
    [int]$Port,

    [Parameter(Mandatory = $true)]
    [int]$Users,

    [Parameter(Mandatory = $true)]
    [int]$RampUp,

    [Parameter(Mandatory = $true)]
    [int]$Duration,

    [Parameter(Mandatory = $true)]
    [int]$Runs,

    [string]$JMeterHome = $(if ($env:JMETER_HOME) { $env:JMETER_HOME } else { Join-Path $HOME "apache-jmeter-5.6.3" }),
    [string]$Workload = "workload",
    [string]$OutputDir = ".\\results",
    [int]$PauseSeconds = 10,
    [switch]$Summary,
    [string]$PythonExe = "",
    [string]$SummarizeScript = ""
)

$ErrorActionPreference = "Stop"

function Resolve-RequiredPath {
    param(
        [Parameter(Mandatory = $true)]
        [string]$PathValue,
        [Parameter(Mandatory = $true)]
        [string]$Label
    )

    try {
        return (Resolve-Path -LiteralPath $PathValue).Path
    }
    catch {
        throw "$Label not found: $PathValue"
    }
}

function Resolve-PythonCommand {
    param(
        [string]$RequestedCommand
    )

    if ($RequestedCommand) {
        return $RequestedCommand
    }

    foreach ($candidate in @("py", "python", "python3")) {
        $command = Get-Command $candidate -ErrorAction SilentlyContinue
        if ($command -and -not $command.Source.Contains("WindowsApps")) {
            return $candidate
        }
    }

    throw "No usable Python command was found. Re-run with -PythonExe <path-to-python.exe>."
}

$scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
if (-not $SummarizeScript) {
    $SummarizeScript = Join-Path $scriptDir "summarize_jtl.py"
}

$planPath = Resolve-RequiredPath -PathValue $Plan -Label "Plan"
$jmeterHomePath = Resolve-RequiredPath -PathValue $JMeterHome -Label "JMeter home"
$jmeterJar = Join-Path $jmeterHomePath "bin\\ApacheJMeter.jar"

if (-not (Test-Path -LiteralPath $jmeterJar)) {
    throw "JMeter jar not found: $jmeterJar"
}

$outputDirFull = [System.IO.Path]::GetFullPath($OutputDir)
New-Item -ItemType Directory -Force -Path $outputDirFull | Out-Null

$timestamp = (Get-Date).ToUniversalTime().ToString("yyyyMMddTHHmmssZ")
$metaFile = Join-Path $outputDirFull "${Workload}_${timestamp}_runs.csv"
"run,workload,host,port,users,ramp_up,duration,started_utc,jtl_file,meta_file" | Set-Content -LiteralPath $metaFile

$jtlFiles = New-Object System.Collections.Generic.List[string]

for ($run = 1; $run -le $Runs; $run++) {
    $runStamp = (Get-Date).ToUniversalTime().ToString("yyyyMMddTHHmmssZ")
    $runBase = "{0}_{1}_run{2:d2}" -f $Workload, $timestamp, $run
    $jtlFile = Join-Path $outputDirFull ($runBase + ".jtl")
    $runMeta = Join-Path $outputDirFull ($runBase + ".txt")

    @(
        "workload=$Workload"
        "run=$run"
        "host=$HostName"
        "port=$Port"
        "users=$Users"
        "ramp_up=$RampUp"
        "duration=$Duration"
        "started_utc=$runStamp"
        "plan=$planPath"
        "jmeter_home=$jmeterHomePath"
        "jtl_file=$jtlFile"
    ) | Set-Content -LiteralPath $runMeta

    Write-Host ("Starting run {0}/{1}: {2}" -f $run, $Runs, $runBase)

    Push-Location $jmeterHomePath
    try {
        & java -jar "bin/ApacheJMeter.jar" `
            -t $planPath `
            -Jhostname $HostName `
            -Jport $Port `
            -JnumUser $Users `
            -JrampUp $RampUp `
            -Jduration $Duration `
            -l $jtlFile `
            -n
    }
    finally {
        Pop-Location
    }

    if ($LASTEXITCODE -ne 0) {
        throw "JMeter failed on run $run with exit code $LASTEXITCODE"
    }

    "{0},{1},{2},{3},{4},{5},{6},{7},{8},{9}" -f `
        $run, $Workload, $HostName, $Port, $Users, $RampUp, $Duration, $runStamp, $jtlFile, $runMeta |
        Add-Content -LiteralPath $metaFile

    $jtlFiles.Add($jtlFile) | Out-Null

    if ($run -lt $Runs -and $PauseSeconds -gt 0) {
        Start-Sleep -Seconds $PauseSeconds
    }
}

if ($Summary) {
    $summarizePath = Resolve-RequiredPath -PathValue $SummarizeScript -Label "Summarize script"
    $pythonCommand = Resolve-PythonCommand -RequestedCommand $PythonExe
    & $pythonCommand $summarizePath @($jtlFiles.ToArray())
    if ($LASTEXITCODE -ne 0) {
        throw "Summary command failed with exit code $LASTEXITCODE"
    }
}

Write-Host "Results written to: $outputDirFull"
Write-Host "Run metadata: $metaFile"
