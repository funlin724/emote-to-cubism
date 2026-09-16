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

Shell 注意：上表 `export VAR=...` 是 bash/Zsh 语法；PowerShell 用
`$env:EMOTE_MOTION_DIR="..."`（仅当前会话生效）。

诊断开关：`HIDE_PIECES`（强制隐藏件）、`NO_EYEBALL`/`NO_MASK`/`TOP_STACK`
（build_moc3_params 的 A/B 判定开关）。

## 管线

```
游戏档案 ──mzs_decrypt.py──▶ PSB ──FreeMote PsbDecompile──▶ JSON+图集
   │
   ├─ merge_emote_parts.py     老世代多部件条目合并（老世代专用，见下）
   ├─ parse_motion.py          部件切割 + 清单 + 曲线 + 层树（质检起点）
   ├─ qc_parse.py              parse 产物质检（三方计数 + 逐像素贴回比对）
   ├─ emote_assemble.py        层树装配器（库，含 mesh_geometry/footprint）
   │
   │  ── 主接口（担保级）：PSB → PSD ──
   │  新世代单根树条目：emote_assemble 启发式装配
   │  老世代条目（多根树/变量系统）：exact_extract.py 精确求值（推荐）
   └─ build_psd.py             多外观合并 PSD + 层→外观成员表
                                  （UV 足迹静态放置；导入 Cubism Editor /
                                   喂 psd2live 等绑骨工具）
   ├─ packer_misfit.py         打包器对位失配审计（旧/新口径对照表）

   ── 实验路线（运行时可加载；Editor 不可编辑、物理未做）──
   ├─ build_moc3.py            静姿模型 + 眨眼绑定 + 眼部遮罩 → moc3
   ├─ build_moc3_params.py     参数绑定版（重放法 + 多 binding 张量积）
   ├─ moc3_writer.py           最小示例生成器（学习 moc3 结构用）
   └─ moc3_downgrade.py        MOC5(v5) → moc3 4.0 重序列化（PSD2Live 产物降版）

   ── 图集后处理 ──
   └─ atlas_bleed.py           图集边缘外扩（bleed），消网格外圈白边
```

老世代条目（2015 前后 MAGES/E-mote 游戏）的额外步骤：
`mzs_decrypt`（mdf\0 变体自动识别）→ FreeMote 解包多部件 →
`merge_emote_parts.py` 合并为单条目 → `exact_extract.py` 按引擎语义
（StaticMotionPainterCore）精确求值 → `build_psd.py`。
差异清单与配方见 docs/emote-to-cubism-method.md §7。

## 脚本清单

| 脚本 | 说明 |
|---|---|
| `mzs_decrypt.py` | MzS 容器解密/回封（MDF+Zstd）。密钥外部输入 |
| `parse_motion.py` | 条目深解析：部件 PNG、parts_list.csv、曲线、层树、metadata 概要 |
| `qc_parse.py` | parse 产物质检：部件数三方一致、PNG 与图集逐像素比对、清单字段核对 |
| `emote_assemble.py` | `Assembler` 类：层树展开/组挂载点嫁接/默认帧规则/meshMatrix 网格映射/tristrip 三角化；`mesh_geometry()`/`footprint()`（UV 足迹）供打包器共用 |
| `build_moc3.py` | 全量静姿 moc3 生成器（Editor 真值校准结构） |
| `build_moc3_params.py` | 参数绑定生成器：重放法提取位置 keyform + 多 binding 张量积 |
| `eye_stack.py` | 眼部件 draw_order 相对序校正（值集守恒） |
| `bake_stencil.py` | 【退役】眼部模板烘焙（历史对照保留） |
| `merge_emote_parts.py` | 老世代多部件条目合并：组名并集、纹理改名+icon 分段重排、src 改写、多根 layer 拼接（配方经可移植性审查实证） |
| `exact_extract.py` | 引擎语义精确提取器（StaticMotionPainterCore 语义：默认时间/帧类型/opa 255 继承/Z 序/变量系统），门面替换后走 build_psd——老世代条目的推荐路径 |
| `build_psd.py` | 多外观合并 PSD + 层→外观成员表（换装差分）。UV 足迹静态放置：足迹 1:1 裁剪、裙边三角形按钳制 UV 补绘；去重取可见件优先 |
| `packer_misfit.py` | 打包器对位失配审计：整矩形→全网格框（旧）vs 足迹→足迹框（新）逐件对照 |
| `moc3_downgrade.py` | MOC5(v5) → moc3 4.0 重序列化（PSD2Live 固定输出 v5、旧 core 只认 v4 的降版工具） |
| `atlas_bleed.py` | 图集边缘外扩（bleed）：最不透明邻居外推，消网格外圈采样半透明像素的白边 |
| `dump_rig.py` | moc3 rig 解析器（库）：绑定链/关键帧几何/裁剪链 dump 与双模型比对 |
| `psd2live_client.py` | PSD2Live MCP 直连客户端（库）。令牌从环境变量或本机注册表读取，不内嵌 |
| `amp_scale.py` | 把 PSD2Live 工程里绑在指定参数上的关键形变向中性收缩（断点续写 + 写后回读验证） |
| `build_presets.py` | 生成 blink/idle motion3、表情 exp3、挂载它们的 model3.json（官方 JSON 格式） |
| `moc3_writer.py` | 最小可验证模型示例（两个四边形网格） |
| `check_moc3.py` | moc3 摘要 / 逐段对齐校验 / 双文件比对 |
| `dump_moc3.py` | moc3 全段 dump（与 Editor 真值对照用） |
| `moc3lib/` | moc3 读写库（基于 py-moc3 修复版；修复内容见 docs/moc3-format-semantics.md） |

## 依赖

```
pip install pillow numpy psd-tools zstandard
```

FreeMote（.NET）为外部依赖，仅用于解包/回封，不在本仓库分发。
