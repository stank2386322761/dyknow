# 安装 Windows 计划任务 —— 每天 18:30 启动夜间转录
# 以管理员身份运行此脚本

$TaskName = "DyKnow Night Transcribe"
$ScriptPath = "D:\BaiduSyncdisk\workcode\skills\DyKnow\dyknow\night_runner.bat"

# 如果已存在则先删除
$existing = Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
if ($existing) {
    Write-Host "⚠️  已存在同名任务，正在删除..."
    Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false
}

# 创建触发器：每天 18:30
$Trigger = New-ScheduledTaskTrigger -Daily -At 18:30

# 创建动作：运行 bat 脚本
$Action = New-ScheduledTaskAction -Execute $ScriptPath

# 设置：允许在电池模式下运行、隐藏窗口、最高权限
$Settings = New-ScheduledTaskSettingsSet `
    -AllowStartIfOnBatteries `
    -DontStopIfGoingOnBatteries `
    -StartWhenAvailable `
    -RunOnlyIfNetworkAvailable:$false `
    -MultipleInstances IgnoreNew

# 注册任务
Register-ScheduledTask `
    -TaskName $TaskName `
    -Trigger $Trigger `
    -Action $Action `
    -Settings $Settings `
    -RunLevel Highest `
    -Description "每天晚上 18:30 自动开始转录音视频收藏，持续到次日 08:30 自动停止。支持断点续转和失败重试。"

Write-Host "✅ 计划任务已安装"
Write-Host "   任务名: $TaskName"
Write-Host "   时间: 每天 18:30"
Write-Host "   脚本: $ScriptPath"
Write-Host ""
Write-Host "验证: 运行 'taskschd.msc' 打开任务计划程序查看"
Write-Host "删除: Unregister-ScheduledTask -TaskName '$TaskName'"
