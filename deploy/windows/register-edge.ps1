param(
    [Parameter(Mandatory=$true)][string]$Repository,
    [Parameter(Mandatory=$true)][string]$Python,
    [string]$TaskName = 'USAGI WeChat Edge'
)
$repoPath = (Resolve-Path -LiteralPath $Repository).Path
$pythonPath = (Resolve-Path -LiteralPath $Python).Path
$configPath = Join-Path $repoPath 'apps\wechat-edge\config.json'
if (-not (Test-Path -LiteralPath $configPath -PathType Leaf)) { throw 'Create edge config.json first' }
$identity = [Security.Principal.WindowsIdentity]::GetCurrent().Name
$taskAction = New-ScheduledTaskAction -Execute $pythonPath -Argument ('-m wechat_edge.main --config "{0}"' -f $configPath) -WorkingDirectory $repoPath
$taskTrigger = New-ScheduledTaskTrigger -AtLogOn -User $identity
$taskPrincipal = New-ScheduledTaskPrincipal -UserId $identity -LogonType Interactive -RunLevel Limited
$taskSettings = New-ScheduledTaskSettingsSet -RestartCount 3 -RestartInterval (New-TimeSpan -Minutes 1) -ExecutionTimeLimit ([TimeSpan]::Zero) -MultipleInstances IgnoreNew
Register-ScheduledTask -TaskName $TaskName -Action $taskAction -Trigger $taskTrigger -Principal $taskPrincipal -Settings $taskSettings
