param(
    [switch]$Instalar,
    [switch]$Quitar
)

if ($Instalar -eq $Quitar) {
    throw "Indica exactamente una opción: -Instalar o -Quitar."
}

$startup = [Environment]::GetFolderPath('Startup')
$linkPath = Join-Path $startup 'Uso y limites.lnk'
$scriptFolder = Split-Path -Parent $MyInvocation.MyCommand.Path
$wscriptPath = Join-Path $env:SystemRoot 'System32\wscript.exe'
$arguments = '"' + (Join-Path $scriptFolder 'Abrir.vbs') + '"'

$shell = New-Object -ComObject WScript.Shell
if (Test-Path -LiteralPath $linkPath) {
    $existing = $shell.CreateShortcut($linkPath)
    $owned = ($existing.TargetPath -ieq $wscriptPath) -and ($existing.Arguments -ieq $arguments)
    if (-not $owned) {
        throw "Ya existe un acceso directo con ese nombre que no pertenece a esta instalación."
    }
} else {
    $owned = $false
}

if ($Quitar) {
    if ($owned) {
        Remove-Item -LiteralPath $linkPath
    }
    exit 0
}

if (-not $owned) {
    $shortcut = $shell.CreateShortcut($linkPath)
    $shortcut.TargetPath = $wscriptPath
    $shortcut.Arguments = $arguments
    $shortcut.WorkingDirectory = $scriptFolder
    $shortcut.Save()
}
