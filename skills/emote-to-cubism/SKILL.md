---
name: emote-to-cubism
description: Drive the E-mote → Cubism rebuild pipeline with an AI agent: unpack MzS/PSB game data the user legally owns, parse Emote layer trees, assemble geometry, generate and verify moc3 models. Use when the user asks to unpack/convert/rebuild E-mote or MAGES-engine game character data into Live2D Cubism models.
---

# E-mote → Cubism 管线（Agent Skill）

让 AI 代理驱动本仓库工具链，把用户合法持有的 E-mote 游戏数据重建为
Cubism 模型。代理负责判断类工作（选条目、认部件、质检、对比），
工具负责确定性操作（解密、解析、装配、生成、校验）。

## 红线（每次开工前先确认）

1. **只处理用户自备的游戏数据**；要求用户确认拥有该游戏。
2. **密钥绝不入库、不入日志、不入示例**：只经 `--key` / `MZS_BASE_KEY` /
   本地 `mzs_key.txt` 传入；屏幕回显时打码。
3. **产物不外发**：生成的部件图/模型只在用户本地输出目录，不粘贴
   到任何公开位置；给用户的确认截图应避免直接展示大块游戏美术
   （可用像素计数、bbox、遮罩比对等数值证据代替）。
4. 图标 ID 清单、层树根参数名等**【本作校准值】**因游戏而异，
   换数据必须重新标定（见 docs/emote-to-cubism-method.md §5）。

## 管线步骤

### 1. 解包（用户数据 → PSB → JSON+图集）

```bash
# 密钥提取见 docs/finding-your-base-key.md；假设已获得 BASE_KEY
python tools/mzs_decrypt.py <game>/wind3d11data/image_info.psb.m  # 验证：输出前4字节=28 B5 2F FD
# 批量解包推荐直接用 FreeMote（外部工具）：
# PsbDecompile.exe info-psb -k <BASE_KEY> -a -o unpacked/image image_info.psb.m
```

产物约定：`<条目>.psb.m.json` + `.psb.m.resx.json` + `<条目>.psb.m/tex#NNN-texture.png`。
设置 `EMOTE_MOTION_DIR` 指向该目录。

### 2. 选条目（代理判断）

- 条目名模式：`<角色><姿态组><_服装差分>`；基文件取名字最短的。
- 先跑 `parse_motion.py` 看 `layer_tree.txt` 与 `metadata_summary.json`，
  确认：层树根参数名（默认 'タイムライン構造'，换数据要重找）、
  图集数量、部件规模。

### 3. 装配 + 生成

```bash
python tools/build_moc3.py            # 静姿 + 眨眼 + 遮罩 → output/
python tools/build_moc3_params.py     # 参数绑定版 → output_m3/
```

生成器内置自检（静态姿势一致性断言）；若断言失败，通常是校准值不适配
该数据——回 docs/emote-to-cubism-method.md §5 逐项重标定。

### 4. 验证（代理可执行的数值证据）

- `python tools/check_moc3.py --verify output_m3/<entry>.moc3`（逐段对齐）
- 浏览器 pixi-live2d-display 加载 model3.json；判定用页内
  `gl.readPixels` 的**非背景像素计数**而非截图（缓存陷阱：换目录/换参数）。
- 逐参数驱动验证：ParamAngleZ=±30 头倾、ParamEyeLOpen 0/0.5/1 眨眼三态、
  ParamMouthOpenY=1 张嘴。

### 5. 常见失败 → 处置

| 症状 | 根因方向 |
|---|---|
| 运行时拒载（File read error） | 段顺序/计数自洽性——先 `check_moc3.py --verify` |
| 只渲染一个部件（碎片） | 索引写成全局偏移（必须局部索引） |
| 整画布黑 | 网格 parent_deformer=-1 且文件含变形器 |
| 部件边缘缺失/整体收缩 | 网格映射用了矩形推导而非 meshMatrix |
| 歪头/整体错移 | 默认帧选择把参数层当动画层 |
| 排序错乱（后发盖脸） | draw_order 用了纯层树序或纯 zorder |

全部语义细节见 `docs/moc3-format-semantics.md` 与
`docs/emote-to-cubism-method.md`——先读文档再动手，不要猜字段。

## 与 Editor 协作（可选路线）

需要"可人工修正的产物"（.cmo3）时走 PSD 路线：`tools/build_psd.py` 生成
合并 PSD → 导入 Cubism Editor（自动网格）→ 变形器/参数可通过外部编辑
API（cubism-mcp 类工具）或人工完成 → Editor 官方导出 moc3。
Editor 真值模型制作菜谱见 `docs/editor-truth-recipe.md`。

## 软件化路线（roadmap）

本 skill 即"agent + MCP"形态的知识层：把上述工具包一层 MCP server
（detect / decrypt / parse / assemble / build / verify 六个 tool），
即可让任意 MCP 客户端代理驱动管线。通用 galgame 解包的长尾格式
建议委托 GARbro 集成，本管线专注 E-mote/MAGES 系的保真转换。
