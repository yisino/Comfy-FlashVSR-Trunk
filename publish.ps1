<#
.SYNOPSIS  Comfy-FlashVSR-Trunk 一键发布 / 多平台同步脚本
.DESCRIPTION
  - 幂等配置 3 个平台 remote（gitee / github / codeup），URL 见下方 $remotes。
  - 向每个 remote 推送所有分支 + 标签（mirror 式同步）。
  - -Deploy 开关：把本仓库同步进本地 ComfyUI custom_nodes（解决源/部署手动 cp 不一致）。
  - 在你本机正常终端运行（需 SSH agent 已加载密钥、known_hosts 已就绪）。
.EXAMPLE
  .\publish.ps1                 # 仅推送三个平台
  .\publish.ps1 -Deploy         # 推送 + 同步到本地 ComfyUI
#>
param(
  [switch]$Deploy,
  [string]$ComfyCustomNodes = "D:/Comfy-Desktop/ComfyUI-Installs/NVIDIA/ComfyUI/custom_nodes"
)

$ErrorActionPreference = "Stop"
$repo = "Comfy-FlashVSR-Trunk"

# ── remote 配置（如命名空间需改动，改这里即可）──────────────────────
$remotes = @{
  # Gitee（替换原内网 GitLab）：命名空间 simino（本机 id_ed25519 已授权）
  origin = "git@gitee.com:simino/$repo.git"
  github = "git@github.com:yisino/$repo.git"
  codeup = "git@codeup.aliyun.com:5f28c467769820a3e817fc05/$repo.git"
}

# 幂等设置 remote
foreach ($name in $remotes.Keys) {
  $url = $remotes[$name]
  $existing = git remote get-url $name 2>$null
  if ($existing -ne $url) {
    if ($existing) { git remote set-url $name $url }
    else           { git remote add $name $url }
  }
  Write-Host ("remote[{0}] -> {1}" -f $name, $url)
}

# 推送所有分支 + 标签
foreach ($name in $remotes.Keys) {
  Write-Host ("`n>> pushing to {0} ..." -f $name)
  git push $name --all
  git push $name --tags
}

# 可选：部署到本地 ComfyUI（与发布仓库保持一致）
if ($Deploy) {
  $dest = Join-Path $ComfyCustomNodes $repo
  Write-Host ("`n>> deploying to {0}" -f $dest)
  if (-not (Test-Path $dest)) { New-Item -ItemType Directory -Path $dest | Out-Null }
  # 排除 .git / __pycache__，保持部署目录为纯插件
  robocopy $PSScriptRoot $dest /E /XD ".git" "__pycache__" /XF "*.pyc" /NFL /NDL /NJH
  if ($LASTEXITCODE -ge 8) { throw "robocopy failed (exit $LASTEXITCODE)" }
  Write-Host "deployed."
}

Write-Host "`nDONE. 三平台 (gitee/github/codeup) remote 已配置并推送；节点位于 ComfyUI 分类 🧪AILab/⚡FlashVSR/Trunk"
