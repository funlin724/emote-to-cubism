# emote-to-cubism

E-mote → Live2D Cubism 的结构感知转换工具链与格式文档。

从 E-mote（M2 Co., Ltd.）游戏数据重建可在 Cubism 运行时加载驱动的标准模型。
E-mote → Cubism 的跨引擎完整转换此前没有公开先例，本仓库公开的是方法、
格式语义和工具，不含任何游戏素材。

## 这是什么，不是什么

| 是 | 不是 |
|---|---|
| MzS / PSB / E-mote 容器与结构解析工具 | 不含任何游戏的解密密钥 |
| moc3 写出器与校验器，语义全部经逆向实证 | 不含任何游戏美术、模型或素材 |
| 装配器：E-mote 层树 → 世界坐标几何 → moc3 | 不是一键转换器，见下 |
| 格式文档：MzS 容器、E-mote PSB、moc3 语义 | 不做通用 galgame 解包 |

## 两条路线

**主接口：PSB → PSD。** `tools/build_psd.py` 把 E-mote 条目装配成多外观
合并分层 PSD，附带 membership.json 记录每个图层被哪些外观共享，是换装
差分的开关依据。产物可以直接导入 Cubism Editor，也可以喂给 psd2live 这类
自动绑骨工具，并已经过 psd2live 实际加载验证。这一段是纯数据操作，
不依赖 Editor。

**实验路线：直写 moc3。** `tools/build_moc3*.py` 从 PSB 语义直接生成
moc3。眨眼四态、视线、口型、头身倾斜都过了运行时帧缓冲验证。但 Editor
打不开这个产物，无法人工修正；与官方 4.0 约定的往返对齐尚未闭环；物理
转换未做。

所以整体定位是半自动工具链加详细格式文档，clone 即跑的只有数据侧，
Editor 侧的操作要照文档手动完成。

## 仓库结构

```
├── docs/                        格式文档，零素材、零密钥
│   ├── mzs-container-format.md      MzS 容器布局与 MDF 加密算法
│   ├── finding-your-base-key.md     如何从自己的游戏提取密钥
│   ├── emote-psb-structure.md       E-mote PSB 层树、icon、网格结构
│   ├── moc3-format-semantics.md     moc3 二进制语义，含易错表
│   ├── emote-to-cubism-method.md    跨引擎翻译全流程与踩坑清单
│   └── editor-truth-recipe.md       用官方 Editor 制作接线真值模型
├── tools/                       工具链，密钥外部输入
├── skills/emote-to-cubism/      Agent Skill，让 AI 代理驱动管线
├── examples/                    规划中：自绘占位角色端到端示例
└── .github/workflows/           CI 资产守卫，检测到游戏素材即 fail
```

## 快速开始

前提：你拥有合法游戏副本，并已用 FreeMote 解出目标条目，得到
`<条目>.psb.m.json`、`<条目>.psb.m.resx.json` 和 `tex#NNN-texture.png` 图集。

从 info+body 归档提取条目：
`PsbDecompile.exe info-psb -k <基础密钥串> -l 131 <xxx_info.psb.m>`
（body 必须与 info 同目录同名）。注意 FreeMote 顶层的 `-k` 是 uint PSB key
（只对 .emt/老 PSB 生效），与 info-psb 子命令的字符串 `-k` 语义不同，
误用会直接抛异常。老世代（2015 前后）条目常拆成多个部件文件，需先合并
为单条目——见 docs/emote-to-cubism-method.md 的适配层说明。

```bash
pip install -r requirements.txt
export EMOTE_MOTION_DIR=/path/to/decompiled/motion
export EMOTE_ENTRY=char_a

# 部件切割、清单、曲线、层树
python tools/parse_motion.py char_a out/char_a

# 主接口：多外观合并 PSD
python tools/build_psd.py --variants variants.json --out char_all.psd

# 实验路线：直写 moc3
python tools/build_moc3.py            # 静姿 + 眨眼 + 遮罩
python tools/build_moc3_params.py     # 加头身倾斜、口型、眼球

# moc3 结构校验
python tools/check_moc3.py --verify output_m3/char_a.moc3
```

渲染验证用浏览器加载 pixi-live2d-display，判定方法见
docs/moc3-format-semantics.md 第六节。

variants.json 的结构见 tools/build_psd.py 文件头注释。tools 里标注
【本作校准值】的常量来自某个具体游戏的实测，换游戏要按
docs/emote-to-cubism-method.md 第五节重新标定。

## 密钥

`tools/mzs_decrypt.py` 不内置任何密钥。基础密钥串用 `--key` 参数、
`MZS_BASE_KEY` 环境变量或本地 `mzs_key.txt` 提供，提取方法见
docs/finding-your-base-key.md。`mzs_key.txt` 已被 .gitignore 排除，
不要提交。

## 致谢与相关项目

- [FreeMote](https://github.com/uanu2002/FreeMote)——E-mote PSB 工具链，
  解包回封的基础设施，MDF 算法的参考实现
- [py-moc3](https://pypi.org/project/py-moc3/)——moc3lib 的基座，本仓库
  修复了它的段顺序和计数字段等写出 bug，见 docs
- [psd2live](https://github.com/psd2live/psd2live)——命名 PSD 自动建模
  直出 cmo3。与本项目互补：它解决从 PSD 建模，本项目解决从 E-mote
  游戏数据保真搬运，它的输入可以由本仓库的主接口直接生成
- [GARbro](https://github.com/morkt/GARbro)——通用视觉小说归档浏览，
  不识别 MzS，适合其他格式的图片转换

## 法律声明

本仓库不包含、不分发任何游戏素材：没有图集、部件图、模型成品，也没有
解密密钥。详见 ASSETS_POLICY.md 和 NOTICE.md。

逆向工程以互操作为目的在多地受法律保护，格式事实与方法论不受版权保护。
使用本工具即表示你承诺：只处理自己合法获得的游戏数据，遵守游戏 EULA 和
所在地法律，产出仅限个人研究，不再分发任何衍生素材。

本项目与 E-mote / M2 Co., Ltd.、Live2D Inc. 及任何游戏厂商均无关联。
本文不构成法律意见，商业化前请咨询律师。

## 许可证

代码 MIT，见 LICENSE；文档 CC-BY-4.0，全文见
https://creativecommons.org/licenses/by/4.0/legalcode
