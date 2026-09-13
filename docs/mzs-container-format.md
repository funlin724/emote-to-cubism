# MzS 容器格式

> M2 / MAGES 系引擎的资源封装格式（E-mote 游戏数据的外层包装）。
> 本文为逆向互操作性研究成果，不含任何厂商密钥。

## 1. 容器布局

```
偏移 0x00  "mzs\0"           4 字节魔数
偏移 0x04  uint32 LE         解压后载荷大小
偏移 0x08  ...至文件尾       MDF 加密的 Zstd 帧
```

解包流程 = **MDF 解密（偏移 8 起）→ Zstd 解压 → 标准 PSB**。
加密区从文件偏移 8 开始，头部不加密。

## 2. MDF 加密算法

（与 FreeMote `PsbExtension.EncodeMdf` 一致，可互相验证。）

1. `seed = MD5(UTF8(key_string))`，取 4 个小端 uint32；
2. MT19937（mt19937ar）以 `init_by_array` 播种（标准 19650218 预处理）；
3. 连续输出 `genrand_int32()` 的小端字节流，与密文**逐字节异或**；
4. 游戏变体使用 **131 字节周期**的截断密钥流（等价于取前 131 字节循环异或，
   与 FreeMote 默认 `-l 131` 一致）。

参考实现：`tools/mzs_decrypt.py`（含回封 `mzs_encrypt`）。

## 3. 密钥结构（不含密钥值）

- **完整密钥 = 基础密钥串 + 文件全名（含扩展名）**。例如基础串为 `K` 时，
  `image_info.psb.m` 的完整密钥是 `Kimage_info.psb.m`；归档内提取出的子文件
  同样按"基础串 + 子文件名"构造。
- 基础密钥串是每个游戏唯一的常量，通常硬编码在游戏主程序内。本仓库
  **不内置任何密钥**；如何从自己合法持有的游戏中提取，见
  [finding-your-base-key.md](finding-your-base-key.md)。

## 4. 验证方法

- 解密成功判据：解密后数据前 4 字节应为 `28 B5 2F FD`（Zstd 魔数）。
- `*_info.psb.m`（索引/元数据）与 `*_body.bin`（数据体）成对出现；
  body 必须通过对应 info 索引提取，直接解析 body 不受支持。
- 解出 PSB 后可直接用 FreeMote（PsbDecompile/PsbDump）继续处理，
  或用本仓库 `tools/parse_motion.py` 解析 E-mote motion 结构。

## 5. 回封

Zstd 压缩 + MDF 异或 + 头部即可回封（`mzs_encrypt`），配合 FreeMote `PsBuild`
可完成"读出→修改→写回"的往返。修改游戏资源仅建议用于自己的本地研究环境。
