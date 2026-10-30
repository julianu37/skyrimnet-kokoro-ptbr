$ErrorActionPreference = "SilentlyContinue"

$stopped = @()

# 1) Stop whatever owns the configured port, but only if it looks like the adapter.
$port = 8020
if (Test-Path (Join-Path $PSScriptRoot "..\config.yaml")) {
    $match = Select-String -Path (Join-Path $PSScriptRoot "..\config.yaml") -Pattern "^\s*port:\s*(\d+)" | Select-Object -First 1
    if ($match) { $port = [int]$match.Matches[0].Groups[1].Value }
}

$conn = Get-NetTCPConnection -LocalPort $port -State Listen
if ($conn) {
    foreach ($processId in ($conn.OwningProcess | Select-Object -Unique)) {
        $proc = Get-CimInstance Win32_Process -Filter "ProcessId=$processId"
        if ($proc -and $proc.Name -eq "python.exe" -and $proc.CommandLine -like "*server.py*") {
            Stop-Process -Id $processId -Force
            $stopped += $processId
        } else {
            Write-Host "Port $port is held by PID $processId ($($proc.Name)); not stopping it."
        }
    }
}

# 2) Stop the venv launcher (its child may hold the port).
Get-CimInstance Win32_Process |
    Where-Object { $_.CommandLine -like "*kokoro-skyrimnet*server.py*" } |
    ForEach-Object {
        Stop-Process -Id $_.ProcessId -Force
        $stopped += $_.ProcessId
    }

$stopped = $stopped | Select-Object -Unique
if ($stopped) {
    $stopped | ForEach-Object { Write-Host "stopped PID $_" }
} else {
    Write-Host "No adapter process found."
}
