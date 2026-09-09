param(
    [string]$ContainerName = "estateflow-postgres16-local",
    [string]$VolumeName = "estateflow-postgres16-local-data",
    [int]$Port = 55436
)

$ErrorActionPreference = "Stop"
$projectRoot = Split-Path -Parent $PSScriptRoot
$envPath = Join-Path $projectRoot ".env"

if (-not (Test-Path -LiteralPath $envPath)) {
    throw "Missing $envPath. Copy .env.example to .env and configure credentials first."
}

$envMap = @{}
Get-Content -LiteralPath $envPath | ForEach-Object {
    if ($_ -match '^\s*([A-Za-z_][A-Za-z0-9_]*)\s*=(.*)$') {
        $key = $Matches[1]
        $value = $Matches[2].Trim()
        if (
            ($value.StartsWith('"') -and $value.EndsWith('"')) -or
            ($value.StartsWith("'") -and $value.EndsWith("'"))
        ) {
            $value = $value.Substring(1, $value.Length - 2)
        }
        $envMap[$key] = $value
    }
}

$dbUser = $envMap["DB_USER"]
$dbPassword = $envMap["DB_PASSWORD"]
$dbName = $envMap["DB_NAME"]
if (
    [string]::IsNullOrWhiteSpace($dbUser) -or
    [string]::IsNullOrWhiteSpace($dbPassword) -or
    [string]::IsNullOrWhiteSpace($dbName)
) {
    throw "DB_USER, DB_PASSWORD, and DB_NAME must be configured in .env."
}

$existing = docker ps -a --filter "name=^$ContainerName$" --format "{{.Names}}"
if ($LASTEXITCODE -ne 0) {
    throw "Docker daemon is unavailable."
}

if ($existing) {
    $image = docker inspect --format "{{.Config.Image}}" $ContainerName
    if ($image -ne "postgres:16") {
        throw "Container '$ContainerName' exists with image '$image'; expected postgres:16."
    }
    $state = docker inspect --format "{{.State.Status}}" $ContainerName
    if ($state -eq "paused") {
        docker unpause $ContainerName | Out-Null
    } elseif ($state -ne "running") {
        docker start $ContainerName | Out-Null
    }
} else {
    docker run -d `
        --name $ContainerName `
        --restart unless-stopped `
        -p "127.0.0.1:${Port}:5432" `
        -v "${VolumeName}:/var/lib/postgresql/data" `
        --env "POSTGRES_USER=$dbUser" `
        --env "POSTGRES_PASSWORD=$dbPassword" `
        --env "POSTGRES_DB=$dbName" `
        --env "POSTGRES_INITDB_ARGS=--auth-host=scram-sha-256" `
        --health-cmd "pg_isready -U $dbUser -d $dbName" `
        --health-interval 3s `
        --health-timeout 3s `
        --health-retries 20 `
        postgres:16 | Out-Null
    if ($LASTEXITCODE -ne 0) {
        throw "Failed to create '$ContainerName'."
    }
}

$deadline = (Get-Date).AddSeconds(60)
do {
    $health = docker inspect `
        --format "{{if .State.Health}}{{.State.Health.Status}}{{else}}{{.State.Status}}{{end}}" `
        $ContainerName
    if ($health -eq "healthy") {
        break
    }
    Start-Sleep -Seconds 2
} while ((Get-Date) -lt $deadline)

if ($health -ne "healthy") {
    throw "PostgreSQL container '$ContainerName' did not become healthy (status: $health)."
}

Write-Output "$ContainerName is healthy on 127.0.0.1:$Port."
