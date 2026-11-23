$ErrorActionPreference = 'Stop'

Write-Host "Obteniendo credenciales de Git Credential Manager..."
$gitCredInput = "protocol=https`nhost=github.com"
$credOutput = $gitCredInput | git credential fill
$tokenLine = ($credOutput | Where-Object { $_ -match '^password=' })
if (-not $tokenLine) {
    throw "No se pudo recuperar el token de GitHub desde Git Credential Manager."
}
$token = $tokenLine.Substring(9).Trim()

$headers = @{
    'Authorization' = "Bearer $token"
    'User-Agent'    = 'Valhalla-Release-Uploader'
    'Accept'        = 'application/vnd.github+json'
}

$repoOwner = "Licenciao"
$repoName  = "Valhalla-POS"
$tagName   = "v1.0.0"

Write-Host "Verificando si la release $tagName ya existe..."
$release = $null
try {
    $release = Invoke-RestMethod -Uri "https://api.github.com/repos/$repoOwner/$repoName/releases/tags/$tagName" -Headers $headers -Method Get
    Write-Host "La release ya existe con ID: $($release.id)"
} catch {
    Write-Host "La release no existe. Creando release $tagName..."
    $bodyJson = @{
        tag_name         = $tagName
        target_commitish = "main"
        name             = "Valhalla POS v1.0.0 — Versión Oficial Móvil y Servidor"
        body             = "Primera versión oficial del ecosistema Valhalla POS.`n`n### 📦 Descarga directa para Android`nDescarga el archivo adjunto **valhalla-movil.apk** e instálalo directamente en cualquier teléfono o tablet Android para operar en piso o almacén con soporte offline."
        draft            = $false
        prerelease       = $false
    } | ConvertTo-Json

    $release = Invoke-RestMethod -Uri "https://api.github.com/repos/$repoOwner/$repoName/releases" -Headers $headers -Method Post -Body $bodyJson -ContentType "application/json; charset=utf-8"
    Write-Host "Release creada exitosamente con ID: $($release.id)"
}

$apkPath = "G:\IAs\valhalla-movil.apk"
if (-not (Test-Path $apkPath)) {
    throw "No se encontró el archivo $apkPath."
}

$apkName = [System.IO.Path]::GetFileName($apkPath)
Write-Host "Verificando si el asset $apkName ya está subido..."
$existingAsset = $release.assets | Where-Object { $_.name -eq $apkName }
if ($existingAsset) {
    Write-Host "El asset ya existe. Eliminándolo para resubir la versión más reciente..."
    Invoke-RestMethod -Uri "https://api.github.com/repos/$repoOwner/$repoName/releases/assets/$($existingAsset.id)" -Headers $headers -Method Delete
    Write-Host "Asset anterior eliminado."
}

Write-Host "Subiendo $apkPath ($([math]::Round((Get-Item $apkPath).Length / 1MB, 2)) MB) a GitHub Releases..."
$uploadUri = "https://uploads.github.com/repos/$repoOwner/$repoName/releases/$($release.id)/assets?name=$apkName"

$uploadHeaders = @{
    'Authorization' = "Bearer $token"
    'User-Agent'    = 'Valhalla-Release-Uploader'
    'Content-Type'  = 'application/vnd.android.package-archive'
}

$uploadResult = Invoke-RestMethod -Uri $uploadUri -Headers $uploadHeaders -Method Post -InFile $apkPath

Write-Host "============================================================"
Write-Host "✔ APK SUBIDA CON ÉXITO A GITHUB RELEASES"
Write-Host "URL de la Release: $($release.html_url)"
Write-Host "URL de descarga directa: $($uploadResult.browser_download_url)"
Write-Host "============================================================"
