# E-mote Motion PSB 结构

> E-mote（M2 Co., Ltd.）模型的 PSB v3 容器内组织。数据形态以
> FreeMote `PsbDecompile` 的产物（JSON + resx.json + 图集 PNG）为基准描述。

## 1. 产物形态

一个角色条目（如 `char_a.psb.m`）解包后为三件套：

| 文件 | 内容 |
|---|---|
| `<条目>.psb.m.json` | 模型结构（层树/参数/图集索引） |
| `<条目>.psb.m.resx.json` | 资源表；顶点等大数值数组以 `ExtraFlattenArrays` flatten 存放 |
| `<条目>.psb.m/tex#NNN-texture.png` | 4096px 级 RGBA 图集（1–4 张） |

引用规则：JSON 内 `#resource@N` 字符串指向 `resx.json` 的
`ExtraFlattenArrays['@N']`（注意键带 `@` 前缀）。

## 2. JSON 顶层

`easing / id / label / metadata / object / screenSize / source / spec /
stereovisionProfile / version`。

## 3. metadata（控制/参数层）

- `blinkParameter`：眨眼时序（openTime/closeTime/holdTime/interval）
- `eyeControl / eyeParameter / eyebrowControl / mouthControl`：眼/眉/嘴控制
- `clampControl`：把逻辑变量（如 head_LR/UD）钳制映射到参数链
- `hairControl / bustControl / loopControl / orbitControl / mirrorControl`
- `velvety*Control`（jelly/string/wind/stretch/synchronize/partsTree 等）：
  **Velvety 物理模拟**——头发/饰品摆动体系
- `charaProfile`：身高、`pixelMarker`（眼/嘴/胸/上下边界像素标定，
  可用于装配精度校准）、`collisionMap`
- `customPartsList` / `variableList` / `timelineControl`

## 4. object（部件层树 + 动画曲线）

- 分组：`face_parts / body_parts / ex_body_a~e / ex_parts_a~e / general_obj_a` 等；
- 每组 `motion` 下是**命名参数**（日文部件/表情名），参数内是 `layer` 树；
- `layer` 节点：`children` + `frameList[{time, type, content{icon, src, mask, coord, angle, opa}}]`
  + `transformOrder` + `inheritMask` + `stencilType`（模板遮罩）+ `meshCombine`
  + `parameterize`（参数化链标记）——关键帧时间线结构；
- `type=3` 的层是**组挂载点**：其默认帧 `content.icon` 即要嫁接的组参数名
  （装配器按此递归展开，需防环）；
- `mask` 字段的大数（如 33554432）是通用标志位，与可见性无关。

### parameterize 参数化轴（原作时序还原的钥匙）

参数树节点可带 `parameterize` 标记，指向同参数的 `parameter[]` 数组：

```
parameter[] = {id, rangeBegin, rangeEnd, division}
```

- **时间换算**：参数值 v 对应的时间轴位置 `t = (v − rangeBegin) / (rangeEnd − rangeBegin) × division`。
  层内 `frameList[].time` 就在这个刻度上——重放某参数值时按 t 取帧/插值
  （`build_moc3_params.py` 的 ReplayWalker 即此原理）。
- **语义锚**：`metadata.variableMetaInfoList[]` 的 keyframes 标签给出
  参数刻度上的语义点（如眨眼轴的睁/半/闭、表情轴的通常/怒/哀/笑），
  是把数值刻度翻译回语义状态的权威依据。
- 原作时序（眨眼间隔/闭眼时长等）在 `metadata.blinkParameter`；
  把两者组合即可在 Cubism 侧还原原作的驱动时序（motion3/exp3 或运行时驱动）。

## 5. source.tex#NNN（图集 + 逐部件几何）

每部件 `icon` 条目：

- `left/top/width/height`：图集 UV 矩形
- `originX/originY`：原点（锚点），可为负（矩形外锚定是有意设计）
- `metadata.zorder`：**组内局部**层级序（不能作全局排序键！）
- `mesh`：
  `vertices`（图标内归一化 [0..1]）、`meshMatrix`（[A,B,C,D,E,F] 仿射，
  E,F=−A/2,−D/2 即锚点居中）、`tristrip`（三角条带索引）、
  `convexHulls / concaveHulls`（凸/凹包络，备援三角化来源）、
  `minAreaRect{angle,cx,cy,w,h}`（仅编辑器元数据，不参与 UV/位置）

### 网格几何的权威映射约定

```
pos_local = meshMatrix · (u, v)          # vertices 归一化于"原始图层图像 A×D"
矩形内像素 = pos_local + (originX, originY)
UV = (left + 矩形内.x)/atlasW, (top + 矩形内.y)/atlasH
```

⚠ 不要用图标矩形 W,H 代替 meshMatrix 的 A,D——两者仅在少数特例相等；
用矩形推导会整体收缩且外圈纹理带永不采样。

### tristrip 位型陷阱

FreeMote 把 int32 数值按 float32 位模式写出（非规格化小数，如 7.29e-44
= 位型整数 52）。直接 `round()` 会全变 0 → 条带全退化 → 静默回退凸包扇，
凹形网格（睫毛弧线/嘴线/阴影）整片采错纹理。须按位重解释还原
（`tools/emote_assemble.py` 的 `toint()` / tristrip 解码）。

### icon 的两种形态

- `"0031"`：字典键，查 `source.tex#NNN.icon`；
- `"x:y:w:h"`：rect 形式。rect + `src:"blank"` = **摆动物理代理网格**，
  不是可见画，不渲染是正确的。
