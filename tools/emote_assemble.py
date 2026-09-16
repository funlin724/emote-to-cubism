# -*- coding: utf-8 -*-
"""Emote 层树 → 默认姿势装配器（静姿模型的数据侧）。

装配规则（实测确认，详见 docs/emote-to-cubism-method.md）：
1. 部件网格：icon.mesh.vertices 为图标矩形内归一化 [0..1] 坐标，
   meshMatrix=[A,B,C,D,E,F] 将其映射到"锚点相对"像素空间；
   tristrip（int 数组，FreeMote 序列化成了 float32 位模式，需按位重解释）
   是 Emote 自带的三角条带索引，直接转三角形列表，无需自行三角化。
2. 层树：object.<group>.motion.<param>.layer 为树根；type=3 的层是"组挂载点"，
   其默认帧 content.icon 即要嫁接的组参数名（全部可在 registry 解析到唯一组）。
3. 变换：每层默认帧 content.coord=[x,y,z]（平移）+ content.angle（度，旋转），
   父链仿射累积；icon 锚点放在层原点，即 world = parent_world ∘ T(coord) ∘ R(angle)。
4. 默认姿势 = 每层 frameList 中时间最小的帧；content.opa 为不透明度（缺省 1）。
5. footprint()：UV 足迹静态放置（PSD 打包器用）——网格只在可见三角形实际
   采样的矩形子区域内采图，供打包器 1:1 裁剪放置，避免形变件美术被压扁。

数据来源：FreeMote PsbDecompile 产物（同 parse_motion.py 的目录约定）。
源目录 = 环境变量 EMOTE_MOTION_DIR（缺省仓库内 data/motion）。

**适配层说明**：本文件底部的 DEGENERATE_UV_*/PIECE_NUDGES/hidden_icons 等
常量是对单个游戏实测出的"本作校准值"（图标 ID、口型变体清单等属于具体
游戏的指纹，E-mote 规范本身不定义它们）。换游戏复用时需按该游戏的
图集/部件实际情况重新标定——见 docs/emote-to-cubism-method.md 第 5 节
"通用规则 vs 本作特例"。
"""
import json, os, re, struct, math

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SRC = os.environ.get('EMOTE_MOTION_DIR') or os.path.join(ROOT, 'data', 'motion')
BASE = os.environ.get('EMOTE_ENTRY')  # 条目名（如 char_a），由调用方提供

DEGENERATE_UV_ICONS = {'0039', '0040'}
# 0039 圆点行在矩形下半部：重映射收紧到圆点行，
# 否则矩形上缘深色行被拉成楔形（本作高光图标的源矩形与内容错位特例）
DEGENERATE_UV_V_RANGE = {'0039': (3, 9)}
# 0039 源矩形 top 比其实际圆点内容偏高 5px 的校正
DEGENERATE_UV_V_OFFSET = {'0039': 5.0}
ICON_RE = re.compile(r'^\d{4}$')

# 部件微调 (dx, dy, rot_deg[, scale, px, py])：源像素平移 + 绕锚点旋转。
# 【本作校准值】眼部各件的落点在源布局下即正确（迭代证实各档偏移均劣于源位），
# 全部归零保留作为模板：若其它游戏出现同类偏差，在此按图标 ID 微调。
PIECE_NUDGES = {
    '0031': (0, 0, 0), '0035': (0, 0, 0),
    '0047': (0, 0, 0), '0051': (0, 0, 0),
    '0087': (0, 0, 0), '0088': (0, 0, 0),
}


def load_model(base):
    d = json.load(open(os.path.join(SRC, base + '.psb.m.json'), encoding='utf-8-sig'))
    resx = json.load(open(os.path.join(SRC, base + '.psb.m.resx.json'), encoding='utf-8-sig'))
    return d, resx


def _deref_factory(resx):
    # 部分老世代 FreeMote 产物没有 ExtraFlattenArrays 键（JSON 亦无 #resource@
    # 引用），缺失时按空表处理
    flat = resx.get('ExtraFlattenArrays') or {}

    def deref(v):
        if isinstance(v, str) and v.startswith('#resource@'):
            return flat['@' + v[len('#resource@'):]]
        return v

    return deref


def toint(x):
    """FreeMote 把 int 以 float32 位模式写出（非规格化小数），按位重解释。"""
    if isinstance(x, float):
        if x == 0.0:
            return 0
        if abs(x) < 1e-35:
            return struct.unpack('<i', struct.pack('<f', x))[0]
        return int(round(x))
    return x


class Affine:
    """2x3 仿射：world = (a*x + c*y + e, b*x + d*y + f)。"""

    __slots__ = ('a', 'b', 'c', 'd', 'e', 'f')

    def __init__(self, a=1.0, b=0.0, c=0.0, d=1.0, e=0.0, f=0.0):
        self.a, self.b, self.c, self.d, self.e, self.f = a, b, c, d, e, f

    def compose(self, r):
        """self ∘ r（先应用 r 再应用 self）。"""
        return Affine(
            self.a * r.a + self.c * r.b,
            self.b * r.a + self.d * r.b,
            self.a * r.c + self.c * r.d,
            self.b * r.c + self.d * r.d,
            self.a * r.e + self.c * r.f + self.e,
            self.b * r.e + self.d * r.f + self.f,
        )

    def apply(self, x, y):
        return (self.a * x + self.c * y + self.e, self.b * x + self.d * y + self.f)


def translation(tx, ty):
    return Affine(e=tx, f=ty)


def rotation(deg):
    t = math.radians(deg)
    return Affine(a=math.cos(t), b=math.sin(t), c=-math.sin(t), d=math.cos(t))


def _frame_cost(fr):
    """参数层的"离中性位"距离；content=None 视为完全中性。"""
    c = fr.get('content')
    if not isinstance(c, dict):
        return 0.0
    coord = c.get('coord') or [0, 0, 0]
    angle = c.get('angle') or 0
    return abs(float(coord[0])) + abs(float(coord[1])) + abs(angle)


def default_frame(layer):
    """默认帧。

    参数层（≥3 个内容帧、键只含 coord/angle/mask/哑图标、帧间无内容切换）：
    取最接近中性位（全零）的内容帧 —— move_UD 等参数范围 [-100,+100] 的层，
    t=0 是参数边界而非中性位；t=201 的 content=None 是清理帧，绝不作默认。
    布局层（≤2 个内容帧，t0=放置位 + t201=清理）：取最小时间帧。
    动画层（帧间切换 tex 图标/透明度）：取最小时间帧。
    mask 键是模板遮罩引用，与参数插值无关，不能因它把参数层误判为动画层
    （躯干回転中心 angle=-6 的歪头即源于此误判）。
    """
    fl = layer.get('frameList') or []
    if not fl:
        return None
    content_frames = [fr['content'] for fr in fl if isinstance(fr.get('content'), dict)]
    if len(content_frames) >= 3:
        def is_param_frame(c):
            keys = set(c.keys())
            if keys <= {'coord', 'angle', 'mask'}:
                return True
            # 哑图标（非 tex# 贴图，如 '829:616:414:308' 色块轨道）且帧间恒定
            if keys <= {'coord', 'angle', 'mask', 'icon'}:
                icon = c.get('icon')
                if icon is not None and str(c.get('src', '')).startswith('tex#'):
                    return False
                return True
            return False
        icons = {str(c.get('icon')) for c in content_frames}
        if all(is_param_frame(c) for c in content_frames) and len(icons) <= 1:
            def _content_cost(c):
                coord = c.get('coord') or [0, 0, 0]
                return (abs(float(coord[0])) + abs(float(coord[1]))
                        + abs(float(c.get('angle') or 0)))
            best = min(content_frames, key=_content_cost)
            return {'content': best}   # 伪帧：保持"返回帧对象"的调用契约
    return min(fl, key=lambda fr: (fr.get('time') if fr.get('time') is not None else 0))


def frame_content(layer):
    fr = default_frame(layer)
    if fr is None:
        return {}
    c = fr.get('content')
    return c if isinstance(c, dict) else {}


class Assembler:
    def __init__(self, base=None, emit_frames=False):
        # emit_frames=True：对"帧间切换贴图"的层（差分），按**每一帧**各发射一个
        # 实例（该帧自带 coord/angle 与自己的 ic/mesh，不能用基础件的框去套）。
        # 默认 False ＝ 只取 default_frame，以免影响既有工具。
        base = base or BASE
        if not base:
            raise SystemExit('未指定条目名：请传入 Assembler(base=...) 或设置环境变量 EMOTE_ENTRY')
        self.base = base
        self.emit_frames = emit_frames
        self.d, self.resx = load_model(base)
        self.deref = _deref_factory(self.resx)
        self.obj = self.d['object']
        self.icons = {}       # icon_id -> (texname, icon dict)
        for tex, t in self.d['source'].items():
            if isinstance(t, dict) and 'icon' in t:
                for iid, ic in t['icon'].items():
                    self.icons[iid] = (tex, ic)
        self.registry = {}    # param name -> layer tree root (or None for empty)
        # 跨游戏静默失败防护（可移植性审查教训：R1 产出废品而全程零告警）
        self.multi_root = []  # [(参数名, 根数)] layer 树多根的参数
        self.src_miss = set()  # src 不是 tex# 前缀而被跳过的图标
        for gname, g in self.obj.items():
            if isinstance(g, dict) and 'motion' in g:
                for pn, p in g['motion'].items():
                    if isinstance(p, dict):
                        lay = p.get('layer')
                        if isinstance(lay, list) and lay:
                            if len(lay) > 1:
                                # 老世代条目常见多根（如頭部変形基礎拆
                                # 頭部セット/表情セット两根）；registry 只取
                                # 第一根，其余需在条目合并时拼接
                                self.multi_root.append((pn, len(lay)))
                            self.registry.setdefault(pn, lay[0])
                        else:
                            self.registry.setdefault(pn, None)
        self.instances = []   # dict(icon, tex, ic, world, opa, order)
        self._order = 0
        # 头-身接口校准：默认帧选择修复（default_frame）后原生头位置已正确，
        # 校准整体置空。若换游戏出现头-颈错位，可按
        # metadata.charaProfile.pixelMarker 标定反推补偿量填回这里。
        self.socket_correction = {}
        # 脖子分支随头走：同一机制，置空。
        self.branch_correction = {}
        # 口型变体互斥：运行时靠组切换，静态只保留基础口（0000），其余隐藏。
        # 【本作校准值】图标 ID 清单按具体游戏实测。
        self.hidden_icons = {'0001', '0003', '0004', '0005', '0006', '0007', '0008', '0009'}
        # 0086 = 轮廓蒙版形状载体，游戏里只作蒙版形状不直接渲染；
        # 误渲染时会在双眼周围出现彩色月牙。
        self.hidden_icons.add('0086')
        # 诊断/调优开关：HIDE_PIECES=0061,0062 → 强制隐藏指定件
        _hp = os.environ.get('HIDE_PIECES')
        if _hp:
            self.hidden_icons.update(x.strip() for x in _hp.split(',') if x.strip())

    def assemble(self, root_param='全体構造'):
        """root_param：层树根参数名。'全体構造'（整体构造）是本作实测的根；
        换游戏时在解析出的 layer_tree.txt 里找对应的根组参数名传入。"""
        root = self.registry.get(root_param)
        if root is None:
            raise SystemExit(f'root param {root_param} not found')
        self._walk(root, Affine(), (root_param,), 0, '')
        for inst in self.instances:
            for key, (dx, dy) in self.branch_correction.items():
                if key in inst['path']:
                    inst['world'] = inst['world'].compose(translation(dx, dy))
        if self.multi_root:
            names = ', '.join('%s×%d根' % (pn, n) for pn, n in self.multi_root[:5])
            print('[warn] %d 个参数的 layer 树有多根，registry 只用第一根：%s'
                  '——老世代条目需先经条目合并拼接（见 docs/emote-to-cubism-method.md）'
                  % (len(self.multi_root), names))
        if self.src_miss:
            print('[warn] %d 个图标的 src 不是 tex# 前缀、已被跳过（如 %s）——'
                  '老世代条目 src 为裸纹理名，需先经条目合并适配改写，'
                  '否则产出为空/残缺'
                  % (len(self.src_miss), ', '.join(sorted(self.src_miss)[:5])))
        return self.instances

    def _walk(self, layer, world, stack, depth, path=''):
        if not isinstance(layer, dict) or depth > 64:
            return
        label = str(layer.get('label'))
        path2 = path + '/' + label if path else label

        # 默认帧（子节点的世界变换一律以默认帧为准，保持旧行为）
        dcontent = frame_content(layer)
        dcoord = dcontent.get('coord') or [0, 0, 0]
        dangle = dcontent.get('angle') or 0
        w2 = world.compose(translation(float(dcoord[0]), float(dcoord[1]))
                           .compose(rotation(float(dangle))))

        # 本层要发射的内容帧：默认只有 1 帧；emit_frames 时，若该层是
        # "帧间切换贴图"的差分（≥2 帧且图标不同），则每帧各发射一个实例。
        contents = [dcontent]
        if self.emit_frames:
            tf = []
            for fr in (layer.get('frameList') or []):
                c = fr.get('content')
                if isinstance(c, dict) and str(c.get('src', '')).startswith('tex#'):
                    tf.append(c)
            if len(tf) >= 2 and len({str(c.get('icon')) for c in tf}) >= 2:
                contents = tf
        multi = len(contents) > 1
        group = str(contents[0].get('icon')) if multi else None
        order0 = self._order

        for st, content in enumerate(contents):
            coord = content.get('coord') or [0, 0, 0]
            angle = content.get('angle') or 0
            wf = world.compose(translation(float(coord[0]), float(coord[1]))
                               .compose(rotation(float(angle))))
            icon = content.get('icon')
            ltype = layer.get('type')
            if ltype == 3 and isinstance(icon, str) and icon in self.registry:
                # 组挂载点：嫁接注册表中的组树（防环）
                if icon not in stack:
                    dx, dy = self.socket_correction.get(icon, (0.0, 0.0))
                    w3 = wf.compose(translation(dx, dy)) if (dx or dy) else wf
                    grafted = self.registry[icon]
                    if grafted is not None:
                        self._walk(grafted, w3, stack + (icon,), depth + 1,
                                   path2 + '>' + icon)
            elif isinstance(icon, str) and ICON_RE.match(icon):
                if not str(content.get('src', '')).startswith('tex#'):
                    # src 为裸纹理名等非 tex# 形态（老世代条目），静默跳过
                    # 曾致整模型 0 件产出——计数并在 assemble 末尾告警
                    self.src_miss.add(icon)
                    continue
                tex, ic = self.icons[icon]
                opa = content.get('opa', 1)
                if icon in self.hidden_icons:
                    opa = 0.0
                rec = {
                    'icon': icon, 'tex': tex, 'ic': ic, 'world': wf,
                    'opa': float(opa) if opa is not None else 1.0,
                    'order': order0, 'path': path2,
                }
                if multi:
                    rec['state'] = st
                    rec['group'] = group
                self.instances.append(rec)
        if not multi:
            self._order = order0 + 1
        else:
            self._order = order0 + 1   # 差分同一层共用一个 order（互斥，不占额外绘制序）
        for ch in (layer.get('children') or []):
            self._walk(ch, w2, stack, depth + 1, path2)

    def atlas_size(self, tex_name):
        """图集像素尺寸（延迟读取 PNG 头）。缺失即报错——静默回退曾致
        多图集模型把 4096x2504 的图集按首图集尺寸算 v-UV，错位 1.527 倍。"""
        if not hasattr(self, '_atlas_sizes'):
            from PIL import Image
            self._atlas_sizes = {}
            for tex in self.d['source']:
                p = os.path.join(SRC, self.base + '.psb.m', tex + '-texture.png')
                if os.path.exists(p):
                    with Image.open(p) as im:
                        self._atlas_sizes[tex] = im.size
            for tex in self.d['source']:
                if tex not in self._atlas_sizes:
                    raise FileNotFoundError(f'atlas missing: {tex}')
        return self._atlas_sizes[tex_name]

    def _tristrip_to_triangles(self, m):
        """Emote tristrip（int 序列）→ 三角形索引列表（含退化桥跳过）。

        resx 把 tristrip 的 int32 位型按 f32 解码成非规格化小数（如 7.29e-44
        = 位型整数 52），直接 toint() 会全变 0 → 条带全退化 → 静默回退凸包扇，
        凹形网格（睫毛弧线/嘴线/阴影）整片采错纹理（实证见 docs/moc3-format-semantics.md）。
        此处对非零微小值做位型还原。"""
        ts = []
        n = None
        for x in (self.deref(m.get('tristrip')) or []):
            x = float(x)
            if x != 0.0 and abs(x) < 1e-38:
                x = float(struct.unpack('<I', struct.pack('<f', x))[0])
            ts.append(int(x))
        indices = []
        for i in range(2, len(ts)):
            i0, i1, i2 = ts[i - 2], ts[i - 1], ts[i]
            if i0 == i1 or i1 == i2 or i0 == i2:
                continue
            if n is None:
                n = max(ts)
            if i0 < 0 or i1 < 0 or i2 < 0:
                continue
            indices += [i0, i1, i2]
        return indices

    def _triangulate(self, m, n, px, py):
        """由凸包/凹包重建三角形，统一逆时针环绕（y 向下坐标系下 signed area > 0）。

        Emote 的 tristrip 子条带环绕方向不一致，直接转三角形会被背面剔除；
        包络多边形逐扇三角化并按有向面积统一方向，规避该问题。
        """
        tris = []
        for key in ('convexHulls', 'concaveHulls'):
            hulls = self.deref(m.get(key)) or []
            for h in hulls:
                if isinstance(h, str):
                    h = self.deref(h)
                idxs = [toint(x) for x in h]
                if len(idxs) < 3:
                    continue
                if any(i < 0 or i >= n for i in idxs):
                    continue
                sa = 0.0
                for k in range(len(idxs)):
                    j = (k + 1) % len(idxs)
                    sa += px[idxs[k]] * py[idxs[j]] - px[idxs[j]] * py[idxs[k]]
                if sa < 0:
                    idxs = idxs[::-1]
                for k in range(1, len(idxs) - 1):
                    tris += [idxs[0], idxs[k], idxs[k + 1]]
        return tris

    def mesh_geometry(self, inst):
        """mesh_data 的求值核心：返回中间几何，供 mesh_data 与 footprint 共用。

        返回 dict(positions, uvs, indices, in_rect, rx, ry)：
        in_rect[n] = 该顶点采样落在图标矩形内（矩形外=源图透明，其三角形被丢弃）；
        rx/ry[n] = clamp 到矩形内的矩形内采样坐标（图集 px）。
        """
        ic = inst['ic']
        m = ic.get('mesh') or {}
        verts = self.deref(m.get('vertices'))
        world = inst['world']
        aw, ah = self.atlas_size(inst['tex'])
        ox, oy = float(ic['originX']), float(ic['originY'])
        left, top = float(ic['left']), float(ic['top'])
        if verts:
            n = len(verts) // 2
            mm = self.deref(m.get('meshMatrix'))
            if mm is not None and len(mm) >= 6:
                ma, mb, mc, md = (float(mm[0]), float(mm[1]),
                                  float(mm[2]), float(mm[3]))
                me, mf = float(mm[4]), float(mm[5])
            else:               # 无 meshMatrix → 退化为矩形推导（特例形态）
                ma, md = float(ic['width']), float(ic['height'])
                mb = mc = 0.0
                me, mf = -ma / 2.0, -md / 2.0
            w = float(ic['width'])
            h = float(ic['height'])
            px = [0.0] * n
            py = [0.0] * n
            in_rect = []
            positions = []
            uvs = []
            rxl = [0.0] * n
            ryl = [0.0] * n
            for i in range(n):
                u, v = verts[2 * i], verts[2 * i + 1]
                lx = ma * u + mb * v + me           # 锚点相对
                ly = mc * u + md * v + mf
                wx, wy = world.apply(lx, ly)
                rx = lx + ox                        # 矩形内坐标（UV 用）
                ry = ly + oy
                # 矩形外的网格部分在源层图像里即透明：钳制进矩形——
                # 零沟槽图集上外扩会咬到相邻件
                px[i], py[i] = wx, wy
                positions += [wx, wy]
                in_rect.append(0.0 <= rx <= w and 0.0 <= ry <= h)
                rxl[i] = min(max(rx, 0.0), w)
                ryl[i] = min(max(ry, 0.0), h)
                uvs += [(left + rxl[i]) / aw, (top + ryl[i]) / ah]
            indices = self._tristrip_to_triangles(m)
            if not indices:      # 无有效三角 → 凸包扇
                indices = self._triangulate(m, n, px, py)
            # UV 退化检测：全部顶点被钳到同 1px 行/列（源 meshMatrix 与裁剪矩形
            # 不一致的高光类图标）→ 按未钳坐标范围线性重映射进矩形，
            # 否则整片被拉成 1px 深色条。
            us_ = uvs[0::2]
            vs_ = uvs[1::2]
            span_u = (max(us_) - min(us_)) * aw
            span_v = (max(vs_) - min(vs_)) * ah
            # 仅限已实证的退化件；泛化会误伤身体件
            voff = DEGENERATE_UV_V_OFFSET.get(inst['icon'], 0.0)
            top_ = top + voff
            if inst['icon'] in DEGENERATE_UV_ICONS and (span_u < 2.0 or span_v < 2.0):
                rxu = [ma * verts[2 * k] + mb * verts[2 * k + 1] + me
                       for k in range(n)]
                ryu = [mc * verts[2 * k] + md * verts[2 * k + 1] + mf
                       for k in range(n)]
                su0, su1 = min(rxu), max(rxu)
                sv0, sv1 = min(ryu), max(ryu)
                if su1 - su0 < 1e-6:
                    su0, su1 = 0.0, float(w)
                if sv1 - sv0 < 1e-6:
                    sv0, sv1 = 0.0, float(h)
                uvs = []
                for k in range(n):
                    fu = (rxu[k] - su0) / (su1 - su0)
                    fv = (ryu[k] - sv0) / (sv1 - sv0)
                    vr = DEGENERATE_UV_V_RANGE.get(inst['icon'],
                                                   (1, h - 1))
                    uvs += [(left + 1.0 + fu * (w - 2.0)) / aw,
                            (top_ + vr[0] + fv * (vr[1] - vr[0])) / ah]
            return {'positions': positions, 'uvs': uvs, 'indices': indices,
                    'in_rect': in_rect, 'rx': rxl, 'ry': ryl}
        # 无网格图标 → 图标矩形四边形
        corners = [(0, 0), (1, 0), (0, 1), (1, 1)]
        positions = []
        uvs = []
        w = float(ic['width'])
        h = float(ic['height'])
        uw = w / aw
        vh = h / ah
        for cx, cy in corners:
            wx, wy = world.apply(cx * w - ox, cy * h - oy)
            positions += [wx, wy]
            uvs += [left / aw + cx * uw, top / ah + cy * vh]
        return {'positions': positions, 'uvs': uvs,
                'indices': [0, 1, 2, 2, 1, 3],
                'in_rect': [True] * 4,
                'rx': [0.0, w, 0.0, w], 'ry': [0.0, 0.0, h, h]}

    def mesh_data(self, inst):
        """返回 (positions, uvs, indices)：锚点相对像素空间的世界坐标三角形。

        权威约定（实证：多数图标 meshMatrix≠矩形推导，旧"verts*矩形WH−origin"
        约定只对少数特例成立）：
          mesh.vertices 归一化于"原始图层图像 A×D"（meshMatrix 的 A/D，
          E,F=−A/2,−D/2 即锚点居中）；pos_local = meshMatrix·(u,v)；
          矩形内像素 = pos_local + (originX, originY)；
          UV = (left + 矩形内.x)/atlasW, (top + 矩形内.y)/atlasH。
        网格外圈顶点的 UV 会落在矩形外 0~5px 的透明沟槽（E-mote 网格
        比内容裁剪大约一个 thickness），属正常，不 clamp。
        minAreaRect 只是编辑器元数据，不参与 UV/位置。"""
        g = self.mesh_geometry(inst)
        return g['positions'], g['uvs'], g['indices']

    def footprint(self, inst):
        """UV 足迹静态放置（PSD 打包器对位修复，见 docs/emote-to-cubism-method.md）。

        网格只在 UV 足迹（可见三角形实际采样的矩形子区域）内采样图集；
        旧打包器把**整矩形**裁剪拉伸到**全网格**世界包围盒——当网格
        （口/目影/add_mask 等参数形变件）只覆盖矩形一部分时，
        足迹外的美术被卷进图层、纵横比失配可达数倍。

        裙边（skirt）补充：网格外圈顶点采样在矩形外（透明沟槽），其 UV
        渲染时**钳制到矩形边缘**——E-mote 的 V 形下巴尖等轮廓正是靠这些
        钳制裙边三角形画出来的。footprint 裁剪会把它们切平，因此返回值
        含 `skirt`：带矩形外顶点的三角形（世界坐标 + 钳制 UV），由打包器
        按原作渲染语义补绘。

        返回 dict：
          crop  (x0,y0,x1,y1)  图集 px 的足迹裁剪（1:1 像素，零形变）
          bbox  (minx,miny,maxx,maxy)  足迹顶点（in_rect）的世界包围盒
          legacy_bbox  全顶点世界包围盒（含裙边；有 skirt 时作放置框）
          skirt  [(v0,v1,v2),...] 每顶点 (wx,wy,u,v)；无裙边 = []
        无网格件：crop=整矩形、bbox=legacy_bbox、skirt=[]。
        """
        ic = inst['ic']
        w = float(ic['width'])
        h = float(ic['height'])
        left = int(round(float(ic['left'])))
        top = int(round(float(ic['top'])))
        g = self.mesh_geometry(inst)
        pos = g['positions']
        legacy = (min(pos[0::2]), min(pos[1::2]), max(pos[0::2]), max(pos[1::2]))
        # 退化 UV 件：UV 不代表真实采样区，维持旧行为（整矩形→全网格 bbox）
        if inst['icon'] in DEGENERATE_UV_ICONS:
            return {'crop': (left, top, left + int(round(w)), top + int(round(h))),
                    'bbox': legacy, 'legacy_bbox': legacy, 'skirt': []}
        idxs = g['indices']
        used = set(idxs)
        vis = [i for i in used if g['in_rect'][i]]
        if not vis:                       # 全部采样在矩形外（异常）→ 旧行为
            return {'crop': (left, top, left + int(round(w)), top + int(round(h))),
                    'bbox': legacy, 'legacy_bbox': legacy, 'skirt': []}
        # 裙边三角形：三个顶点中任一采样出矩形的（UV 渲染时钳制）
        skirt = []
        for k in range(0, len(idxs), 3):
            tri = (idxs[k], idxs[k + 1], idxs[k + 2])
            if any(not g['in_rect'][i] for i in tri):
                skirt.append([(pos[2 * i], pos[2 * i + 1],
                               g['uvs'][2 * i], g['uvs'][2 * i + 1]) for i in tri])
        x0 = min(g['rx'][i] for i in vis)
        x1 = max(g['rx'][i] for i in vis)
        y0 = min(g['ry'][i] for i in vis)
        y1 = max(g['ry'][i] for i in vis)
        xs = [pos[2 * i] for i in vis]
        ys = [pos[2 * i + 1] for i in vis]
        bbox = (min(xs), min(ys), max(xs), max(ys))
        return {'crop': (left + x0, top + y0, left + x1, top + y1),
                'bbox': bbox, 'legacy_bbox': legacy, 'skirt': skirt}


def world_bbox(assembler, instances):
    xs = []
    ys = []
    for inst in instances:
        p, _, _ = assembler.mesh_data(inst)
        xs += p[0::2]
        ys += p[1::2]
    return min(xs), min(ys), max(xs), max(ys)


if __name__ == '__main__':
    a = Assembler()
    inst = a.assemble()
    print('instances:', len(inst))
    print('opa values:', sorted({i['opa'] for i in inst}))
    bb = world_bbox(a, inst)
    print('world bbox: x[%.0f, %.0f] y[%.0f, %.0f]' % bb)
    prof = a.d['metadata'].get('charaProfile', {}).get('pixelMarker', {})
    print('charaProfile bounds: x[%.0f, %.0f] y[%.0f, %.0f]' % (
        prof.get('boundsLeft', 0), prof.get('boundsRight', 0),
        prof.get('boundsTop', 0), prof.get('boundsBottom', 0)))
    from collections import Counter
    print('tex usage:', Counter(i['tex'] for i in inst))
    print('unique icons used:', len({i['icon'] for i in inst}))
