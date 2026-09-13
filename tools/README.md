# tools/ — E-mote → Cubism 工具链

> 所有脚本默认从环境变量读取数据位置与条目名；
> **不内置任何密钥、不引用任何具体游戏**。
> 标注**【本作校准值】**的常量为某个具体游戏的实测标定（适配层），
> 换游戏需重新标定——见 `../docs/emote-to-cubism-method.md` §5。

## 环境变量约定

| 变量 | 含义 | 使用者 |
|---|---|---|
| `EMOTE_MOTION_DIR` | FreeMote 解包产物目录（含 `<条目>.psb.m.json` 等） | parse_motion / emote_assemble / moc3_writer |
| `EMOTE_ENTRY` | 条目名（如 `char_a`） | 全部生成器 |
| `EMOTE_OUT` | 输出目录 | 生成器 |
| `MZS_BASE_KEY` / `--key` / `mzs_key.txt` | MzS 基础密钥（**用户自带**） | mzs_decrypt |

诊断开关：`HIDE_PIECES`（强制隐藏件）、`NO_EYEBALL`/`NO_MASK`/`TOP_STACK`
（build_moc3_params 的 A/B 判定开关）。

## 管线

```
游戏档案 ──mzs_decrypt.py──▶ PSB ──FreeMote PsbDecompile──▶ JSON+图集
   │
   ├─ parse_motion.py          部件切割 + 清单 + 曲线 + 层树（质检起点）
   ├─ emote_assemble.py        层树装配器（库，被下游 import）
   │
   │  ── 主接口（担保级）：PSB → PSD ──
   └─ build_psd.py             多外观合并 PSD + 层→外观成员表
                                  （导入 Cubism Editor / 喂 psd2live 等绑骨工具）

   ── 实验路线（运行时可加载；Editor 不可编辑、物理未做）──
   ├─ build_moc3.py            静姿模型 + 眨眼绑定 + 眼部遮罩 → moc3
   ├─ build_moc3_params.py     参数绑定版（重放法 + 多 binding 张量积）
   └─ moc3_writer.py           最小示例生成器（学习 moc3 结构用）
```

## 脚本清单

| 脚本 | 说明 |
|---|---|
| `mzs_decrypt.py` | MzS 容器解密/回封（MDF+Zstd）。密钥外部输入 |
| `parse_motion.py` | 条目深解析：部件 PNG、parts_list.csv、曲线、层树、metadata 概要 |
| `emote_assemble.py` | `Assembler` 类：层树展开/组挂载点嫁接/默认帧规则/meshMatrix 网格映射/tristrip 三角化 |
| `build_moc3.py` | 全量静姿 moc3 生成器（Editor 真值校准结构） |
| `build_moc3_params.py` | 参数绑定生成器：重放法提取位置 keyform + 多 binding 张量积 |
| `eye_stack.py` | 眼部件 draw_order 相对序校正（值集守恒） |
| `bake_stencil.py` | 【退役】眼部模板烘焙（历史对照保留） |
| `build_psd.py` | 多外观合并 PSD + 层→外观成员表（换装差分） |
| `moc3_writer.py` | 最小可验证模型示例（两个四边形网格） |
| `check_moc3.py` | moc3 摘要 / 逐段对齐校验 / 双文件比对 |
| `dump_moc3.py` | moc3 全段 dump（与 Editor 真值对照用） |
| `moc3lib/` | moc3 读写库（基于 py-moc3 修复版；修复内容见 docs/moc3-format-semantics.md） |

## 依赖

```
pip install pillow numpy psd-tools zstandard
```

FreeMote（.NET）为外部依赖，仅用于解包/回封，不在本仓库分发。
