# moc3 格式语义（逆向实证）

> 从零构建 moc3 的权威参考。全部结论经"官方 Editor 导出真值 + Web 运行时
> 帧缓冲实测"双重验证（2026-09-05 定版）。读者对象：moc3 写出器作者。

## 1. 文件布局与内存映射

```
[0..64)      Header: "MOC3" magic + 版本字节 + endian + padding
[64..704)    Section Offset Table: 160 × uint32
[704..832)   Count Info: 23 × uint32 + padding
[832..)      Body sections（各段 64 字节对齐）
```

**核心（WASM）按规范段顺序线性映射文件，不按 SOT 找段。** 写出器段顺序
错 → 核心把错误字节当段数据 → 求值清零/碎片/RangeError 拒载。

- 权威段顺序来源：社区逆向规范 `moc3.hexpat`（moc3ingbird 项目）。
  验证方法：按规范顺序+对齐规则线性走文件，SOT 逐项比对——
  Editor 真值/官方样例模型 0 mismatch 才算修对。
- **计数/画布段位置由 SOT[0]/SOT[1] 给出，不是固定偏移**：
  Cubism 5.3 导出（版本字节 6）SOT[0]=5824；硬编码 1984 会把计数整片读成 0。
- 对齐规则：runtime 段 8B/对象；ids 64B/对象**不另对齐**；其余段写前对齐 64 字节。
- 版本字节：3.0→1, 3.3→2, **4.0→3**, 4.2→4, 5.0→5, 5.3→6。
  4.0 段集 = 2 + 100；V3.03+ 需保留 `additional.quad_transforms` 槽位
  （写 0 个也要占位）。

## 2. 关键字段语义（易错表）

| 字段 | 正确语义 | 备注 |
|---|---|---|
| `art_mesh.vertex_counts` | **顶点池大小**（=UV 对数） | 与 `position_index_counts` 极易写反 |
| `art_mesh.position_index_counts` | **索引表长度**（3×三角形数） | |
| `art_mesh.vertex_uv/position` | 文件内为归一化坐标 | 画布像素 = v×ppu + canvas/2；**y 向下** |
| `position_index.indices` | **各网格局部索引**（0 基于自身顶点池） | 全局偏移 → 除第一格外全部越界，WebGL 静默跳绘 →"碎片现象"真因 |
| keyform 槽位 | 64 字节（16 float）对齐，可含空洞 | 真值实证：warp 50f→80f、mesh 8f→16f |
| `KEYFORM_POSITIONS` count | 池实际 float 数（含对齐尾） | |
| 参数无绑定 | `keyform_binding_begin_indices=-1, counts=0` | 写 0 会被当 binding 0 |
| 网格无变形器 | `parent_deformer_indices=-1` 合法 | **仅当文件内 DEFORMERS=0**；文件里有变形器时 -1 危险（整画布黑） |
| runtime space 段 | 全零即可 | |
| `draw_order_group` 段 | 可不写（counts=0）；写则 max 在 min **之前** | |
| model3.json | 必须带 `Groups` 键 | 缺 → pixi 运行时每帧抛错黑屏 |

## 3. 绑定系统（keyform binding）

```
parameter p ──keyform_binding_begin_indices[p] / counts[p]──▶ binding b（直接索引）
binding b ──keys_begin_indices[b] / keys_counts[b]──▶ keys.values 池
mesh ──keyform_binding_band_indices──▶ band ──begin/counts──▶ keyform_binding_index.indices ──▶ binding id
mesh.keyform_begin_indices = 该 mesh 第一个 keyform 在 art_mesh_keyform 数组中的槽位号
mesh.keyform_counts = 键数
art_mesh_keyform 槽位 = (opacities, draw_orders, keyform_position_begin_indices)
keyform_position_begin = keyform 顶点在 keyform_position.xys 池中的 float 偏移
```

- 求值：参数值落在 keys 上按权重混合各 keyform（位置与不透明度同时混合）。
  **默认参数值所在的键必须放默认姿势**，否则静止渲染偏移。
- 无绑定网格：band 0（空带，begin=0,count=0）+ keyform_counts=1（基础姿势）。
- 同一 band 可被多网格共享；同一 binding 可被多 band 引用。
- **多 binding 张量积槽位序（实测）**：`flat = i0 + n0*(i1 + n1*(...))`，
  即 **bindings[0] 最快、最后一个 binding 最慢**（列主序）。
  mesh.keyform_counts = 各 binding 键数之积。
- ⚠ 绑定深度：官方 Editor 样例最深 3 层；实测 5 层嵌套在部分运行时
  触发疑似未定义行为，保守取 ≤3。

## 4. 遮罩系统（drawable mask）

```
counts[DRAWABLE_MASKS] = N（总条目数）
drawable_mask.art_mesh_indices = [被用作蒙版的网格 id 列表]
art_mesh.mask_begin_indices[i] / mask_counts[i] = 网格 i 的蒙版条目区间
```

- 语义：蒙版网格的**不透明区域**裁剪被遮网格（E-mote stencil 的对应物）。
- 典型应用：瞳/高光裁进眼白形状；眨眼时白有两态时蒙版取并集（mask_count=2）。
- drawable_flags=4（NORMAL）；蒙版载体不设特殊 flag，由引用关系决定。

## 5. moc3 不变量速查（写生成器时的 checklist）

- [ ] counts[23] 与各段长度自洽（尤其 KEYFORM_BINDING_BANDS 空带也要 =1）
- [ ] 段顺序 = hexpat 规范；每段 64B 对齐（ids 除外）
- [ ] KEYFORM_POSITIONS = 池实际 float 数；keyform 槽位 16-float 对齐
- [ ] art_mesh_keyform 三数组与槽位一一对应
- [ ] position_index = 局部索引；值域 ≤32767（i16）
- [ ] 参数无绑定 begin=-1；V4 格式保留 additional.quad_transforms 槽位
- [ ] model3.json 带 Groups；draw_orders ≥500 起始（0 起始会掉出渲染序列）
- [ ] 顶点语义：画布中心原点、y 向下、单位 = ppu 像素
- [ ] 纹理建议 POT（NPOT 在 WebGL2 实测可用，WebGL1 未验证）

## 6. 验证方法论（按可信度排序）

1. **pixi 帧缓冲预言机**（唯一渲染判据）：pixi-live2d-display 页内
   `gl.readPixels` 导出 PNG。页面截图会被视口裁切/合成干扰；桌面 Viewer
   的 JOGL 画布外部抓不到。
   - moc3 有**浏览器启发式缓存**——每次实验必须换目录或换 URL 参数。
   - 页面在后台时 rAF 冻结 → 保持面板可见，或用同步 `render()`。
2. **drawMesh 审计**：钩渲染器原型记录 (纹理号, 索引数, 顶点数, UV/位置, opacity)。
3. **软渲染对账**：numpy 逐三角形光栅化 moc3 文件，与 CPU 装配渲染逐像素比
   ——区分"文件错"还是"渲染管线错"的唯一手段。
4. **Editor 真值对照**：官方 Editor 导出的最小模型（1 网格+1 变形器+1 绑定）
   是绑定接线的权威参照——见 [editor-truth-recipe.md](editor-truth-recipe.md)。
5. **池清零对照 / 逐组隐藏 / 常量位置池 / MVP 捕获**：定位求值链路问题的
   四个关键判定实验。
6. ⚠ 核心包装层 getter（getDrawableVertexPositions 等）返回解析时拷贝，
   部分 getter 在文件语义错误时静默返回垃圾——只能当线索，不能当判据。
