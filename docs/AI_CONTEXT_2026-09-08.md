# ETS2ModManager 外部上下文

## 当前阶段

正在建立只读 BSII 解析基础，暂不启用金钱、经验、等级或磨损写入。

## 已验证事实

- 真实 `game.sii` 解密后为 BSII version 3。
- 当前样本包含 46 个结构定义和 35,687 个对象。
- `economy.experience_points` 是 type `0x27`，UInt32，当前值 `279375`。
- `bank.money_account` 是 type `0x31`，Int64，当前值 `1253729`。
- `vehicle.engine_wear` 是 type `0x05`，float32；车辆磨损字段可以通过结构定义和对象边界定位。
- 字段 offset/size 指向字段值载荷，不指向字段名或类型字节。

## 本阶段约束

- 不清理、回退或覆盖用户已有未提交修改。
- 只读解析和测试通过后单独提交本阶段相关文件。
- 不 push、不打包、不发布。
- Cloud/Steam Profile 只读；Profile/存档写入前必须保证游戏关闭。

## 后续方向

1. 用 BSII 字段类型和 offset 实现受校验的金钱/经验写入。
2. 经验使用 `economy.experience_points` UInt32，等级由经验反推，不搜索不存在的 `level` 字段。
3. 金钱使用 `bank.money_account` Int64。
4. 为写入增加类型、边界、回环和游戏关闭检查。
