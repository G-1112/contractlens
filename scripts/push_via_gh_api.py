"""GitHub 推送（api.github.com git-data 通道）。

背景：github.com 被 DNS 污染，`git push` 假成功/必失败；唯一可信通道是
`gh api`（认证走 api.github.com）。本脚本把本地 72661f5..HEAD 的提交逐个
经 git-data API（blobs→trees→commits→refs）复刻到远端 main。

流程：
  0. 备份：在远端创建 backup/region-<date> 分支指向当前远端 main（零丢失）。
  1. 遍历 git rev-list --reverse <base>..HEAD：
     - git diff-tree 找出该提交相对父提交的变更文件；
     - 新 blob 逐个上传（blob sha = 内容 sha，去重）；
     - 以远端当前 tree 为 base_tree 建 tree，再建 commit（保留本地
       author/committer 与时间），PATCH refs/heads/main 前进。
  2. 终验：远端 main 的 tree sha 必须等于本地 HEAD^{tree}（内容一致性的
     硬证明，提交 sha 允许因 API 规范化差异而不同）。
  3. 二次校验：gh api commits/main 读回 HEAD sha 与 message。

用法：python scripts/push_via_gh_api.py [--dry-run]
注意：需 gh CLI 已登录（G-1112）；github.com 网络不可达不影响本脚本。
"""

from __future__ import annotations

import base64
import json
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
GH = r"C:\Program Files\GitHub CLI\gh.exe"
REPO = "G-1112/contractlens"
BASE = "72661f5"  # 与远端的最近共同祖先（可用 --base <sha> 覆盖为增量基点）
if "--base" in sys.argv:
    BASE = sys.argv[sys.argv.index("--base") + 1]


def gh(args: list[str], stdin: str | None = None) -> dict | list:
    """调 gh api；端点段自动以 / 拼接为单参数。返回解析后的 JSON。"""
    endpoint: list[str] = []
    rest: list[str] = []
    for a in args:
        if rest or a.startswith("-"):
            rest.append(a)
        else:
            endpoint.append(a)
    cmd = [GH, "api", "/".join(endpoint), *rest]
    if stdin is not None:
        cmd += ["--input", "-"]
    r = subprocess.run(cmd, input=stdin, capture_output=True, text=True, encoding="utf-8")
    if r.returncode != 0:
        raise RuntimeError(f"gh api {' '.join(args[:3])} 失败: {r.stderr[:300]}")
    return json.loads(r.stdout)


def git(args: str) -> str:
    argv = ["git", "-c", "core.quotepath=false", *args.split()]
    r = subprocess.run(argv, cwd=ROOT, capture_output=True, encoding="utf-8")
    if r.returncode != 0:
        raise RuntimeError(f"git {args} 失败: {r.stderr[:300]}")
    return r.stdout.strip()


def main() -> int:
    dry = "--dry-run" in sys.argv

    remote_main = gh(["repos", REPO, "commits", "main"])
    remote_head_sha = remote_main["sha"]
    remote_tree_sha = remote_main["commit"]["tree"]["sha"]
    print(f"远端 main: {remote_head_sha[:7]} tree={remote_tree_sha[:7]}")
    print(f"本地 HEAD: {git('rev-parse --short HEAD')} tree={git('rev-parse HEAD^{tree}')[:7]}")

    # 0) 备份远端 main（幂等：已存在则跳过）
    try:
        gh(["repos", REPO, "git", "refs", "heads", f"backup/region-20261125"])
        print("备份分支已存在，跳过创建")
    except RuntimeError:
        if not dry:
            gh(["repos", REPO, "git", "refs", "--input", "-"], stdin=json.dumps({
                "ref": "refs/heads/backup/region-20261125", "sha": remote_head_sha}))
        print(f"备份分支 backup/region-20261125 → {remote_head_sha[:7]} ✓")

    if dry:
        print("(dry-run 停止)")
        return 0

    # 第十八轮修正：以本地 72661f5 的树为基准（远端 fa891a5 树含本地已删除的
    # 陈旧文件，污染了后续每个提交的树）。blob 以本地为准重传/复用，去重靠
    # 内容 sha，服务端天然幂等。
    tree_map: dict[str, tuple[str, str]] = {}
    for line in git(f"ls-tree -r {BASE}").splitlines():
        meta, path = line.split("\t", 1)
        mode, _t, blob = meta.split()[:3]
        tree_map[path] = (blob, mode)
    uploaded: set[str] = set()

    # 预上传基准树的全部 blob（首个提交 diff 为空时，树的 blob 必须已在远端）
    def _cat_blob(blob: str) -> bytes:
        for attempt in range(3):
            r = subprocess.run(
                ["git", "cat-file", "blob", blob], cwd=ROOT, capture_output=True
            )
            if r.returncode == 0 and r.stdout:
                return r.stdout
            time.sleep(0.5 * (attempt + 1))
        raise RuntimeError(f"cat-file 3 次空输出: {blob[:8]}")

    for path, (blob, mode) in list(tree_map.items()):
        if blob not in uploaded:
            data = _cat_blob(blob)
            b64 = base64.b64encode(data).decode()
            res = gh(["repos", REPO, "git", "blobs", "--input", "-"], stdin=json.dumps(
                {"content": b64, "encoding": "base64"}))
            if res["sha"] != blob:
                print(f"  ✗ {path}: 期望 {blob[:8]} 实得 {res['sha'][:8]}  发送字节数 {len(data)}")
                raise SystemExit(1)
            uploaded.add(blob)
    print(f"基准树 blob 预上传：{len(uploaded)} 个")

    commits = git(f"rev-list --reverse {BASE}..HEAD").split()
    print(f"待推送提交：{len(commits)} 个")

    prev_remote_commit = remote_head_sha
    prev_local_parent = BASE

    for i, csha in enumerate(commits, 1):
        meta = git(f"show -s --format=%an%n%ae%n%aI%n%cI%n%s%n%b {csha}")
        lines = meta.split("\n")
        an, ae, ad, cd, subject = lines[0], lines[1], lines[2], lines[3], lines[4]
        body = "\n".join(lines[5:]).rstrip("\n")
        message = (subject + ("\n\n" + body if body.strip() else ""))

        # 相对父提交的变更（本地父提交）
        diff = git(f"diff-tree -r --no-renames {prev_local_parent} {csha}")
        tree_changes: list[dict] = []
        for line in diff.splitlines():
            if not line.startswith(":"):
                continue
            meta, path = line.split("\t", 1)
            fields = meta.lstrip(":").split()
            newmode, status = fields[1], fields[4]
            if "D" in status:
                tree_map.pop(path, None)
                tree_changes.append({"path": path, "sha": None})
                continue
            blob = subprocess.run(
                ["git", "-c", "core.quotepath=false", "rev-parse", f"{csha}:{path}"],
                cwd=ROOT, capture_output=True, text=True, encoding="utf-8",
            ).stdout.strip()
            if blob not in uploaded:
                data = _cat_blob(blob)
                b64 = base64.b64encode(data).decode()
                res = gh(["repos", REPO, "git", "blobs", "--input", "-"], stdin=json.dumps(
                    {"content": b64, "encoding": "base64"}))
                assert res["sha"] == blob, f"blob sha 不一致 {path}"
                uploaded.add(blob)
            tree_map[path] = (blob, newmode or "100644")
            tree_changes.append({"path": path, "mode": newmode or "100644",
                                 "type": "blob", "sha": blob})

        # 全量树：不走 base_tree（删除语义在部分实现下报 invalid mode），
        # 直接提交 tree_map 的完整清单，幂等且无删除语义歧义。
        full_tree = [{"path": k, "mode": m, "type": "blob", "sha": sh}
                     for k, (sh, m) in sorted(tree_map.items())]
        tree_res = gh(["repos", REPO, "git", "trees", "--input", "-"], stdin=json.dumps(
            {"tree": full_tree}))
        remote_tree_sha = tree_res["sha"]

        commit_res = gh(["repos", REPO, "git", "commits", "--input", "-"], stdin=json.dumps({
            "message": message,
            "tree": remote_tree_sha,
            "parents": [prev_remote_commit],
            "author": {"name": an, "email": ae, "date": ad},
            "committer": {"name": an, "email": ae, "date": cd},
        }))
        prev_remote_commit = commit_res["sha"]
        gh(["repos", REPO, "git", "refs", "heads", "main", "--method", "PATCH",
            "--input", "-"], stdin=json.dumps({"sha": prev_remote_commit, "force": True}))
        prev_local_parent = csha
        print(f"[{i}/{len(commits)}] {csha[:7]} → 远端 {prev_remote_commit[:7]} {subject[:40]}")

    # 终验 1：树哈希一致 = 内容逐字节一致
    local_tree = git("rev-parse HEAD^{tree}")
    ok_tree = remote_tree_sha == local_tree
    print(f"终验·树哈希一致：{ok_tree}（本地 {local_tree[:7]} / 远端 {remote_tree_sha[:7]}）")
    # 终验 2：读回远端 HEAD
    back = gh(["repos", REPO, "commits", "main"])
    print(f"终验·远端 HEAD：{back['sha'][:7]} {back['commit']['message'].splitlines()[0][:50]}")
    return 0 if ok_tree else 1


if __name__ == "__main__":
    sys.exit(main())
