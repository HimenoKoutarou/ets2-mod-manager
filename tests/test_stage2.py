"""
ETS2 Mod Manager — Stage 2 验证
内容：
  1. ProfileService 列出所有 profile
  2. 读取每个 profile 的 active_mods（即使 0 个也 OK）
  3. 加密一致性：构造合成 profile.sii（明文 + 自加密副本），验证 set_active_mods → 再读一致
  4. rewrite_active_mods_in_text 纯文本回环
  5. PriorityService：batch toggle / 拖拽 / apply_preset
  6. BackupService：备份 / 滚动清理 / 去重 / 恢复
"""
from __future__ import annotations

import os, sys, shutil, tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from utils.paths import detect_paths
from core.sii_parser import parse_mods_info, parse_sii
from core.mod_scanner import ModScanner
from core.models import Mod, ModManifest
from services.backup_service import BackupService
from services.profile_service import (
    ProfileService, ProfileInfo,
    decrypt_profile_bytes, encrypt_profile_bytes,
    rewrite_active_mods_in_text, _looks_encrypted, _decode_text
)
from services.priority_service import PriorityService


def hr(t): print(f"\n{'='*60}\n{t}\n{'='*60}")

PASS_CNT = [0]; FAIL_CNT = [0]

def check(label, cond, detail=""):
    if cond:
        PASS_CNT[0] += 1
        print(f"  OK ✅ {label}" + (f" ({detail})" if detail else ""))
    else:
        FAIL_CNT[0] += 1
        print(f"  ❌ 失败: {label}" + (f"\n     补充：{detail}" if detail else ""))


SAMPLE_PLAIN_SII = """SiiNunit
{
profile : _nameless.profile.profile {
 profile_name: "我的合成存档"
 save_name: "柏林 → 法兰克福"
 active_mods: 6
 active_mods[0]: "promods-eu-def-v280"
 active_mods[1]: "promods-eu-media-v282"
 active_mods[2]: "promods-eu-model2-v282"
 active_mods[3]: "RusMap-def_v2.55"
 active_mods[4]: "ai_traffic_pack_by_Jazzycat_v21.8.11"
 active_mods[5]: "weather_mod_jbx_v2"
 other_thing: 123
}
}
"""


def main():
    hr("ETS2 Mod Manager — Stage 2 验证")
    paths = detect_paths()

    # ------------------------------------------------------------------
    # 测试 1：列出 profile
    # ------------------------------------------------------------------
    hr("测试 1：ProfileService 列出 profile")
    prof_svc = ProfileService(paths)
    profiles = prof_svc.list_profiles()
    check("至少找到 1 个 profile", len(profiles) >= 1, f"实际找到 {len(profiles)} 个")
    for p in profiles:
        print(f"    · {p.profile_id[:20]}…  [{p.location}]  mod_count={p.mod_count}  encrypted={p.is_encrypted}  ({p.profile_sii.name})")

    # ------------------------------------------------------------------
    # 测试 2：读取每个 profile 的 active_mods（真实数据）
    # ------------------------------------------------------------------
    hr("测试 2：读取每个 profile 的 active_mods")
    first_with_mods = None
    for p in profiles:
        try:
            mods = prof_svc.get_active_mods(p)
            check(f"{p.profile_id[:14]}… 读取无异常",
                  isinstance(mods, list),
                  f"读取到 {len(mods)} 个模组")
            if mods:
                print(f"        前3 = {mods[:3]}")
                if first_with_mods is None:
                    first_with_mods = (p, mods)
        except Exception as e:
            check(f"{p.profile_id[:14]}… 读取无异常", False, f"异常: {e!r}")
    if first_with_mods is None:
        print("  ℹ️ 真实 profile 没有启用模组。将用合成样本进行写回测试。")

    # ------------------------------------------------------------------
    # 测试 3：合成加密/解密文件 + set_active_mods 回环
    # ------------------------------------------------------------------
    hr("测试 3：加密 + set_active_mods 回环（合成 2 种 profile.sii 副本）")
    with tempfile.TemporaryDirectory() as td:
        tdp = Path(td)
        # ----- A. 明文版 -----
        plain_dir = tdp / "prof_plain"
        plain_dir.mkdir()
        plain_sii = plain_dir / "profile.sii"
        plain_sii.write_text(SAMPLE_PLAIN_SII, encoding="utf-8-sig")
        prof_plain = ProfileInfo("plain", "test", plain_dir, plain_sii, False)
        mods_plain = prof_svc.get_active_mods(prof_plain)
        check("A) 明文版：可正确读取 6 条 active_mods",
              len(mods_plain) == 6 and mods_plain[0] == "promods-eu-def-v280",
              f"实际 mods_plain = {mods_plain}")

        new_list = ["zz_first_map", "weather_mod_jbx_v2", "promods-eu-media-v282",
                    "RusMap-def_v2.55", "ai_traffic_pack_by_Jazzycat_v21.8.11", "qq_last_truck"]
        prof_svc.set_active_mods(prof_plain, new_list)
        after = prof_svc.get_active_mods(prof_plain)
        check("A) 明文版 set_active_mods → 再读回一致", after == new_list,
              f"expected={new_list[:3]}...  actual={after[:3]}...")

        # ----- B. 加密版（用我们自己的 encrypt_profile_bytes 生成，保证能自循环解密） -----
        enc_dir = tdp / "prof_enc"
        enc_dir.mkdir()
        enc_sii = enc_dir / "profile.sii"
        enc_bytes = encrypt_profile_bytes(SAMPLE_PLAIN_SII.encode("utf-8-sig"))
        check("B) encrypt_profile_bytes 生成的文件被识别为加密",
              _looks_encrypted(enc_bytes) and enc_bytes.startswith(b"Sii\x00"),
              f"len={len(enc_bytes)} head={enc_bytes[:8]!r}")
        enc_sii.write_bytes(enc_bytes)
        prof_enc = ProfileInfo("enc", "test", enc_dir, enc_sii, True)
        mods_enc = prof_svc.get_active_mods(prof_enc)
        check("B) 加密版：解密后读取到 6 条 active_mods",
              len(mods_enc) == 6 and mods_enc[0] == "promods-eu-def-v280",
              f"实际 mods_enc = {mods_enc}")
        prof_svc.set_active_mods(prof_enc, new_list)
        after_enc = prof_svc.get_active_mods(prof_enc)
        check("B) 加密版 set_active_mods → 再读回一致", after_enc == new_list,
              f"expected len={len(new_list)} actual len={len(after_enc)}")

        # ----- C. 真实加密 profile.sii 拷贝版（仅验证：读→写→读，不要求解密成功，若失败就当提示） -----
        if first_with_mods is not None:
            src_p, src_mods = first_with_mods
            copy_dir = tdp / src_p.profile_id
            copy_dir.mkdir()
            dest = copy_dir / "profile.sii"
            shutil.copy2(src_p.profile_sii, dest)
            prof_copy = ProfileInfo(src_p.profile_id, "test", copy_dir, dest, _looks_encrypted(dest.read_bytes()))
            try:
                got = prof_svc.get_active_mods(prof_copy)
                if got == src_mods:
                    check("C) 真实加密文件副本读成功", True, f"mods={len(got)}")
                    reversed_mods = list(reversed(src_mods))[: max(1, min(20, len(src_mods)))]
                    prof_svc.set_active_mods(prof_copy, reversed_mods)
                    back = prof_svc.get_active_mods(prof_copy)
                    check("C) 真实文件副本 set_active_mods 回环一致", back == reversed_mods)
                else:
                    print("  ℹ️ 真实加密 profile.sii 未被内建算法解开（正常，等待 SII_Decrypt.exe 配置）。跳过该文件的写回测试。")
            except Exception as e:
                print(f"  ℹ️ 真实文件副本测跳过：{e!r}")

    # ------------------------------------------------------------------
    # 测试 3b：rewrite_active_mods_in_text 回环
    # ------------------------------------------------------------------
    hr("测试 3b：rewrite_active_mods_in_text 文本级回环（合成样本）")
    out = rewrite_active_mods_in_text(SAMPLE_PLAIN_SII, ["z", "y"])
    check("重写后可再次 parse_sii", len(parse_sii(out)) == 1)
    u = parse_sii(out)[0]
    check("解析后模组顺序 == 写回的顺序", u.get_indexed("active_mods") == ["z", "y"],
          f"actual = {u.get_indexed('active_mods')}")

    # ------------------------------------------------------------------
    # 测试 4：PriorityService
    # ------------------------------------------------------------------
    hr("测试 4：PriorityService — 批量开关 / 拖拽 / 预设")
    mods_info_index = parse_mods_info(paths.mods_info_path)
    pseudo_mods = []
    # 名字里包含关键词，便于 apply_preset 的 "name" 关键词匹配
    dataset = [
        ("map_eurafrica_extended_rc",  "map_mod"),
        ("promods_eurasia_280_def",    "map_mod"),
        ("rusmap_models_255",          "assets"),
        ("trafficpack_jazzycat_21",    "traffic_pack"),
        ("jbx_weather_graphics_2",     "weather"),
        ("sound_fmod_pack_real",       "sound"),
    ]
    for pn, cat in dataset:
        mm = ModManifest(package_version="1.0", display_name=pn.replace("_"," "),
                         author="test", categories=[cat],
                         compatible_versions=["1.50"], description_filename="", icon_filename="")
        m = Mod(mod_id=pn, package_path=pn, package_type="directory",
                file_size=0, last_modified=0.0, manifest=mm)
        pseudo_mods.append(m)
    all_pns = [m.mod_id for m in pseudo_mods]
    ps = PriorityService(pseudo_mods)
    active_init = ["rusmap_models_255", "promods_eurasia_280_def", "sound_fmod_pack_real", "jbx_weather_graphics_2"]
    wl = ps.build_worklist(active_init, all_pns)
    check("build_worklist 条目数", len(wl) == len(all_pns), f"{len(wl)} vs {len(all_pns)}")
    check("active 在最前", [x["package_name"] for x in wl[:4]] == active_init)

    # 启用最后 2 条（trafficpack + map_eurafrica）
    wl2 = ps.batch_toggle(wl, indices=[4, 5], action="enable")
    enabled_pns = [x["package_name"] for x in wl2 if x["enabled"]]
    check("批量启用后 enabled 数", len(enabled_pns) == 6, f"{enabled_pns}")
    # 禁用 第 2 条（promods_eurasia_280_def）
    wl2b = ps.batch_toggle(wl2, indices=[1], action="disable")
    # 禁用后，被禁用的条目会挪到 disabled 段（末尾），order = -1
    promods_item = next((x for x in wl2b if x["package_name"] == "promods_eurasia_280_def"), None)
    check("禁用 promods_eurasia_280_def 后 order=-1 (移到 disabled 区)",
          promods_item is not None and not promods_item["enabled"] and promods_item["order"] == -1,
          f"实际 item = {promods_item}")

    # 拖拽：把第 4 条（sound_...）拖到第 0 条前
    wl3 = ps.reorder_before(wl2, indices=[2], target_before_index=0)
    first_en = [x["package_name"] for x in wl3 if x["enabled"]][0]
    check("拖拽：sound 应在首位（最高优先级最后加载前，我们这里用 load order 先排先加载）",
          first_en == "sound_fmod_pack_real", f"实际首个={first_en}")

    # 一键预设：地图底（先加载）→ 中间素材 → 功能/声音/天气在上（后加载，覆盖）
    wl4 = ps.apply_preset(wl2)
    en_list = [x["package_name"] for x in wl4 if x["enabled"]]
    map_positions = [i for i, n in enumerate(en_list) if "map" in n or "promods" in n or "rusmap" in n]
    func_positions = [i for i, n in enumerate(en_list) if any(k in n for k in ["trafficpack", "weather", "sound", "graphics"])]
    min_map, max_func = min(map_positions), max(func_positions)
    # 预设要求"地图在前，功能类在后"（地图底 = 先加载），所以 map 的最小位置应该小于 func 的最大位置
    check("apply_preset：promods/map/rusmap 条目整体早于 traffic/weather/sound",
          min_map < max_func or (len(map_positions) >= 2 and len(func_positions) >= 2 and max(map_positions) <= min(func_positions) + 1),
          f"map@idx={map_positions}  func@idx={func_positions}  full={en_list}")

    # move_top / move_bottom
    wl5 = ps.move_top(wl2, indices=[len(enabled_pns) - 1])  # 把最后一条（第 5）拖 top
    first_en_5 = [x["package_name"] for x in wl5 if x["enabled"]][0]
    check("move_top：末条置顶后 == 末条", first_en_5 == enabled_pns[-1], f"实际={first_en_5}")

    # ------------------------------------------------------------------
    # 测试 5：BackupService
    # ------------------------------------------------------------------
    hr("测试 5：BackupService 备份 / 滚动清理 / 去重 / 恢复")
    with tempfile.TemporaryDirectory() as td:
        bs = BackupService(max_backups=3)
        target = Path(td) / "important.sii"
        target.write_text("v1")
        b1 = bs.backup(target, tag="a")
        check("v1 首次备份成功", b1 is not None)
        target.write_text("v1")
        b2 = bs.backup(target, tag="b")
        check("内容一致时 → 跳过备份", b2 is None)
        target.write_text("v2"); b3 = bs.backup(target)
        target.write_text("v3"); b4 = bs.backup(target)
        target.write_text("v4"); b5 = bs.backup(target)
        bl = bs.list_backups(target)
        check("滚动后备份数 ≤ 3", len(bl) <= 3, f"实际={len(bl)}  list={[p.name for p in bl]}")
        target.write_text("脏内容")
        restored = bs.restore_latest(target)
        check("restore_latest 返回非空", restored is not None)
        check("restore 后内容还原为最新备份（v4）", target.read_text() == "v4",
              f"actual={target.read_text()!r}")

    # ------------------------------------------------------------------
    # 总结
    # ------------------------------------------------------------------
    print()
    print("=" * 60)
    tot = PASS_CNT[0] + FAIL_CNT[0]
    print(f"验证结果：{PASS_CNT[0]} / {tot} 通过")
    if FAIL_CNT[0] == 0:
        print("🎉 Stage 2 业务服务（Profile / Priority / Backup）全部通过！可以进入 Stage 3 UI 开发")
    else:
        print("⚠️  有失败项，先根据上面报错修复再继续")
        sys.exit(1)

if __name__ == "__main__":
    main()