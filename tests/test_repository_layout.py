from pathlib import Path
import subprocess


ROOT = Path(__file__).resolve().parents[1]


def test_lightagent_is_vendored_not_submodule():
    assert not (ROOT / ".gitmodules").exists()
    assert not (ROOT / "LightAgent" / ".git").exists()

    result = subprocess.run(
        ["git", "ls-files", "--stage", "LightAgent"],
        cwd=ROOT,
        check=True,
        capture_output=True,
        text=True,
    )

    assert "160000" not in result.stdout
    assert "LightAgent/channel/wechat_group/wechat_group_channel.py" in result.stdout
