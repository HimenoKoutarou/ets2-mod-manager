"""
上传Release到GitHub
====================
使用方法：python upload_release.py

前置条件：
1. 已创建GitHub Personal Access Token（需要 repo 权限）
2. 已打包生成 ETS2ModManager-win-x64.zip
3. 将此脚本复制到 F:\ETS2ModManager 目录下运行
"""
import json
import sys
import urllib.request
import urllib.error
from pathlib import Path


REPO_OWNER = "HimenoKoutarou"
REPO_NAME = "ets2-mod-manager"
VERSION = "v1.0.1"
RELEASE_NAME = f"ETS2 Mod Manager {VERSION}"
RELEASE_NOTES = """## 更新内容

- 自动更新功能：启动时自动检查GitHub Releases，有新版本时提示下载安装
- 多语言支持：更新功能支持中文、英文、俄文三种语言
- 版本解析修复：正确处理以'v'开头的版本号
- 优化：打包为独立可执行文件，无需Python环境

## 使用说明

1. 下载 zip 文件并解压
2. 运行 ETS2ModManager.exe 启动程序
3. 程序会自动检查更新，发现新版本时会提示下载安装

## 系统要求

- Windows 10/11 (64位)
- 无需安装Python环境
"""

ASSET_PATH = Path(r"F:\ETS2ModManager\dist\ETS2ModManager-win-x64.zip")
ASSET_NAME = "ETS2ModManager-win-x64.zip"
PROXY_URL = "http://127.0.0.1:7897"


def github_api_request(method, url, token, data=None, headers=None, raw_data=None, content_type=None):
    """发送GitHub API请求。"""
    if headers is None:
        headers = {}
    headers["Authorization"] = f"token {token}"
    headers["Accept"] = "application/vnd.github.v3+json"
    
    body = None
    if raw_data is not None:
        body = raw_data
        if content_type:
            headers["Content-Type"] = content_type
    elif data is not None:
        body = json.dumps(data).encode("utf-8")
        headers["Content-Type"] = "application/json"
    
    req = urllib.request.Request(url, data=body, headers=headers, method=method)
    
    proxy_handler = urllib.request.ProxyHandler({
        "http": PROXY_URL,
        "https": PROXY_URL,
    })
    opener = urllib.request.build_opener(proxy_handler)
    
    try:
        with opener.open(req, timeout=600) as resp:
            content = resp.read()
            content_type_resp = resp.headers.get("content-type", "")
            if "application/json" in content_type_resp:
                return resp.status, json.loads(content.decode("utf-8"))
            else:
                return resp.status, content
    except urllib.error.HTTPError as e:
        error_body = e.read().decode("utf-8", errors="replace") if hasattr(e, "read") else ""
        print(f"  [error] HTTP {e.code}: {error_body[:500]}")
        return e.code, None


def get_existing_release(token):
    """检查是否已存在该版本的Release。"""
    url = f"https://api.github.com/repos/{REPO_OWNER}/{REPO_NAME}/releases/tags/{VERSION}"
    status, data = github_api_request("GET", url, token)
    if status == 200:
        return data
    return None


def delete_release(token, release_id):
    """删除已存在的Release。"""
    url = f"https://api.github.com/repos/{REPO_OWNER}/{REPO_NAME}/releases/{release_id}"
    status, _ = github_api_request("DELETE", url, token)
    return status == 204


def create_release(token):
    """创建新的Release。"""
    url = f"https://api.github.com/repos/{REPO_OWNER}/{REPO_NAME}/releases"
    
    data = {
        "tag_name": VERSION,
        "target_commitish": "main",
        "name": RELEASE_NAME,
        "body": RELEASE_NOTES,
        "draft": False,
        "prerelease": False,
    }
    
    status, result = github_api_request("POST", url, token, data)
    if status == 201:
        print(f"  [success] Release创建成功: {result['html_url']}")
        return result
    else:
        return None


def upload_asset(token, release_id, asset_path):
    """上传资产到Release。"""
    if not asset_path.exists():
        print(f"  [error] 文件不存在: {asset_path}")
        return None
    
    file_size = asset_path.stat().st_size
    print(f"  [upload] 读取文件 {asset_path.name} ({file_size / 1024 / 1024:.1f} MB)...")
    
    with open(asset_path, "rb") as f:
        file_content = f.read()
    
    upload_url = f"https://uploads.github.com/repos/{REPO_OWNER}/{REPO_NAME}/releases/{release_id}/assets?name={ASSET_NAME}"
    
    print(f"  [upload] 上传中（使用代理 {PROXY_URL}）...")
    
    status, result = github_api_request(
        "POST",
        upload_url,
        token,
        raw_data=file_content,
        content_type="application/zip",
        headers={
            "Content-Length": str(len(file_content)),
        }
    )
    
    if status == 201:
        print(f"  [success] 上传成功!")
        return result
    else:
        return None


def main():
    print("=" * 60)
    print("  GitHub Release 上传工具")
    print("=" * 60)
    print()
    print(f"  仓库: https://github.com/{REPO_OWNER}/{REPO_NAME}")
    print(f"  版本: {VERSION}")
    print(f"  资产: {ASSET_PATH.name} ({ASSET_PATH.stat().st_size / 1024 / 1024:.1f} MB)")
    print()
    
    # 1. 获取Token
    print("-" * 40)
    print("  创建Token: https://github.com/settings/tokens")
    print("  需要权限: repo (完整的仓库控制)")
    print("-" * 40)
    token = input("\n请输入 GitHub Personal Access Token: ").strip()
    if not token:
        print("[error] Token不能为空")
        sys.exit(1)
    
    # 隐藏Token显示
    print("[info] Token已获取")
    
    # 2. 检查现有Release
    print("\n[1/3] 检查现有Release...")
    existing = get_existing_release(token)
    if existing:
        print(f"[info] 已存在该版本Release")
        choice = input("  是否删除并重新创建？(y/n): ").strip().lower()
        if choice == 'y':
            print("  [info] 删除旧Release...")
            if delete_release(token, existing['id']):
                print("  [success] 已删除旧Release")
            else:
                print("  [error] 删除失败")
                sys.exit(1)
            # 创建新Release
            print("  [info] 创建新Release...")
            release = create_release(token)
            if not release:
                sys.exit(1)
            release_id = release["id"]
        else:
            release_id = existing['id']
    else:
        # 创建新Release
        print("[info] 创建新Release...")
        release = create_release(token)
        if not release:
            sys.exit(1)
        release_id = release["id"]
    
    # 3. 上传资产
    print("\n[2/3] 上传zip文件...")
    result = upload_asset(token, release_id, ASSET_PATH)
    if not result:
        sys.exit(1)
    
    # 4. 输出结果
    print("\n[3/3] 完成!")
    print()
    print("=" * 60)
    print(f"  Release URL: https://github.com/{REPO_OWNER}/{REPO_NAME}/releases/tag/{VERSION}")
    print(f"  下载链接: {result['browser_download_url']}")
    print("=" * 60)


if __name__ == "__main__":
    main()