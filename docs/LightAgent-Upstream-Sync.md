# LightAgent 上游同步规则

本项目维护自己的 `LightAgent/` 源码目录。`yideng966/LightAgent` 只作为上游参考，不作为本项目的推送目标。

## 固定原则

- 不要重新把 `LightAgent/` 改成 Git submodule。
- 不要在 `LightAgent/` 目录里保留 `.git` 文件或 `.git` 目录。
- 不要推送 `yideng966/LightAgent`。
- 同步上游时，只把需要的上游改动合并进本仓库的 `LightAgent/` 目录。
- 最终只提交并推送主仓库 `https://github.com/520pt/jkpb.git`。

## 推荐流程

1. 在临时目录拉取上游：

```powershell
git clone https://github.com/yideng966/LightAgent.git F:\newwork\TaiZhang\_upstream-LightAgent
```

2. 对比上游和本项目目录：

```powershell
git diff --no-index F:\newwork\TaiZhang\_upstream-LightAgent F:\newwork\TaiZhang\duty-reminder\LightAgent
```

3. 只把确认需要的文件改动合并到 `F:\newwork\TaiZhang\duty-reminder\LightAgent`。

4. 跑测试：

```powershell
python -m py_compile app\main.py app\daily_duty_image.py LightAgent\channel\wechat_group\wechat_group_channel.py
.codex-root-test-venv\Scripts\python.exe -m pytest tests/test_api.py tests/test_storage.py tests/test_wecom.py tests/test_daily_duty_image.py
```

5. 提交并推送主仓库：

```powershell
git status --short
git add LightAgent app tests docker docker-compose.yml docs README.md
git commit -m "Sync LightAgent upstream updates"
git push origin main
```

## 提交前检查

确认这些命令没有异常输出：

```powershell
Test-Path .gitmodules
Test-Path LightAgent\.git
git config --get-regexp "^submodule\."
git status --short
```

前两个命令应返回 `False`。第三个命令不应输出 `submodule.LightAgent`。
