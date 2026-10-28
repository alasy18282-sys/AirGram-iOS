# Sync RSA public key from Python server into desktop client source.
# Run after changing keys/server_rsa.key, then rebuild the client.

$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent $PSScriptRoot
$py = 'python'
if (Test-Path 'E:\TBuild\ThirdParty\python\Scripts\python.exe') {
    $tp = 'E:\TBuild\ThirdParty\python\Scripts\python.exe'
    & $tp -c "import sys" 2>$null
    if ($LASTEXITCODE -eq 0) { $py = $tp }
}

Push-Location $root
$output = & $py tools\export_rsa_key.py
Pop-Location

$block = ($output -split 'Use PKCS#1 block for BEGIN RSA PUBLIC KEY \(Telegram Desktop\):', 2)[1]
if (-not $block) { Write-Error 'Failed to export PKCS#1 key block' }

$testBlock = $block.Trim() -replace 'kPublicRSAKeys', 'kTestPublicRSAKeys'
$publicBlock = $block.Trim()
$combined = "$testBlock`n`n$publicBlock"

$dcOptionsFile = Join-Path $root '..\mytelegram\Telegram\SourceFiles\mtproto\mtproto_dc_options.cpp'
$content = Get-Content $dcOptionsFile -Raw
$pattern = '(?s)const char\* kTestPublicRSAKeys\[\] = \{.*?-----END (?:RSA )?PUBLIC KEY-----" \};\s*const char\* kPublicRSAKeys\[\] = \{.*?-----END (?:RSA )?PUBLIC KEY-----" \};'
if ($content -notmatch $pattern) {
    Write-Error "RSA key block not found in $dcOptionsFile"
}

$content = [regex]::Replace($content, $pattern, $combined)
Set-Content $dcOptionsFile $content -NoNewline
Write-Host "Updated RSA public key in $dcOptionsFile"
Write-Host "Rebuild client: mytelegram\rebuild_client.bat"
