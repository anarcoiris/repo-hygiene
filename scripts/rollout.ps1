param (
    [Parameter(Mandatory=$true)]
    [string]$TargetRepoPath
)

# 1. Verificar que el directorio destino existe
if (-not (Test-Path $TargetRepoPath -PathType Container)) {
    Write-Error "El directorio de destino '$TargetRepoPath' no existe."
    exit 1
}

# 2. Definir rutas origen y destino
$SourceDir = Split-Path -Parent $PSScriptRoot
$DestHygieneDir = Join-Path $TargetRepoPath ".repo-hygiene"
$DestWorkflowDir = Join-Path $TargetRepoPath ".github\workflows"

Write-Host "Iniciando rollout de repo-hygiene a '$TargetRepoPath'..."

# 3. Crear directorios destino si no existen
if (-not (Test-Path $DestHygieneDir -PathType Container)) {
    New-Item -ItemType Directory -Force -Path $DestHygieneDir | Out-Null
}
if (-not (Test-Path $DestWorkflowDir -PathType Container)) {
    New-Item -ItemType Directory -Force -Path $DestWorkflowDir | Out-Null
}

# 4. Copiar directorios clave (agents, scripts, templates) y config.yaml
$ItemsToCopy = @("agents", "scripts", "templates", "config.yaml")
foreach ($Item in $ItemsToCopy) {
    $SrcPath = Join-Path $SourceDir $Item
    $DstPath = Join-Path $DestHygieneDir $Item
    if (Test-Path $SrcPath) {
        Write-Host "  -> Copiando $Item..."
        Copy-Item -Path $SrcPath -Destination $DstPath -Recurse -Force -Exclude ".venv", "*.pyc", "__pycache__", "rollout.ps1"
    }
}

# 5. Copiar el workflow de GitHub Actions
$SrcWorkflow = Join-Path $SourceDir ".github\workflows\repo-hygiene.yml"
$DstWorkflow = Join-Path $DestWorkflowDir "repo-hygiene.yml"
if (Test-Path $SrcWorkflow) {
    Write-Host "  -> Copiando workflow de GitHub..."
    Copy-Item -Path $SrcWorkflow -Destination $DstWorkflow -Force
}

Write-Host ""
Write-Host "Rollout completado exitosamente para target: $TargetRepoPath"
Write-Host "Pasos siguientes para el repositorio:"
Write-Host "  1. Accede al repositorio: cd '$TargetRepoPath'"
Write-Host "  2. Crea un entorno virtual e instala dependencias:"
Write-Host "     python -m venv .repo-hygiene\.venv"
Write-Host "     .repo-hygiene\.venv\Scripts\pip install pyyaml requests anthropic"
Write-Host "  3. Ejecuta una prueba en seco (dry-run):"
Write-Host "     .repo-hygiene\.venv\Scripts\python .repo-hygiene\scripts\orchestrator.py --schedule daily --dry-run"
