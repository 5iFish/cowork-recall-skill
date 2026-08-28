#!/usr/bin/env python3
"""把本仓库 skills/cowork-recall/ 安装为跨工具共享技能 ~/.agents/skills/cowork-recall。

优先建链接（Windows 目录联接无需管理员；*nix 符号链接），失败则整目录复制并提示。
用法：
    python install_skill.py            # 默认 link 优先
    python install_skill.py --copy     # 强制复制
    python install_skill.py --remove   # 移除已安装的技能（删链接或复制目录）
"""
import os
import shutil
import sys

REPO_SKILL = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TARGET_NAME = "cowork-recall"


def target_root():
    return os.path.join(
        os.environ.get("USERPROFILE") or os.path.expanduser("~"),
        ".agents", "skills")


def _rm_single(path):
    """删除单个文件；目录交由 rmdir（仅空目录，禁递归）。"""
    try:
        if os.path.isdir(path) and not os.path.islink(path):
            os.rmdir(path)
        else:
            os.remove(path)
    except FileNotFoundError:
        pass


def remove(target):
    if not os.path.exists(target):
        print(f"未发现已安装: {target}")
        return 0
    _rm_single(target)
    print(f"已移除: {target}")
    return 0


def install(mode):
    dst_root = target_root()
    os.makedirs(dst_root, exist_ok=True)
    target = os.path.join(dst_root, TARGET_NAME)
    if mode == "--remove":
        return remove(target)
    if os.path.exists(target) or os.path.islink(target):
        if mode != "--copy":
            print(f"目标已存在: {target}\n如需覆盖请先执行 --remove 再安装，"
                  f"或使用 --copy 前先手动处理。")
            return 1
    if mode != "--copy":
        try:
            if sys.platform == "win32":
                import _winapi                                   # noqa
                _winapi.CreateJunction(REPO_SKILL, target)
            else:
                os.symlink(REPO_SKILL, target)
            print(f"已链接安装: {target} -> {REPO_SKILL}")
            print("提示：ZCode 斜杠命令可另装本仓库的 .zcode-plugin 插件壳。")
            return 0
        except OSError as e:
            print(f"链接创建失败（{e}），回退为整目录复制。后续更新需重新执行安装。")
    shutil.copytree(REPO_SKILL, target,
                    ignore=shutil.ignore_patterns("__pycache__", ".git"))
    print(f"已复制安装: {target}")
    return 0


if __name__ == "__main__":
    sys.exit(install(sys.argv[1] if len(sys.argv) > 1 else ""))
