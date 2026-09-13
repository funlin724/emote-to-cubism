# emote-to-cubism

**E-mote → Live2D Cubism 的结构感知转换工具链与格式文档。**

从 E-mote（M2 Co., Ltd.）游戏数据（PSB 容器 + 层树 + 图集 + 网格 + 参数曲线）
重建为可在 Cubism 运行时加载驱动的标准模型（moc3 + model3.json）。
E-mote → Cubism 的跨引擎完整转换此前无公开先例；本仓库公开的是
**方法、格式语义与工具**，不含任何游戏素材。

## 这是什么 / 不是什么

| 是 | 不是 |
|---|---|
| MzS/PSB/E-mote 容器与结构解析工具 | ❌ 不含任何游戏的解密密钥 |
| moc3 二进制格式的写出器/校验器（逆向实证语义） | ❌ 不含任何游戏美术、模型或素材 |
| 装配器：E-mote 层树 → 世界坐标几何 → moc3 | ❌ 不是"一键转换器"——见下文半自动声明 |
| 格式文档：MzS 容器 / E-mote PSB / moc3 语义 | ❌ 不是通用 galgame 解包器（格式覆盖见下） |

**两条路线，两种担保（诚实分级）**：

- **主接口（担保级，生产可用）：PSB → PSD + 语义清单**。`tools/build_psd.py`
  把 E-mote 条目装配成多外观合并分层 PSD + `membership.json`（层→外观成员表，
  换装差分依据），可直接导入 Cubism Editor，或喂给 psd2live 等自动绑骨工具。
  这一段是纯数据操作，不依赖 Editor，产物已被第三方工具链（psd2live）实际
  加载验证。
- **实验路线（运行时可加载，不担保可修正）：直写 moc3**。`tools/build_moc3*.py`
  从 PSB 语义直接生成 moc3——眨眼 4 态 / 视线 3 态 / 口型 / 头身倾斜已过
  运行时帧缓冲验证；但 **Editor 无法打开该产物**（不可人工修正）、与官方
  4.0 约定的往返对齐未闭环、物理（velvety→physics3.json）未落地。

管线整体定位：**半自动工具链 + 详细格式文档**，不是一键转换器。

## 仓库结构

```
├── docs/                        格式文档（最高价值、零素材、零密钥）
│   ├── mzs-container-format.md      MzS 容器布局与 MDF 加密算法
│   ├── finding-your-base-key.md     如何从自己的游戏提取密钥（自行操作）
│   ├── emote-psb-structure.md       E-mote PSB 层树/icon/网格结构
│   ├── moc3-format-semantics.md     moc3 二进制语义（逆向实证，含易错表）
│   ├── emote-to-cubism-method.md    跨引擎翻译全流程与踩坑清单
│   └── editor-truth-recipe.md       用官方 Editor 制作接线真值模型
├── tools/                       工具链（密钥外部输入，用户自带游戏数据）
├── skills/emote-to-cubism/      Agent Skill：让 AI 代理驱动整条管线
├── examples/                    （规划）自绘占位角色端到端示例
└── .github/workflows/           CI 资产守卫：检测到游戏素材即 fail
```

## 快速开始（需自备游戏数据）

前提：你拥有合法游戏副本。本仓库不提供、不接受任何游戏资源。

```bash
# 0. 依赖：Python 3.10+，pip install pillow numpy psd-tools zstandard

# 1. 用 FreeMote 解包（或 mzs_decrypt + PsbDecompile）得到：
#    <条目>.psb.m.json / .psb.m.resx.json / tex#NNN-texture.png
export EMOTE_MOTION_DIR=/path/to/decompiled/motion
export EMOTE_ENTRY=char_a            # 条目名

# 2. 解析条目：部件切割 + 清单 + 曲线 + 层树
python tools/parse_motion.py char_a out/char_a

# 3. 主接口：多外观合并 PSD（可导入 Cubism Editor / 喂给 psd2live 等）
python tools/build_psd.py --variants variants.json --out char_all.psd
#    （variants.json 结构见 tools/build_psd.py 文件头注释）

# 3'. 实验路线：直写 moc3（运行时可加载；Editor 不可编辑、物理未做）
python tools/build_moc3.py           # → output/char_a.moc3（静姿+眨眼+遮罩）
python tools/build_moc3_params.py    # → output_m3/（+头身倾斜/口型/眼球）

# 4. 验证：浏览器 pixi-live2d-display 加载 *.model3.json
#    （验证方法论见 docs/moc3-format-semantics.md §6）

# 5. moc3 结构校验 / 真值对照
python tools/check_moc3.py --verify output_m3/char_a.moc3
```

注意：`tools/` 中标注**【本作校准值】**的常量（图标 ID 清单、隐藏件、
层树根参数名等）来自某一个具体游戏的实测，换游戏必须按
[docs/emote-to-cubism-method.md](docs/emote-to-cubism-method.md) §5 重新标定。

## MzS 解密的密钥来源

`tools/mzs_decrypt.py` **不内置任何密钥**。用 `--key` 参数、
`MZS_BASE_KEY` 环境变量或本地 `mzs_key.txt`（已 gitignore）提供，
提取方法见 [docs/finding-your-base-key.md](docs/finding-your-base-key.md)。

## 致谢与相关项目

- [FreeMote](https://github.com/uanu2002/FreeMote)（E-mote PSB 工具链）——
  解包与回封的基础设施，MDF 算法的参考实现
- [py-moc3](https://pypi.org/project/py-moc3/)——moc3lib 的基座
  （本仓库修复了段顺序/计数字段等写出 bug，见 docs）
- [psd2live](https://github.com/psd2live/psd2live)（PSD → 自动绑骨 → cmo3）——
  与本项目**互补**：psd2live 解决"从命名 PSD 自动建模"；本项目解决
  "从 E-mote 游戏数据（结构/参数/曲线已在二进制内）保真搬运"
- [GARbro](https://github.com/morkt/GARbro)——通用视觉小说归档浏览
  （不识别 MzS，但适合其他格式的图片转换）

## 法律声明（Legal）

本项目用于格式研究与互操作，**不包含、不分发任何游戏素材**：无图集、
无部件图、无 moc3/psd/cmo3 成品、无解密密钥。详见
[ASSETS_POLICY.md](ASSETS_POLICY.md) 与 [NOTICE.md](NOTICE.md)。

- 逆向工程以**互操作性**为目的在多地受法律保护（如 Sega v. Accolade 判例、
  欧盟软件指令第 6 条）；格式事实与方法论不受版权保护。
- 使用本工具即表示你承诺：使用自己合法获得的游戏数据，遵守游戏 EULA 与
  所在地法律，产出仅限个人研究用途，不再分发任何衍生游戏素材。
- 本项目与 E-mote / M2 Co., Ltd.、Live2D Inc. 及任何游戏厂商无关联、
  未获其认可。
- 本项目不提供法律意见；商业化使用前请咨询律师。

## 许可证

代码：MIT（[LICENSE](LICENSE)）；文档：CC-BY-4.0。
