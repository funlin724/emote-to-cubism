# -*- coding: utf-8 -*-
"""参数绑定模型生成器（数据驱动版）。

【核心思路】链变换不再手调仿射，而是**重放 Emote 参数链帧**：
每个 moc3 keyform = "把某参数链上所有 parameterize 层的默认帧换成
参数值 v 对应时间轴位置的帧后重新装配" 得到的网格顶点。装配规则与
静姿生成器（build_moc3.py）完全一致，因此静态姿势（全部参数取默认值）
与静姿版逐点一致（内置自检断言）。

【实测依据（全条目扫描，见 docs/emote-to-cubism-method.md）】
Emote 模型里带几何关键帧的参数通常只有 body_slant / head_slant /
move_LR / move_UD（后两个常被 clampControl 停用）。head_LR/head_UD/
face_eye_LR/UD 等链是零变换——数据里不存在转头/眼球几何。
故本生成器的绑定集：
  ParamBodyAngleZ ← body_slant（躯干回転中心，angle ∓6°）
  ParamAngleZ     ← head_slant（头部傾き，angle 0/10/18°，
                     静息位在 hs=-100；键 [0,30] → hs [-100,+100]）
  ParamEyeLOpen/ROpen ← 眨眼图标不透明度 + 高光淡出（眼闭合 31% 内高光
                     消失 → 键 [0,0.5,1] 映射 [0,0,1]）
  ParamMouthOpenY ← 基础口型图标切换 0000↔0002（2 键）
  ParamAngleX/Y   ← 无源数据，声明但不绑定（motion 兼容）

【多 binding 张量积】mesh.keyform_counts = 各 binding 键数之积。槽位序实测
（pixi 顶点探针）：flat = i0 + n0*(i1 + n1*(...))，即 **bindings[0]
最快、最后一个 binding 最慢**（列主序）。

【适配层】SILHOUETTE_EXCLUDE / SOCKET_MAP / EXTRAS / OPA_SEQS 的图标 ID
均为本作实测校准值（图标 ID 不跨游戏稳定），换游戏需重新标定——见
docs/emote-to-cubism-method.md "通用规则 vs 本作特例"。

环境变量：
  EMOTE_ENTRY   条目名（缺省 'model'，仅用作输出文件名前缀）
  EMOTE_MOTION_DIR  解包数据目录（同 emote_assemble.py）
  EMOTE_OUT     输出目录（缺省仓库内 output_m3/）
  HIDE_PIECES   强制隐藏的图标清单（逗号分隔，诊断用）
  NO_EYEBALL=1  回退 3 层绑定深度（5 层在部分运行时触发疑似未定义行为）
  NO_MASK=1     完全去掉运行时遮罩（诊断开关）
  TOP_STACK=1   睫毛线组置顶（默认关闭：置顶后线组贴图的大面积底色外露）

用法: python tools/build_moc3_params.py
"""
import json, os, sys, shutil
from collections import Counter

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, 'tools'))

from emote_assemble import (Assembler, Affine, translation, rotation,
                            frame_content, ICON_RE, PIECE_NUDGES)
from moc3lib import Moc3, MocVersion, CountIdx

ENTRY = os.environ.get('EMOTE_ENTRY', 'model')
OUT = os.environ.get('EMOTE_OUT') or os.path.join(ROOT, 'output_m3')
KF_ALIGN = 16  # floats（=64 字节）

# ---- 参数表：(id, max, min, default, 绑定 spec, cdi 名) --------------------
# 绑定 spec:
#   ('slant', emote_pid, keys, replay_values)   位置绑定（重放链）
#   ('opa', keys, None)                         不透明度绑定（序列见 OPA_SEQS）
#   None                                        无绑定
_K8 = [0.0, 0.155, 0.175, 0.485, 0.505, 0.815, 0.835, 1.0]
_C8 = [1, 1, 0, 0, 0, 0, 0, 0]     # 闭
_T8 = [0, 0, 1, 1, 0, 0, 0, 0]     # 3/4 闭
_H8 = [0, 0, 0, 0, 1, 1, 0, 0]     # 半闭
_O8 = [0, 0, 0, 0, 0, 0, 1, 1]     # 睁
_I8 = [0, 0, 0, 0, 1, 1, 1, 1]     # 虹膜/高光：3/4 闭起消失（源 opa=0）
PARAMS_M3 = [
    ('ParamAngleX',     30.0, -30.0, 0.0, None, '头部左右'),
    ('ParamAngleY',     30.0, -30.0, 0.0, None, '头部上下'),
    ('ParamAngleZ',     30.0, -30.0, 0.0,
     # 3 键：Cubism 核心对键外值做线性外插（实测 -30 权重 -1 会镜像出反倾），
     # 负向是数据死区（源只有正向倾斜），两端键都放静息姿势。
     ('slant', 'head_slant', [-30.0, 0.0, 30.0], [-100.0, -100.0, 100.0]), '头部倾斜'),
    ('ParamBodyAngleZ', 10.0, -10.0, 0.0,
     ('slant', 'body_slant', [-10.0, 0.0, 10.0], [-100.0, 0.0, 100.0]), '身体倾斜'),
    ('ParamEyeLOpen',    1.0,   0.0, 1.0,
     ('opa', _K8, None), '左眼开闭'),
    ('ParamEyeROpen',    1.0,   0.0, 1.0,
     ('opa', _K8, None), '右眼开闭'),
    ('ParamMouthOpenY',  1.0,   0.0, 0.0,
     ('opa', [0.0, 1.0], None), '嘴开闭'),
    # 转眼球：位置 keyform 由 emit_slots 直接加平移；
    # spec 登记 keys 供 bindings/band 表接线（位移在 emit_slots 内实现）
    ('ParamEyeBallX',    1.0,  -1.0, 0.0,
     ('slant', 'eb_x', [-1.0, 0.0, 1.0], [-1.0, 0.0, 1.0]), '眼珠左右'),
    ('ParamEyeBallY',    1.0,  -1.0, 0.0,
     ('slant', 'eb_y', [-1.0, 0.0, 1.0], [-1.0, 0.0, 1.0]), '眼珠上下'),
]
PARAM_IDX = {p[0]: j for j, p in enumerate(PARAMS_M3)}

# 不透明度序列（键序同各参数 keys）。眨眼按目L/目R 树的帧序列升为 4 态
# （睁→半→3/4→闭；眼白、眼影各态同理）。高光按源数据实测：
# 眼闭合 31% 即消失（序列 [0,0,0,1]... 此处用 8 键版 _I8）。
OPA_SEQS = {
    'ParamEyeLOpen': {'0031': _O8, '0032': _H8, '0033': _T8, '0034': _C8,
                      '0043': _O8, '0044': _H8,
                      '0047': _O8, '0048': _H8, '0049': _T8, '0050': _C8,
                      '0041': _I8, '0039': _I8,
                      # 瞳孔附加件：源数据无眨眼键（靠 stencil 裁剪），运行时
                      # 遮罩不可依赖（实测），按眼白同序列兜底。
                      '0087': _O8},
    'ParamEyeROpen': {'0035': _O8, '0036': _H8, '0037': _T8, '0038': _C8,
                      '0045': _O8, '0046': _H8,
                      '0051': _O8, '0052': _H8, '0053': _T8, '0054': _C8,
                      '0042': _I8, '0040': _I8,
                      '0088': _O8},
    'ParamMouthOpenY': {'0000': [1.0, 0.0], '0002': [0.0, 1.0]},
}
# 需要追加装配的图标：(宿主 icon, extra icon)。【本作校准值】
EXTRAS = [
    ('0031', '0032'), ('0031', '0033'), ('0031', '0034'),
    ('0035', '0036'), ('0035', '0037'), ('0035', '0038'),
    ('0043', '0044'), ('0045', '0046'),
    ('0047', '0048'), ('0047', '0049'), ('0047', '0050'),
    ('0051', '0052'), ('0051', '0053'), ('0051', '0054'),
    ('0000', '0002'),
]
# 全身柔光剪影追加件（时间线主树/stencil 链下的低透明瓦片）：
# 部分为游戏柔光效果（保留），部分为接缝/噪点瓦片（排除）。
# 【本作校准值】清单按具体游戏实测。
SILHOUETTE_EXCLUDE = {'0137', '0138', '0139', '0140', '0141', '0142'}
# 遮罩配置：眼内容件 → 同侧白目载体（与官方样例模型同构）。
# 白目自身穹顶溢出问题由纹理级修剪解决（可选，不依赖运行时掩码）。
MASK_CARRIER_ICONS = {'0086'}
SOCKET_MAP = {'0043': '0043', '0044': '0043', '0041': '0043', '0039': '0043', '0087': '0043',
              '0045': '0045', '0046': '0045', '0042': '0045', '0040': '0045', '0088': '0045'}
# 默认参数值落在的键序号（静态姿势 = 该槽位，必须与基础装配一致）
DEFAULT_KEY_IDX = {'ParamAngleX': 1, 'ParamAngleY': 1,
                   'ParamAngleZ': 1, 'ParamBodyAngleZ': 1,
                   'ParamEyeLOpen': 7, 'ParamEyeROpen': 7, 'ParamMouthOpenY': 0,
                   'ParamEyeBallX': 1, 'ParamEyeBallY': 1}
EPS = 1e-4


def align_kf(x):
    return (x + KF_ALIGN - 1) // KF_ALIGN * KF_ALIGN


class ReplayWalker(Assembler):
    """支持"参数值覆写"的装配器：指定参数 id → 值，链上所有绑定该参数的层
    用该值对应时间轴位置的帧内容替代默认帧，其余装配与静姿版完全一致。"""

    def __init__(self):
        super().__init__()
        # 嫁接名 → 定义该组树的 (gname, pn)（与 registry 同序 setdefault）
        self.ctx = {}
        for gname, g in self.obj.items():
            if isinstance(g, dict) and 'motion' in g:
                for pn, p in g['motion'].items():
                    if isinstance(p, dict):
                        self.ctx.setdefault(pn, (gname, pn))
        self.overrides = {}

    def _content_at(self, layer, gname, pn):
        pz = layer.get('parameterize')
        if pz is None or not self.overrides:
            return frame_content(layer)
        p = self.obj[gname]['motion'][pn]
        params = p.get('parameter') or []
        if pz >= len(params):
            return frame_content(layer)
        pr = params[pz]
        pid = pr.get('id')
        if pid not in self.overrides:
            return frame_content(layer)
        v = float(self.overrides[pid])
        rb, re_, dv = pr['rangeBegin'], pr['rangeEnd'], float(pr['division'])
        t = (v - rb) / (re_ - rb) * dv
        fl = [fr for fr in (layer.get('frameList') or [])
              if isinstance(fr.get('content'), dict)]
        if not fl:
            return {}
        fl.sort(key=lambda fr: fr.get('time') or 0)
        for fr in fl:                       # 精确命中
            if abs((fr.get('time') or 0) - t) < EPS:
                return fr['content']
        prev = nxt = None
        for fr in fl:
            tt = fr.get('time') or 0
            if tt < t:
                prev = fr
            elif tt > t and nxt is None:
                nxt = fr
        if prev is None:
            return fl[0]['content']
        if nxt is None:
            return prev['content']
        t0, t1 = prev.get('time') or 0, nxt.get('time') or 0
        w = (t - t0) / (t1 - t0) if t1 > t0 else 0.0
        c0, c1 = prev['content'], nxt['content']
        out = dict(c0)
        a = c0.get('coord') or [0, 0, 0]
        b = c1.get('coord') or a
        out['coord'] = [a[0] + (b[0] - a[0]) * w, a[1] + (b[1] - a[1]) * w, a[2]]
        a = c0.get('angle') or 0.0
        b = c1.get('angle') or 0.0
        out['angle'] = a + (b - a) * w
        return out

    def run(self, overrides=None, root_param='タイムライン構造'):
        # 根 = 时间线主树：全体構造 嫁接 + stencil/追加パーツ 全身柔光剪影。
        # 此前用 全体構造 根会漏装柔光瓦片——"人物整体少了一圈边"的实源。
        self.overrides = overrides or {}
        self.instances = []
        self._order = 0
        gname, pn = self.ctx.get(root_param, ('all_parts', root_param))
        self._walk2(self.registry[root_param], gname, pn,
                    Affine(), (root_param,), 0, '')
        return self.instances

    def _walk2(self, layer, gname, pn, world, stack, depth, path=''):
        if not isinstance(layer, dict) or depth > 64:
            return
        content = self._content_at(layer, gname, pn)
        coord = content.get('coord') or [0, 0, 0]
        angle = content.get('angle') or 0
        local = translation(float(coord[0]), float(coord[1])).compose(
            rotation(float(angle)))
        w2 = world.compose(local)
        label = str(layer.get('label'))
        path2 = path + '/' + label if path else label
        icon = content.get('icon')
        ltype = layer.get('type')
        if ltype == 3 and isinstance(icon, str) and icon in self.registry:
            if icon not in stack:
                gg = self.ctx.get(icon)
                grafted = self.registry[icon]
                if grafted is not None and gg is not None:
                    self._walk2(grafted, gg[0], gg[1], w2,
                                stack + (icon,), depth + 1, path2 + '>' + icon)
        elif isinstance(icon, str) and ICON_RE.match(icon) \
                and str(content.get('src', '')).startswith('tex#'):
            tex, ic = self.icons[icon]
            opa = content.get('opa', 1)
            if icon in self.hidden_icons:
                opa = 0.0
            ng = PIECE_NUDGES.get(icon)
            if ng:
                sc = ng[3] if len(ng) > 3 else 1.0
                w3 = w2.compose(translation(ng[0], ng[1])).compose(
                    rotation(ng[2] if len(ng) > 2 else 0.0))
                if len(ng) > 5:   # 绕指定轴心（源画布 px）缩放
                    px, py = ng[4], ng[5]
                    w3 = w3.compose(translation(px, py)).compose(
                        Affine(a=sc, d=sc)).compose(translation(-px, -py))
                else:
                    w3 = w3.compose(Affine(a=sc, d=sc))
            else:
                w3 = w2
            self.instances.append({
                'icon': icon, 'tex': tex, 'ic': ic, 'world': w3,
                'opa': float(opa) if opa is not None else 1.0,
                'order': self._order, 'path': path2,
            })
            self._order += 1
        for ch in (layer.get('children') or []):
            self._walk2(ch, gname, pn, w2, stack, depth + 1, path2)


def zsort(inst):
    """渲染顺序 = zorder 全局主序 + 层树遍历序次键。帧缓冲实证定版：
    曾改纯层树序 → 全头叠放反转（后发盖脸）；zorder 主序渲染正确。
    历史教训：渲染顺序的最终裁判是 pixi 帧缓冲实测，不是纸面推理。"""
    return sorted(inst, key=lambda i: ((i['ic'].get('metadata') or {}).get(
        'zorder') or 0, i['order']))


def apply_nudges(insts):
    for i in insts:
        ng = PIECE_NUDGES.get(i['icon'])
        if ng:
            sc = ng[3] if len(ng) > 3 else 1.0
            w = i['world'].compose(translation(ng[0], ng[1])).compose(
                rotation(ng[2] if len(ng) > 2 else 0.0))
            if len(ng) > 5:
                px, py = ng[4], ng[5]
                w = w.compose(translation(px, py)).compose(
                    Affine(a=sc, d=sc)).compose(translation(-px, -py))
            else:
                w = w.compose(Affine(a=sc, d=sc))
            i['world'] = w
    return insts


def build():
    HIDDEN_SET = set(x.strip() for x in os.environ.get('HIDE_PIECES', '').split(',') if x.strip())
    walker = ReplayWalker()

    # ---- 位置绑定 spec 与重放缓存 ----------------------------------------
    body_spec = head_spec = None
    for pid, _mx, _mn, _df, spec, _nm in PARAMS_M3:
        if spec and spec[0] == 'slant':
            if pid == 'ParamBodyAngleZ':
                body_spec = (pid, spec[1], spec[2], spec[3])
            elif pid == 'ParamAngleZ':
                head_spec = (pid, spec[1], spec[2], spec[3])
    bkeys, bvals = body_spec[2], body_spec[3]
    hkeys, hvals = head_spec[2], head_spec[3]

    replay_cache = {}   # overrides tuple -> sorted instances

    def replay(ov):
        key = tuple(sorted(ov.items()))
        if key not in replay_cache:
            replay_cache[key] = zsort(walker.run(dict(ov)))
        return replay_cache[key]

    base_inst = replay({})
    # 自检 1：空覆写必须与静姿装配器逐实例一致（含同款微调）
    ref = zsort(apply_nudges(Assembler().assemble(root_param='タイムライン構造')))
    assert len(ref) == len(base_inst), \
        f'instance count {len(ref)} vs {len(base_inst)}'
    for r, f in zip(base_inst, ref):
        assert r['icon'] == f['icon'] and r['world'].e == f['world'].e \
            and r['world'].f == f['world'].f, 'replay({}) != Assembler()'

    def inst_positions(inst):
        p, _, _ = walker.mesh_data(inst)
        return p

    # ---- 基础网格（像素坐标） --------------------------------------------
    meshes = []
    for k, i in enumerate(base_inst):
        if i['icon'] in SILHOUETTE_EXCLUDE:
            continue
        p, u, ix = walker.mesh_data(i)
        meshes.append({
            'id': f"part{i['icon']}_{k}", 'tex': int(i['tex'].split('#')[1]),
            'positions': p, 'uvs': u, 'indices': ix,
            'opacity': i['opa'], 'icon': i['icon'], 'path': i['path'],
            'draw_order': 500.0 + k, 'base_map': k, 'host': None,
            '_hidden': i['icon'] in HIDDEN_SET,
            'visible': i['icon'] not in HIDDEN_SET,
        })

    # ---- extra 图标：按自身 ic 重推几何（复制宿主 UV 会采到宿主贴图） ------
    inserts = []
    host_extra_count = {}
    for host_icon, xicon in EXTRAS:
        hk = next(k for k, me in enumerate(meshes) if me['icon'] == host_icon)
        host = meshes[hk]
        src = base_inst[host['base_map']]
        off = host_extra_count.get(host_icon, 0)
        host_extra_count[host_icon] = off + 1
        e = dict(src)
        e['icon'] = xicon
        e['tex'], e['ic'] = walker.icons[xicon]
        p, u, ix = walker.mesh_data(e)
        inserts.append((hk + 1, {
            'id': f"part{xicon}_x{hk}", 'tex': int(e['tex'].split('#')[1]),
            'positions': p, 'uvs': u, 'indices': ix,
            'opacity': 0.0, 'icon': xicon, 'path': host['path'],
            'draw_order': host['draw_order'] + 0.25 * (off + 1),
            'base_map': host['base_map'], 'host': xicon,
        }))
    for pos, e in sorted(inserts, key=lambda t: -t[0]):
        meshes.insert(pos, e)

    # ---- 归一化（与静姿版同款） -------------------------------------------
    xs, ys = [], []
    for me in meshes:
        xs += me['positions'][0::2]
        ys += me['positions'][1::2]
    minx, miny = min(xs), min(ys)
    W = (max(xs) - minx) * 1.05
    H = (max(ys) - miny) * 1.05

    def norm(px):
        out = []
        for k in range(0, len(px), 2):
            out += [(px[k] - minx - W / 2) / W, (px[k + 1] - miny - H / 2) / W]
        return out

    for me in meshes:
        me['positions'] = norm(me['positions'])

    # ---- 组合位置（重放） --------------------------------------------------
    combo_cache = {}    # (base_map, bi, hi) -> normalized positions

    def combo_positions(me, bi, hi):
        ck = (me['base_map'], me['host'], bi, hi)   # host/extra 共享 base_map，须区分
        if ck in combo_cache:
            return combo_cache[ck]
        ov = {}
        if bi is not None:
            ov[body_spec[1]] = bvals[bi]
        if hi is not None:
            ov[head_spec[1]] = hvals[hi]
        if not ov:
            out = me['positions']
        elif me['host'] is not None:
            inst = replay(ov)
            hinst = inst[me['base_map']]      # 宿主的实例
            e = dict(hinst)
            e['icon'] = me['host']
            e['tex'], e['ic'] = walker.icons[me['host']]
            out = norm(inst_positions(e))
        else:
            inst = replay(ov)
            out = norm(inst_positions(inst[me['base_map']]))
        combo_cache[ck] = out
        return out

    # ---- 眼件层级定版：仅睫毛弧线组可置顶（TOP_STACK=1），目影组回层树位 ----
    # 目影本画在白目上、应被虹膜盖住中段，置顶后与虹膜暗顶带、弧线三层
    # 暗色叠在瞳孔上部（比游戏闷）。整值间距：运行时 draw order
    # 取整、同值并列按 mesh 槽序（历史教训）。
    if os.environ.get('TOP_STACK', '0') == '1':
        top_ro = max(me['draw_order'] for me in meshes)
        for icons, ro in ((('0031', '0032', '0033', '0034'), top_ro + 1),
                          (('0035', '0036', '0037', '0038'), top_ro + 2)):
            for me in meshes:
                if me['icon'] in icons:
                    me['draw_order'] = float(ro)
    # 白目层级修正：白目必须在脸之上、虹膜之下（脸 564 < 白目 566 < 虹膜 574）。
    for me in meshes:
        if me['icon'] in ('0043', '0045'):
            me['draw_order'] = 566.0

    # ---- 每网格的绑定集合与序列 --------------------------------------------
    # 转眼球：虹膜/瞳孔/高光绑 ParamEyeBallX/Y 位置 keyform。
    # 白目绝不绑（窗口静止是转动机能的前提：白目动=窗口动=虹膜相对窗口不动）。
    # ⚠ 嵌套层数：binds 可到 5 层（Body:Angle:EBX:EBY:EyeLOpen）——官方 Editor
    # 样例最深 3 层，5 层在部分运行时触发疑似未定义行为（NO_EYEBALL=1 可回退）。
    EYEBALL_PIECES = set() if os.environ.get('NO_EYEBALL') == '1' else \
        {'0041', '0042', '0039', '0040', '0087', '0088'}
    EB_RANGE_X = 5.0    # 水平行程（模型 px），⊂修剪后窗口
    EB_RANGE_Y = 3.0    # 垂直行程
    for me in meshes:
        moves_b = any(max(abs(a - b) for a, b in
                          zip(combo_positions(me, bi, None), me['positions'])) > EPS
                      for bi in range(len(bkeys)))
        moves_h = any(max(abs(a - b) for a, b in
                          zip(combo_positions(me, None, hi), me['positions'])) > EPS
                      for hi in range(len(hkeys)))
        binds = []
        if moves_b:
            binds.append(('ParamBodyAngleZ', bkeys))
        if moves_h:
            binds.append(('ParamAngleZ', hkeys))
        if me['icon'] in EYEBALL_PIECES:
            binds.append(('ParamEyeBallX', [-1.0, 0.0, 1.0]))
            binds.append(('ParamEyeBallY', [-1.0, 0.0, 1.0]))
        seq = None
        for pid, seqs in OPA_SEQS.items():
            if me['icon'] in seqs:
                seq = seqs[me['icon']]
                binds.append((pid, PARAMS_M3[PARAM_IDX[pid]][4][1]))
        me['binds'] = binds
        me['opa_seq'] = seq
        if seq is not None:
            pid = binds[-1][0]
            me['opacity'] = seq[DEFAULT_KEY_IDX[pid]]
        me['moves'] = (moves_b, moves_h)

    # ---- band 表（唯一绑定组合） -------------------------------------------
    band_of_sig = {}

    def band_index(binds):
        sig = tuple(b[0] for b in binds)
        if sig not in band_of_sig:
            band_of_sig[sig] = len(band_of_sig) + 1   # band 0 = 空（未绑定）
        return band_of_sig[sig]

    for me in meshes:
        me['band'] = band_index(me['binds']) if me['binds'] else 0
        n = 1
        for _pid, ks in me['binds']:
            n *= len(ks)
        me['n_kf'] = n

    # ---- keyform 槽位（bindings[0] 最快、最后一个 binding 最慢） -----------
    # 运行时槽位序实测（pixi 顶点探针）：flat = i0 + n0*(i1 + n1*(...)).
    kf_begin = []
    kf_acc = 0
    kf_opa = []
    kf_order = []

    def emit_slots(me, prefix, remaining):
        if not remaining:
            bi = hi = oi = None
            ex = ey = 0.0
            for pid, ki in prefix:
                if pid == 'ParamBodyAngleZ':
                    bi = ki
                elif pid == 'ParamAngleZ':
                    hi = ki
                elif pid == 'ParamEyeBallX':
                    ex = ki - 1          # 键 [-1,0,1] 索引 → 值 -1/0/1
                elif pid == 'ParamEyeBallY':
                    ey = ki - 1
                else:
                    oi = ki
            pos = combo_positions(
                me,
                bi if bi is not None and me['moves'][0] else None,
                hi if hi is not None and me['moves'][1] else None)
            if ex or ey:
                # 平移（norm 坐标系：位移 = 像素 / 画布宽 W）
                pos = list(pos)
                dx = (ex * EB_RANGE_X) / W
                dy = (ey * EB_RANGE_Y) / W
                for i in range(0, len(pos), 2):
                    pos[i] += dx
                    pos[i + 1] += dy
            if me.get('_hidden'):
                opa = 0.0   # HIDE_PIECES 强制隐藏（覆盖 opa_seq——眨眼序列曾使隐藏失效）
            else:
                opa = me['opa_seq'][oi] if me['opa_seq'] is not None else me['opacity']
            me['kf_slots'].append((pos, opa))
            return
        pid, ks = remaining[0]
        for ki in range(len(ks)):
            emit_slots(me, prefix + [(pid, ki)], remaining[1:])

    for me in meshes:
        me['kf_slots'] = []
        emit_slots(me, [], list(reversed(me['binds'])))   # 末位 bind 最慢（最外层）
        assert len(me['kf_slots']) == me['n_kf']
        for pos, opa in me['kf_slots']:
            kf_begin.append(kf_acc)
            kf_acc = align_kf(kf_acc + len(pos))
            kf_opa.append(opa)
            kf_order.append(me['draw_order'])

    kps = [0.0] * kf_acc
    slot = 0
    mesh_kf_begin = []
    for me in meshes:
        mesh_kf_begin.append(slot)
        for pos, _opa in me['kf_slots']:
            kps[kf_begin[slot]:kf_begin[slot] + len(pos)] = pos
            slot += 1

    # ---- 自检 2：默认键组合槽位 == 基础姿势 --------------------------------
    for me in meshes:
        flat = 0
        for pid, ks in reversed(me['binds']):   # 首绑定最快（列主序）
            flat = flat * len(ks) + DEFAULT_KEY_IDX[pid]
        pos, _ = me['kf_slots'][flat]
        d = max(abs(a - b) for a, b in zip(pos, me['positions']))
        assert d < 1e-9, f'static pose drift on {me["id"]}: {d}'

    # ---- mask 接线（白目窗口裁切眼内容件） ----------------------------------
    n = len(meshes)
    icon2idx = {}
    for k, me in enumerate(meshes):
        icon2idx.setdefault(me['icon'], k)
    mask_entries = []
    mask_begin = [0] * n
    mask_count = [0] * n
    for k, me in enumerate(meshes):
        if me['icon'] in MASK_CARRIER_ICONS:
            me['visible'] = False
        # NO_MASK=1 诊断开关：完全去掉运行时遮罩
        if os.environ.get('NO_MASK') == '1':
            continue
        s_icon = SOCKET_MAP.get(me['icon'])
        if s_icon and s_icon in icon2idx:
            mask_begin[k] = len(mask_entries)
            mask_entries.append(icon2idx[s_icon])
            mask_count[k] = 1  # 单白目载体窗口，恒定不随眨眼变形

    # ---- UV/索引偏移 ---------------------------------------------------------
    uv_off, idx_off = [], []
    uv_acc = idx_acc = 0
    for me in meshes:
        nv = len(me['positions']) // 2
        ni = len(me['indices'])
        uv_off.append(uv_acc)
        uv_acc += nv * 2
        idx_off.append(idx_acc)
        idx_acc += ni

    # ---- binding/band/keys 接线 ----------------------------------------------
    bindings = []           # (param_idx, keys)
    for pid, _mx, _mn, _df, spec, _nm in PARAMS_M3:
        if spec is None:
            continue
        keys = spec[2] if spec[0] == 'slant' else spec[1]
        bindings.append((PARAM_IDX[pid], keys))
    bidx_of_param = {pi: j for j, (pi, _ks) in enumerate(bindings)}
    param_binding_begin = []
    param_binding_count = []
    for pi in range(len(PARAMS_M3)):
        if pi in bidx_of_param:
            param_binding_begin.append(bidx_of_param[pi])
            param_binding_count.append(1)
        else:
            param_binding_begin.append(-1)
            param_binding_count.append(0)

    keys_values = []
    binding_keys_begin = []
    binding_keys_count = []
    for _pi, ks in bindings:
        binding_keys_begin.append(len(keys_values))
        binding_keys_count.append(len(ks))
        keys_values += list(ks)

    bands_begin = [0]
    bands_count = [0]
    kbi = []
    for sig, _b in sorted(band_of_sig.items(), key=lambda kv: kv[1]):
        bands_begin.append(len(kbi))
        bands_count.append(len(sig))
        for pidname in sig:
            kbi.append(bidx_of_param[PARAM_IDX[pidname]])

    # ---- moc3 组装 -------------------------------------------------------------
    m = Moc3()
    m.header.version = MocVersion.V4_00
    m.header.endian = 0
    m.canvas.pixels_per_unit = W
    m.canvas.origin_x, m.canvas.origin_y = W / 2, H / 2
    m.canvas.canvas_width, m.canvas.canvas_height = W, H
    m.canvas.canvas_flag = 0

    counts = [0] * 23
    counts[CountIdx.PARTS] = 1
    counts[CountIdx.ART_MESHES] = n
    counts[CountIdx.PARAMETERS] = len(PARAMS_M3)
    counts[CountIdx.PART_KEYFORMS] = 1
    counts[CountIdx.ART_MESH_KEYFORMS] = len(kf_begin)
    counts[CountIdx.KEYFORM_POSITIONS] = kf_acc
    counts[CountIdx.KEYFORM_BINDING_INDICES] = len(kbi)
    counts[CountIdx.KEYFORM_BINDING_BANDS] = len(bands_begin)
    counts[CountIdx.KEYFORM_BINDINGS] = len(bindings)
    counts[CountIdx.KEYS] = len(keys_values)
    counts[CountIdx.UVS] = uv_acc
    counts[CountIdx.POSITION_INDICES] = idx_acc
    counts[CountIdx.DRAWABLE_MASKS] = len(mask_entries)
    counts[CountIdx.DRAW_ORDER_GROUPS] = 1
    counts[CountIdx.DRAW_ORDER_GROUP_OBJECTS] = n
    m.counts = counts

    S = m._sections
    S['part.ids'] = ['Root']
    S['part.keyform_binding_band_indices'] = [0]
    S['part.keyform_begin_indices'] = [0]
    S['part.keyform_counts'] = [1]
    S['part.visibles'] = [True]
    S['part.enables'] = [True]
    S['part.parent_part_indices'] = [-1]
    S['part_keyform.draw_orders'] = [500.0]

    S['art_mesh.ids'] = [me['id'] for me in meshes]
    S['art_mesh.keyform_binding_band_indices'] = [me['band'] for me in meshes]
    S['art_mesh.keyform_begin_indices'] = mesh_kf_begin
    S['art_mesh.keyform_counts'] = [me['n_kf'] for me in meshes]
    S['art_mesh.visibles'] = [me.get('visible', True) for me in meshes]
    S['art_mesh.enables'] = [True] * n
    S['art_mesh.parent_part_indices'] = [0] * n
    S['art_mesh.parent_deformer_indices'] = [-1] * n
    S['art_mesh.texture_indices'] = [me['tex'] for me in meshes]
    # drawable_flags = 4 (IsMasked/NORMAL)。白目载体方案（官方样例同构）验证：
    # 载体不设特殊 flag，由 drawable_mask.art_mesh_indices 引用关系决定。
    S['art_mesh.drawable_flags'] = [4] * n
    S['art_mesh.vertex_counts'] = [len(me['positions']) // 2 for me in meshes]
    S['art_mesh.uv_begin_indices'] = uv_off
    S['art_mesh.position_index_begin_indices'] = idx_off
    S['art_mesh.position_index_counts'] = [len(me['indices']) for me in meshes]
    S['art_mesh.mask_begin_indices'] = mask_begin
    S['art_mesh.mask_counts'] = mask_count

    S['parameter.ids'] = [p[0] for p in PARAMS_M3]
    S['parameter.max_values'] = [p[1] for p in PARAMS_M3]
    S['parameter.min_values'] = [p[2] for p in PARAMS_M3]
    S['parameter.default_values'] = [p[3] for p in PARAMS_M3]
    S['parameter.repeats'] = [False] * len(PARAMS_M3)
    S['parameter.decimal_places'] = [3] * len(PARAMS_M3)
    S['parameter.keyform_binding_begin_indices'] = param_binding_begin
    S['parameter.keyform_binding_counts'] = param_binding_count

    S['art_mesh_keyform.opacities'] = kf_opa
    S['art_mesh_keyform.draw_orders'] = kf_order
    S['art_mesh_keyform.keyform_position_begin_indices'] = kf_begin
    S['keyform_position.xys'] = kps

    uvf = []
    for me in meshes:
        uvf += me['uvs']
    S['uv.xys'] = uvf

    idx = []
    for me in meshes:
        idx += me['indices']
    S['position_index.indices'] = idx

    orders = [int(me['draw_order']) for me in meshes]
    S['draw_order_group.object_begin_indices'] = [0]
    S['draw_order_group.object_counts'] = [n]
    S['draw_order_group.object_total_counts'] = [n]
    S['draw_order_group.min_draw_orders'] = [min(orders)]
    S['draw_order_group.max_draw_orders'] = [max(orders)]
    S['draw_order_group_object.types'] = [0] * n
    S['draw_order_group_object.indices'] = list(range(n))
    S['draw_order_group_object.group_indices'] = [-1] * n

    S['keyform_binding_band.begin_indices'] = bands_begin
    S['keyform_binding_band.counts'] = bands_count
    S['keyform_binding_index.indices'] = kbi
    S['keyform_binding.keys_begin_indices'] = binding_keys_begin
    S['keyform_binding.keys_counts'] = binding_keys_count
    S['keys.values'] = keys_values
    S['drawable_mask.art_mesh_indices'] = mask_entries
    S['warp_deformer_keyform.keyform_position_begin_indices'] = []

    # ---- 输出 -------------------------------------------------------------------
    from emote_assemble import SRC as EMOTE_SRC
    os.makedirs(OUT, exist_ok=True)
    moc_path = os.path.join(OUT, f'{ENTRY}.moc3')
    m.to_file(moc_path)
    atlas_dir = os.path.join(EMOTE_SRC, walker.base + '.psb.m')
    for tex in walker.d['source']:
        src = os.path.join(atlas_dir, f'{tex}-texture.png')
        if os.path.exists(src):
            shutil.copy(src, os.path.join(OUT, f'tex{int(tex.split("#")[1]):03d}.png'))
    model3 = {
        "Version": 3,
        "FileReferences": {
            "Moc": f"{ENTRY}.moc3",
            "Textures": sorted(
                f"tex{t:03d}.png" for t in
                (int(x.split('#')[1]) for x in walker.d['source']
                 if os.path.exists(os.path.join(atlas_dir, f'{x}-texture.png')))),
        },
        "Groups": [
            {"Target": "Parameter", "Name": "LipSync",
             "Ids": ["ParamMouthOpenY"]},
            {"Target": "Parameter", "Name": "EyeBlink",
             "Ids": ["ParamEyeLOpen", "ParamEyeROpen"]},
        ],
        "HitAreas": [],
    }
    with open(os.path.join(OUT, f'{ENTRY}.model3.json'), 'w',
              encoding='utf-8') as f:
        json.dump(model3, f, ensure_ascii=False, indent=1)
    with open(os.path.join(OUT, f'{ENTRY}.cdi3.json'), 'w',
              encoding='utf-8') as f:
        json.dump({"Version": 3, "Parameters": [
            {"Id": p[0], "Name": p[5]} for p in PARAMS_M3]},
            f, ensure_ascii=False, indent=1)

    nbound = sum(1 for me in meshes if me['binds'])
    sig_count = Counter(tuple(b[0] for b in me['binds']) or ('<none>',)
                        for me in meshes)
    print(f'moc3: {moc_path}')
    print(f'artmeshes={n} bound={nbound} params={len(PARAMS_M3)} '
          f'bindings={len(bindings)} bands={len(bands_begin)} '
          f'kf_slots={len(kf_begin)} kf_pool={kf_acc}')
    for sig, c in sig_count.most_common():
        print(f'  band {":".join(sig)}: {c} meshes')
    return m


if __name__ == '__main__':
    build()
