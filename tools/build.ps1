$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path -Parent $PSScriptRoot
$compiler = Join-Path $env:WINDIR 'Microsoft.NET\Framework64\v4.0.30319\csc.exe'
if (-not (Test-Path $compiler)) { throw 'Missing .NET Framework 4.x C# compiler.' }
if (-not (Test-Path (Join-Path $projectRoot 'runtime\pythonw.exe'))) { throw 'Run tools/prepare_runtime.py first.' }
& (Join-Path $projectRoot 'runtime\python.exe') (Join-Path $PSScriptRoot 'build_locales.py')
if ($LASTEXITCODE -ne 0) { throw 'Language catalog build failed.' }
Add-Type -AssemblyName System.Drawing
$bitmap = New-Object System.Drawing.Bitmap 64,64
$graphics = [System.Drawing.Graphics]::FromImage($bitmap)
$graphics.SmoothingMode = [System.Drawing.Drawing2D.SmoothingMode]::AntiAlias
$graphics.Clear([System.Drawing.Color]::FromArgb(37,92,75))
$pen = New-Object System.Drawing.Pen ([System.Drawing.Color]::FromArgb(236,241,221)),3
$points = [System.Drawing.Point[]]@((New-Object System.Drawing.Point 16,23),(New-Object System.Drawing.Point 32,14),(New-Object System.Drawing.Point 48,23),(New-Object System.Drawing.Point 48,42),(New-Object System.Drawing.Point 32,51),(New-Object System.Drawing.Point 16,42))
$graphics.DrawPolygon($pen,$points)
$graphics.DrawLine($pen,16,23,32,32)
$graphics.DrawLine($pen,32,32,48,23)
$graphics.DrawLine($pen,32,32,32,51)
$graphics.DrawLine($pen,24,19,40,28)
$iconPath = Join-Path $projectRoot 'launcher\app.ico'
$icon = [System.Drawing.Icon]::FromHandle($bitmap.GetHicon())
$stream = [System.IO.File]::Create($iconPath)
$icon.Save($stream)
$stream.Dispose()
$icon.Dispose()
$graphics.Dispose()
$pen.Dispose()
$bitmap.Dispose()
$output = Join-Path $projectRoot 'VaMPackageManager.exe'
& $compiler /nologo /target:winexe /optimize+ /platform:anycpu /reference:System.Windows.Forms.dll "/win32icon:$iconPath" "/out:$output" (Join-Path $projectRoot 'launcher\Launcher.cs')
if ($LASTEXITCODE -ne 0) { throw 'Launcher build failed.' }
Write-Host "Built: $output"
