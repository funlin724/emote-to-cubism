# E-mote → Cubism 转换方法论

> 跨引擎翻译的全流程、装配规则、参数映射与踩坑清单。
> E-mote 与 Cubism 是同一物种（2D 骨骼网格动画）的两家实现，
> 因此映射是"两个已文档化系统"间的翻译，有据可依。

## 1. 管线总览

```
游戏档案（MzS 等容器加密）
   │  tools/mzs_decrypt.py（密钥用户自带，见 docs/finding-your-base-key.md）
   ▼
Emote PSB（.psb.m + .json + .resx.json + tex#NNN-texture.png）
   │  ① tools/parse_motion.py <条目> <输出目录>    部件切割 + 清单 + 曲线
   │  ② tools/emote_assemble.py  Assembler         层树装配 → 世界坐标实例
   │  ③ tools/build_moc3.py / build_moc3_params.py moc3 生成（静姿 / 参数绑定）
   ▼
moc3 + model3.json + cdi3.json + 贴图
   │  ④ pixi 帧缓冲预言机验证（docs/moc3-format-semantics.md §6）
   │  ⑤ CubismViewer5 / 官方运行时加载复验
   ▼
浏览器/Viewer 中可动的完整模型
```

**两条出口，两种担保**：

- **主接口（担保级）：`tools/build_psd.py` 生成多外观合并 PSD**（+
  membership.json 层→外观成员表）→ 导入 Cubism Editor（自动网格/裁剪）→
  变形器/参数经外部编辑 API 或人工完成 → 官方导出 moc3。产物（.cmo3）
  可人工修正，且 PSD 可直接喂给 psd2live 等自动绑骨工具（已被其实际加载
  验证）。管线依赖 GUI（Windows + Editor）——**项目定位是半自动工具链
  而非一键转换器**。
- **实验路线：`tools/build_moc3*.py` 直接写出 moc3**（静态姿势/眨眼/口型/
  头身倾斜已运行时验证）。免 Editor 全自动，但产物 Editor 拒载（不可人工
  修正）、与官方 4.0 约定的往返对齐未闭环、物理转换未落地。

## 2. 装配规则（emote_assemble.py）

1. **层树**：`object.<组>.motion.<参数>.layer` 为根；`type=3` 层是组挂载点，
   默认帧 `content.icon` 即要嫁接的组参数名（防环：stack）。
2. **默认帧选择**（最多 bug 的地方）：
   - **参数层**（≥3 个内容帧、帧键 ⊆ {coord,angle,mask,哑图标}、帧间恒定、
     无 tex 图标/opa 切换）→ 在内容帧里（排除 t=201 清理帧）取**最接近中性位**
     者（min |coord_x|+|coord_y|+|angle|）。⚠ move_UD 等参数范围 [-100,+100]
     的层 t=0 是参数边界而非中性位。
   - **布局层**（≤2 个内容帧）→ 取最小时间帧。
   - **动画层**（tex 图标/opa 切换）→ 取最小时间帧。
   - `mask` 键是模板遮罩引用，不能因它把参数层误判为动画层
     （曾致整头歪 5.7°）。
3. **世界变换**：`world = 父world ∘ T(coord.xy) ∘ R(angle)`；
   moc3 归一化：`v = (px − min − 1.05W/2) / (1.05W)`，y 向下，ppu = 1.05W。
4. **遮挡序**：渲染顺序 = **zorder 全局主序 + 层树遍历序次键**。
   metadata.zorder 只是组内局部序，纯层树序和纯 zorder 都有反例；
   最终裁判是帧缓冲实测。
5. **质检**：按 UV 把部件贴回图集与原图逐像素比对（`tools/qc_parse.py`
   可机检）；部件数与 JSON `source.tex#*.icon` 总数一致。
6. **UV 足迹（PSD 打包器放置）**：`Assembler.footprint()` 返回网格可见
   三角形实际采样的矩形子区域（足迹）。PSD 路线的图层内容应按足迹 1:1
   裁剪、放置在足迹顶点世界包围盒内——整矩形裁剪拉伸到全网格框会把
   形变件足迹外的美术卷进图层并压扁。网格轮廓伸出足迹的件（靠 UV 钳制
   裙边三角形画的 V 形下巴尖等）退回全网格框并按钳制 UV 补绘裙边。

## 3. E-mote → Cubism 参数映射

| Cubism 参数 | Emote 源 | 实现方式 |
|---|---|---|
| ParamEyeLOpen/ROpen | eyeControl 图标序列 | 不透明度绑定 3/8 键 |
| ParamAngleZ | head_slant 链（角度关键帧） | 重放链 → 位置 keyform |
| ParamBodyAngleZ | body_slant 链 | 同上 |
| ParamEyeBallX/Y | 通常无源几何（链为零变换） | 平移 keyform 白名单件 |
| ParamMouthOpenY | 口型图标切换（0000↔0002 类） | 2 键不透明度绑定 |
| 转头 X/Y | 数据里通常**不存在**几何 | 声明不绑定，或伪转头（平移近似） |
| 呼吸 | loopControl | 运行时驱动或 physics |
| 摆动物理 | velvety*Control（velvetyPartsTree 链） | 换算 physics3.json 摆锤 |

关键洞察：**源数据的表情多为"部件切换"而非顶点形变**（口型 7 形、眨眼
多态都是独立部件图）。这正是 Cubism"部件透明度关键帧"的能力范围——
不需要顶点级编辑就能完成大部分表情绑定。

## 4. 重放法（参数位置 keyform 的提取算法）

`tools/build_moc3_params.py` 的 ReplayWalker：每个 moc3 keyform =
"把某参数链上所有 parameterize 层的默认帧换成参数值 v 对应时间轴位置的
帧后重新装配"得到的网格顶点。内置自检：空覆写结果必须与静姿装配器
逐实例一致；默认键组合槽位必须等于基础姿势。

## 5. 通用规则 vs 单游戏特例（适配层）

**可复用的通用规则**（写进文档/代码默认值）：
MzS/MDF 加密结构、PSB 层树语义、icon/mesh/UV 约定、默认帧规则、
moc3 全部格式语义、参数映射方法论。

**单游戏校准值**（代码中标注【本作校准值】的常量，换游戏必须重标定）：
- 图标 ID 清单（眨眼态/口型变体/蒙版载体/剪影瓦片——**图标 ID 不跨文件稳定**）
- 层树根参数名、组挂载点细节
- 隐藏件清单、DEGENERATE_UV 白名单、PIECE_NUDGES 微调
- charaProfile 头部标定反推的补偿量

分辨方法：换一份独立的 E-mote 数据重跑，能自然跑通的部分是通用规则，
需要手工校准的部分属于特例——把它们参数化或集中到适配层。

## 6. 踩坑清单（按代价排序，全部真实踩过）

1. **写出器段顺序错** → 一切从零构建的文件都坏；12 组变异实验全部白做。
   修法：对 hexpat 规范 + SOT 走查（见 docs/moc3-format-semantics.md）。
2. **索引写成全局偏移** → 只有第一网格渲染（"碎片现象"数天真因）。
3. **计数字段语义写反**（vertex_counts / position_index_counts）→ 顶点视图越界。
4. **网格映射用矩形推导**（代替 meshMatrix）→ 整体收缩 + 边缘缺失；
   此后打的全部位置补丁都是这个 bug 的补偿——根因修复后应全部清空重审。
5. **默认帧选型错误**（mask 键误判/返回值契约错配）→ 歪头 + 整体错移。
6. **测试页在首帧前读 model.width/height** → bounds 未就绪 → 拟合错位。
   修：rAF 两帧后按 internalModel.originalWidth/Height 拟合。
7. **浏览器 moc3 启发式缓存** → 每次实验必须换目录或换 URL 参数。
8. **pixi eyeBlink 每帧覆写眨眼参数** → 调试读参数必须 raw 直写。
9. FreeMote 的 int 数值常写成 float32 位模式（`toint()` 还原）；tristrip
   转三角形需跳过退化桥；子条带环绕方向不一致需统一有向面积。
10. 中文路径导致桌面 Viewer 读文件失败；`python -m http.server` 单线程
    会被 keep-alive 阻塞产生随机假失败（用线程化服务器 + no-cache 头）。
11. 渲染问题的最终裁判是帧缓冲实测，不是纸面推理——多次"理论上应该对"
    的排序方案被帧缓冲证伪。

## 7. 已知边界

- **默认帧启发式是单游戏校准**：装配器的"最近中性帧/最小时间帧"规则在
  新世代单根树条目上验证成立；老世代条目（多根 layer 树、
  variableList/selectorControl 变量系统、type0/1 帧类型、opa 0-255 父子
  继承相乘、绘制序=链上 coord[2] 累加、层帧级 mesh bp 形变）需要按引擎
  语义**精确求值**——FreeMote 的 `StaticMotionPainterCore` 是权威参考实现，
  仓库内的正式路径是 `tools/exact_extract.py`（门面替换 build_psd 的装配器），
  全部差异都是静默失败（不报错、只出错图），换游戏后务必做渲染对照验收
  （官方运行时截图 + 固定件锚点对齐 + 分区覆盖探查）。
- 老世代条目常拆成多部件（时间线/体/头/头部差分），用
  `tools/merge_emote_parts.py` 合并为单条目后再进管线；合并配方
  （组名并集、纹理改名+icon 分段重排、src 改写、多根拼接）见该工具文档。
- moc3 网格蒙版在部分 web 运行时对自建文件映射错位（对照官方模型实证），
  依赖运行时遮罩的方案需实测兜底。
- 顶点级网格动画与部件乘色两处只能近似（保真度天花板）。
- 物理（velvety）→ physics3.json 换算未完全落地。
- 纯 moc3 写出路线的产物 Editor 拒载（无法打开修正）；需要可修正产物时
  走 PSD + Editor 路线。
