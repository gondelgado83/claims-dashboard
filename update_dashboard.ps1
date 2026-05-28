$ErrorActionPreference = "Stop"
$DIR = "C:\Users\gonde\OneDrive - Brio Management\Desktop\CLAUDE\claims-dashboard"
Set-Location $DIR

Write-Host "$(Get-Date -Format 'HH:mm') Generando Claims Dashboard..."
python build_dashboard.py

if ($LASTEXITCODE -eq 0) {
    git add docs/index.html
    $changed = git status --porcelain
    if ($changed) {
        git commit -m "Dashboard update $(Get-Date -Format 'yyyy-MM-dd HH:mm')"
        git push
        Write-Host "$(Get-Date -Format 'HH:mm') Publicado OK"
    } else {
        Write-Host "$(Get-Date -Format 'HH:mm') Sin cambios"
    }
} else {
    Write-Host "ERROR en Python - revisar conexion"
    exit 1
}
