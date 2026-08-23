"""
ETS2 Mod Manager — Stage 1 验证脚本
运行：python tests/test_stage1.py

目标：
1) SII Parser 能正确解析你本机的 mods_info.sii（573条）
2) SII Parser 能正确解析一个虚拟 manifest.sii 样本
3) Paths 自动检测 ETS2 相关路径
4) ModScanner 能扫描模组（即使 /mod 目录为空，至少 mods_info.sii 要读对）
5) SymlinkManager 状态报告正常（真实迁移功能需要单独手动运行）
"""
from __future__ import annotations

import os
import sys
import time
from pathlib import Path

# 把 src 加到 sys.path
THIS_DIR = Path(__file__).resolve().parent
SRC_DIR = THIS_DIR.parent / "src"
sys.path.insert(0, str(SRC_DIR))


def test_manifest_parser():
    """测试：解析标准 manifest.sii 样本"""
    print("\n=== 测试 1：manifest.sii 标准样本解析 ===")
    from core.sii_parser import parse_sii
    sample = r'''
SiiNunit
{
mod_package : .package_name
{
 package_version: "1.0 Release 3"
 display_name: "My ProMods Test Mod"
 author: "TestAuthor"
 category[]: "map"
 category[]: "models"
 icon: "mod_icon.jpg"
 description_file: "mod_description.txt"
 compatible_versions[]: "1.57.*"
 compatible_versions[]: "1.58.*"
 dlc_dependencies[]: "dlc_north"
 # This is a comment
}
}
'''
    units = parse_sii(sample)
    print(f"  解析到 unit 数量: {len(units)}")
    assert len(units) >= 1, "至少应该解析出一个 mod_package"
    pkg = units[0]
    assert pkg.unit_type == "mod_package", f"type={pkg.unit_type}"
    assert pkg.get("display_name") == "My ProMods Test Mod"
    assert pkg.get("author") == "TestAuthor"
    assert pkg.get_list("category") == ["map", "models"]
    assert pkg.get_list("compatible_versions") == ["1.57.*", "1.58.*"]
    assert pkg.get_list("dlc_dependencies") == ["dlc_north"]
    assert pkg.get("icon") == "mod_icon.jpg"
    print("  OK ✅ manifest 解析通过")


def test_mods_info_real():
    """测试：解析你本机的 mods_info.sii（真实573条）"""
    print("\n=== 测试 2：本机 mods_info.sii 解析 ===")
    from core.sii_parser import parse_mods_info
    mi_path = Path.home() / "Documents" / "Euro Truck Simulator 2" / "mods_info.sii"
    if not mi_path.exists():
        # 尝试 OneDrive 路径
        alt = Path.home() / "OneDrive" / "Documents" / "Euro Truck Simulator 2" / "mods_info.sii"
        if alt.exists():
            mi_path = alt
    if not mi_path.exists():
        print(f"  ⚠️  跳过：找不到 mods_info.sii（预期路径 {mi_path}）")
        return
    t0 = time.perf_counter()
    idx = parse_mods_info(str(mi_path))
    dt = (time.perf_counter() - t0) * 1000
    n = len(idx)
    print(f"  解析模组数: {n}  (耗时 {dt:.1f} ms)")
    assert n > 500, f"你本机应该有 573 个左右，实际 {n}（解析器可能有bug）"
    # 打印前5个和最后5个
    items = list(idx.items())[:5]
    print("  前5个样本:")
    for name, ts in items:
        print(f"    · {name}  timestamp={ts}")
    print("  OK ✅ mods_info 解析通过")


def test_paths_detection():
    """测试：ETS2 路径自动检测"""
    print("\n=== 测试 3：ETS2 路径自动检测 ===")
    from utils.paths import detect_paths
    p = detect_paths()
    print(f"  ETS2 文档目录:  {p.documents_dir}  (存在={p.documents_dir.exists()})")
    print(f"  mod 目录:       {p.mod_dir}  (存在={p.mod_dir.exists()})")
    print(f"  mods_info.sii:  {p.mods_info_path}  (存在={p.mods_info_path.exists()})")
    print(f"  本地 profiles:  {p.profiles_dir}")
    print(f"  Steam profiles: {p.steam_profiles_dir}")
    print(f"  Workshop 目录:  {p.workshop_content_dir}")
    print(f"  云端 profiles:  {p.steam_cloud_dir}")
    assert p.mods_info_path.exists(), "mods_info.sii 应该存在（你本机已有）"
    print("  OK ✅ 路径检测通过")


def test_mod_scanner():
    """测试：模组扫描（基础，不解析 scs 内容）"""
    print("\n=== 测试 4：ModScanner 基础扫描 ===")
    from utils.paths import detect_paths
    from core.mod_scanner import ModScanner
    p = detect_paths()
    scanner = ModScanner(
        local_mod_dir=p.mod_dir if p.mod_dir.exists() else None,
        workshop_dir=p.workshop_content_dir,
        mods_info_path=p.mods_info_path,
    )
    mi = scanner.load_mods_info_index()
    print(f"  mods_info 索引条目: {len(mi)}")
    # skip_parse=True 只读基础信息
    mods = scanner.scan(skip_manifest_parse=True)
    total_size = sum(m.file_size for m in mods)
    print(f"  扫描到模组(仅基础信息): {len(mods)} 个, 总大小 {total_size/1024/1024:.1f} MB")
    # 有 mods_info 但 /mod 目录可能空（因为你用了 workshop ？），这是正常现象
    if len(mods) == 0 and len(mi) > 500:
        print("  ℹ️  提示：本地 mod 目录为空，573 个模组全部来自 Steam Workshop，这是正常的")
    for m in mods[:3]:
        print(f"    · {m.display_title}  ({m.package_type}, {m.size_mb} MB)")
    print("  OK ✅ ModScanner 工作正常")


def test_symlink_status():
    """测试：SymlinkManager 状态检测（不执行真实链接操作）"""
    print("\n=== 测试 5：SymlinkManager 状态查询 ===")
    from utils.paths import detect_paths
    from utils.symlink_manager import SymlinkManager
    p = detect_paths()
    mgr = SymlinkManager(p.mod_dir)
    st = mgr.get_status()
    print(f"  目标目录: {st.get('link')}")
    print(f"  状态: {st.get('kind')}  存在={st.get('exists')}")
    if st.get("target"):
        print(f"  链接指向: {st['target']}")
    # 根据实际情况给建议
    if st.get("kind") == "real_dir":
        # 估算 C 盘占用
        try:
            from core.mod_scanner import _dir_size
            sz = _dir_size(p.mod_dir) / 1024 / 1024
            print(f"  当前真实目录占用: {sz:.1f} MB（若想迁移到 F 盘，UI 中将提供一键功能）")
        except Exception:
            pass
    print("  OK ✅ SymlinkManager 状态查询正常")


def main():
    print("=" * 60)
    print("ETS2 Mod Manager — Stage 1 验证")
    print("=" * 60)
    passed = 0
    total = 5
    tests = [
        test_manifest_parser,
        test_mods_info_real,
        test_paths_detection,
        test_mod_scanner,
        test_symlink_status,
    ]
    for t in tests:
        try:
            t()
            passed += 1
        except AssertionError as e:
            print(f"  ❌ 断言失败: {e}")
        except Exception as e:
            import traceback
            print(f"  ❌ 异常 {type(e).__name__}: {e}")
            traceback.print_exc()

    print("\n" + "=" * 60)
    print(f"验证结果：{passed} / {total} 通过")
    if passed == total:
        print("🎉 Stage 1 核心代码全部通过！可以进入 Stage 2 开发 ProfileService/PriorityService 了")
    else:
        print("⚠️  有失败项，先根据上面报错修复再继续")
    print("=" * 60)
    return passed == total


if __name__ == "__main__":
    ok = main()
    sys.exit(0 if ok else 1)
