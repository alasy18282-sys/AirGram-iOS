# update_dc_ip.ps1
# Автоматически находит локальный IP сети и заменяет его в mtproto_dc_options.cpp

$dcOptionsFile = "$PSScriptRoot\..\mytelegram\Telegram\SourceFiles\mtproto\mtproto_dc_options.cpp"

# Получаем все IPv4 адреса (не loopback)
$ips = Get-NetIPAddress -AddressFamily IPv4 | Where-Object {
    $_.IPAddress -ne '127.0.0.1' -and
    $_.PrefixOrigin -ne 'WellKnown'
} | Select-Object -ExpandProperty IPAddress

Write-Host "Найдены IP адреса:"
$i = 0
$ips | ForEach-Object { Write-Host "  [$i] $_"; $i++ }

if ($ips.Count -eq 0) {
    Write-Error "Не найдено ни одного сетевого IP"
    exit 1
}

$choice = 0
if ($ips.Count -gt 1) {
    $choice = Read-Host "Выберите номер IP [0]"
    if ($choice -eq '') { $choice = 0 }
    $choice = [int]$choice
}

$selectedIP = $ips[$choice]
Write-Host "`nВыбран IP: $selectedIP"

# Обновляем файл
if (-Not (Test-Path $dcOptionsFile)) {
    Write-Error "Файл не найден: $dcOptionsFile"
    exit 1
}

$content = Get-Content $dcOptionsFile -Raw

# Заменяем IP в define IpV4 и IpV6
$oldIP = [regex]::Match($content, '#define IpV4 "([^"]+)"').Groups[1].Value
Write-Host "Старый IP: $oldIP"

$content = $content -replace '#define IpV4 "([^"]+)"', "#define IpV4 `"$selectedIP`""
$content = $content -replace '#define IpV6 "([^"]+)"', "#define IpV6 `"$selectedIP`""

Set-Content $dcOptionsFile $content -NoNewline
Write-Host "✅ IP обновлён на $selectedIP в $dcOptionsFile"
Write-Host "`nТеперь пересоберите клиент."
Read-Host "`nНажмите Enter для выхода"
