# 在 Windows 上把监控装成「登录即启动、后台运行、不弹窗口」的计划任务。
# 用法（在项目根目录的 PowerShell 里）：
#   powershell -ExecutionPolicy Bypass -File deploy\install_windows.ps1
#   powershell -ExecutionPolicy Bypass -File deploy\install_windows.ps1 -TaskName 我的任务名
# 任务名默认 JiPiaoMonitor，必须和 config.yaml 里 service.windows_task_name 一致。
param(
    [string]$TaskName = "JiPiaoMonitor"
)
$ErrorActionPreference = "Stop"
# 带域名的账户名，兼容微软账户/域账户
$Me = [Security.Principal.WindowsIdentity]::GetCurrent().Name

$Root = Split-Path -Parent $PSScriptRoot
$Pyw  = Join-Path $Root ".venv\Scripts\pythonw.exe"
$Main = Join-Path $Root "main.py"

if (-not (Test-Path $Pyw)) {
    Write-Host "找不到 $Pyw ，请先按 README 建虚拟环境并安装依赖" -ForegroundColor Red
    exit 1
}
if (-not (Test-Path (Join-Path $Root "config.yaml"))) {
    Write-Host "找不到 config.yaml ，请先 copy config.example.yaml config.yaml 并修改" -ForegroundColor Red
    exit 1
}
New-Item -ItemType Directory -Force -Path (Join-Path $Root "logs") | Out-Null

# pythonw = 无控制台窗口；日志写到 logs\monitor.log
$Action   = New-ScheduledTaskAction -Execute $Pyw -Argument "`"$Main`"" -WorkingDirectory $Root
$Trigger  = New-ScheduledTaskTrigger -AtLogOn -User $Me
$Settings = New-ScheduledTaskSettingsSet `
    -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries `
    -StartWhenAvailable `
    -ExecutionTimeLimit ([TimeSpan]::Zero) `
    -RestartCount 3 -RestartInterval (New-TimeSpan -Minutes 1) `
    -MultipleInstances IgnoreNew
$Principal = New-ScheduledTaskPrincipal -UserId $Me -LogonType Interactive -RunLevel Limited

# 已存在就先删掉重建（改了配置/日期后重新运行本脚本即可）
if (Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue) {
    Stop-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
    Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false
}
Register-ScheduledTask -TaskName $TaskName -Action $Action -Trigger $Trigger `
    -Settings $Settings -Principal $Principal `
    -Description "机票降价监控（jipiao-monitor）" | Out-Null
Start-ScheduledTask -TaskName $TaskName

Write-Host "已安装并启动计划任务：$TaskName" -ForegroundColor Green
Write-Host "看日志：Get-Content `"$Root\logs\monitor.log`" -Tail 30 -Wait"
