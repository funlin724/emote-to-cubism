# -*- coding: utf-8 -*-
"""全量静姿模型生成器（Editor 导出真值校准版，渲染已验证）。

结构 = Editor 导出最小真值 moc3 的极简形态：
  - 无变形器：网格 parent_part=0 / parent_deformer=-1（无变形器时 -1 合法）；
  - 全网格挂空绑定带 0，keyform_counts=1（单个默认姿势 keyform）；
  - keyform 槽位 64 字节对齐（16 floats），KEYFORM_POSITIONS 计数 = 池实际 float 数；
  - 参数绑定仅眨眼两参数各 1 binding（不透明度阶梯键）；
  - 眼部 stencil 遮罩 + draw_order_group 1 组覆盖全部网格。

流程：Emote 层树装配（emote_assemble.Assembler）→ 绘制序排序 →
tristrip 三角化网格 → 世界坐标归一化 → moc3（moc3lib）+ model3/cdi3。

环境变量：
  EMOTE_ENTRY   条目名（缺省 'model'，仅用作输出文件名前缀）
  EMOTE_MOTION_DIR  解包数据目录（同 emote_assemble.py）
  EMOTE_OUT     输出目录（缺省仓库内 output/）

用法:
  python tools/build_moc3.py            # 生成 + 回读自检
"""
import json, os, sys, shutil

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, 'tools'))

from emote_assemble import Assembler, Affine, SRC, PIECE_NUDGES, translation, rotation
from moc3lib import Moc3, MocVersion, CountIdx

ENTRY = os.environ.get('EMOTE_ENTRY', 'model')
OUT = os.environ.get('EMOTE_OUT') or os.path.join(ROOT, 'output')
PARAMS = [
    ('ParamAngleX', 30.0, -30.0, 0.0),
    ('ParamAngleY', 30.0, -30.0, 0.0),
    ('ParamAngleZ', 30.0, -30.0, 0.0),
    ('ParamEyeLOpen', 1.0, 0.0, 1.0),
    ('ParamEyeROpen', 1.0, 0.0, 1.0),
    ('ParamMouthOpenY', 1.0, 0.0, 0.0),
]
CDI_NAMES = {
    'ParamAngleX': '头部左右', 'ParamAngleY': '头部上下', 'ParamAngleZ': '头部倾斜',
    'ParamEyeLOpen': '左眼开闭', 'ParamEyeROpen': '右眼开闭', 'ParamMouthOpenY': '嘴开闭',
}
KF_SLOT_ALIGN = 16  # floats（=64 字节，官方真值模型实测）
# 眨眼：按源帧时间轴的 4 态阶梯切换——8 键，段间 0.02 过渡带
# 模拟 E-mote 的离散图标切换（moc3 键间线性插值，宽过渡带会产生半透明鬼影）。
# 源语义（帧数据实证）：睑片是纯睫毛描线、眼裂区透明，几何上盖不住白/瞳；
# "遮白"靠白图层逐态消失——睁=白图睁态；半闭=白图半闭态；
# 3/4 闭起白隐藏（帧 content 空）+ 虹膜/高光 opa=0。
BLINK_KEYS = [0.0, 0.155, 0.175, 0.485, 0.505, 0.815, 0.835, 1.0]
_CLOSED = [1, 1, 0, 0, 0, 0, 0, 0]
_THREE = [0, 0, 1, 1, 0, 0, 0, 0]
_HALF = [0, 0, 0, 0, 1, 1, 0, 0]
_OPEN = [0, 0, 0, 0, 0, 0, 1, 1]
_IRIS = [0, 0, 0, 0, 1, 1, 1, 1]
# icon -> (参数, 8 键不透明度序列)。基础装配含 0031/0035/0043/0045/0047/0051/
# 0041/0042/0039/0040；其余为追加态（BLINK_EXTRA）。
BLINK_SEQS = {
    '0031': ('ParamEyeLOpen', _OPEN), '0032': ('ParamEyeLOpen', _HALF),
    '0033': ('ParamEyeLOpen', _THREE), '0034': ('ParamEyeLOpen', _CLOSED),
    '0043': ('ParamEyeLOpen', _OPEN), '0044': ('ParamEyeLOpen', _HALF),
    '0047': ('ParamEyeLOpen', _OPEN), '0048': ('ParamEyeLOpen', _HALF),
    '0049': ('ParamEyeLOpen', _THREE), '0050': ('ParamEyeLOpen', _CLOSED),
    '0041': ('ParamEyeLOpen', _IRIS), '0039': ('ParamEyeLOpen', _IRIS),
    # 瞳孔附加件：源数据无眨眼键（靠 stencil），运行时遮罩不可依赖，兜底
    '0087': ('ParamEyeLOpen', _OPEN),
    '0035': ('ParamEyeROpen', _OPEN), '0036': ('ParamEyeROpen', _HALF),
    '0088': ('ParamEyeROpen', _OPEN),
    '0037': ('ParamEyeROpen', _THREE), '0038': ('ParamEyeROpen', _CLOSED),
    '0045': ('ParamEyeROpen', _OPEN), '0046': ('ParamEyeROpen', _HALF),
    '0051': ('ParamEyeROpen', _OPEN), '0052': ('ParamEyeROpen', _HALF),
    '0053': ('ParamEyeROpen', _THREE), '0054': ('ParamEyeROpen', _CLOSED),
    '0042': ('ParamEyeROpen', _IRIS), '0040': ('ParamEyeROpen', _IRIS),
}
# 需要追加装配的状态图标：宿主 -> [追加态 icons]
BLINK_EXTRA = {
    '0031': ['0032', '0033', '0034'], '0035': ['0036', '0037', '0038'],
    '0043': ['0044'], '0045': ['0046'],
    '0047': ['0048', '0049', '0050'], '0051': ['0052', '0053', '0054'],
}
# 蒙版形状载体：挂在 stencil 组下（同步部件），在 Emote 运行时里只作
# 轮廓蒙版形状，不作为可见画渲染（可见会以实色盖住眼周）。
MASK_CARRIER_ICONS = {'0086'}
# 眼部 stencil：瞳/高光/眼加饰 裁进同侧眼白形状。
# 眨眼时白在睁/半闭两态分别是两个图标 → 蒙版取两者并集（mask_count=2），
# 否则半闭段虹膜会被空遮罩整体剔除。
EYE_STENCIL = {'0041': ['0043', '0044'], '0039': ['0043', '0044'],
               '0087': ['0043', '0044'],
               '0042': ['0045', '0046'], '0040': ['0045', '0046'],
               '0088': ['0045', '0046']}
# 【本作校准值】全身柔光剪影瓦片不进模型：拼缝观感差，且轮廓本就完整。
# 图标 ID 清单按具体游戏实测（见 docs/emote-to-cubism-method.md 适配层一节）。
SILHOUETTE_ICONS = ('0137', '0138', '0139', '0140', '0141', '0142')


def align_kf(x):
    return (x + KF_SLOT_ALIGN - 1) // KF_SLOT_ALIGN * KF_SLOT_ALIGN


def collect(assembler):
    # 根 = 时间线主树。【本作校准值】根组参数名按游戏实测（'タイムライン構造'
    # = 时间线结构；部分条目走 '全体構造' 嫁接，见 parse_motion 的 layer_tree.txt）
    inst = assembler.assemble(root_param='タイムライン構造')
    # 部件微调（world ∘ T ∘ R）
    for i in inst:
        ng = PIECE_NUDGES.get(i['icon'])
        if ng:
            sc = ng[3] if len(ng) > 3 else 1.0
            w = i['world'].compose(translation(ng[0], ng[1])).compose(
                rotation(ng[2] if len(ng) > 2 else 0.0))
            if len(ng) > 5:
                ppx, ppy = ng[4], ng[5]
                w = w.compose(translation(ppx, ppy)).compose(
                    Affine(a=sc, d=sc)).compose(translation(-ppx, -ppy))
            elif len(ng) > 3:
                w = w.compose(Affine(a=sc, d=sc))
            i['world'] = w
    inst = [i for i in inst if i['icon'] not in SILHOUETTE_ICONS]
    # 渲染顺序 = 层树深度先序遍历序（metadata.zorder 只是组内局部序，
    # 不能作全局排序键——多对眼件关系曾因此反转）
    inst = sorted(inst, key=lambda i: i['order'])
    meshes = []
    for k, i in enumerate(inst):
        pos, uv, idx = assembler.mesh_data(i)
        meshes.append({
            'id': f"part{i['icon']}_{k}", 'tex': int(i['tex'].split('#')[1]),
            'positions': pos, 'uvs': uv, 'indices': idx,
            'opacity': i['opa'], 'icon': i['icon'], 'draw_order': 500.0 + k,
        })
    # 眨眼 extra（各状态图标）：必须按图标自身 ic 重推导几何（图集位置各异，
    # 复制宿主 UV 会采到睁眼贴图）。
    for k, i in enumerate(inst):
        if i['icon'] not in BLINK_EXTRA:
            continue
        for off, icon in enumerate(BLINK_EXTRA[i['icon']]):
            e = dict(i)
            e['icon'] = icon
            e['tex'], e['ic'] = assembler.icons[icon]
            pos, uv, idx = assembler.mesh_data(e)
            meshes.append({
                'id': f"part{icon}_x{k}", 'tex': int(e['tex'].split('#')[1]),
                'positions': pos, 'uvs': uv, 'indices': idx,
                'opacity': 0.0, 'icon': icon,
                'draw_order': 500.0 + k + 0.25 * (off + 1),
            })
    return meshes


def build_moc3(meshes, canvas_wh, origin_xy, ppu):
    m = Moc3()
    m.header.version = MocVersion.V4_00
    m.header.endian = 0
    m.canvas.pixels_per_unit = ppu
    m.canvas.origin_x, m.canvas.origin_y = origin_xy
    m.canvas.canvas_width, m.canvas.canvas_height = canvas_wh
    m.canvas.canvas_flag = 0

    n = len(meshes)
    # 蒙版载体不可见
    for me in meshes:
        if me['icon'] in MASK_CARRIER_ICONS:
            me['visible'] = False

    # 眼部叠放校正：icon zorder 是组内局部序 + extras 固定步进曾产生并列值，
    # 按期望相对序重排眼部件的 draw_order 值分配（值集不变）。
    # 必须在 keyform 循环之前——kf draw_orders 从 me['draw_order'] 取值。
    import eye_stack
    eye_stack.fix_eye_stack(meshes)

    # 眼件置顶：仅睫毛弧线组置顶，目影组回层树位（白目之上/虹膜之下）——
    # 置顶目影会与弧线、虹膜暗顶带三层暗色叠在瞳孔上部。
    top_ro = max(me['draw_order'] for me in meshes)
    for icons, ro in ((('0031', '0032', '0033', '0034'), top_ro + 1),
                      (('0035', '0036', '0037', '0038'), top_ro + 2)):
        for me in meshes:
            if me['icon'] in icons:
                me['draw_order'] = float(ro)

    # 眼部遮罩：drawable_mask 列表 + 每网格 begin/count
    icon2idx = {}
    for k, me in enumerate(meshes):
        icon2idx.setdefault(me['icon'], k)
    mask_entries = []
    mask_begin = [0] * n
    mask_count = [0] * n
    for k, me in enumerate(meshes):
        srcs = EYE_STENCIL.get(me['icon'])
        if srcs:
            mask_begin[k] = len(mask_entries)
            for s_icon in srcs:
                if s_icon in icon2idx:
                    mask_entries.append(icon2idx[s_icon])
            mask_count[k] = len(mask_entries) - mask_begin[k]

    # 每网格 keyform 数：眨眼组图标 8 个（阶梯键），其余 1 个（默认姿势）
    n_kf_per_mesh = [len(BLINK_SEQS[me['icon']][1]) if me['icon'] in BLINK_SEQS
                     else 1 for me in meshes]

    uv_off, idx_off = [], []
    uv_acc = idx_acc = 0
    for me in meshes:
        nv = len(me['positions']) // 2
        ni = len(me['indices'])
        uv_off.append(uv_acc)
        uv_acc += nv * 2
        idx_off.append(idx_acc)
        idx_acc += ni

    # keyform 池：眨眼网格多个 keyform（不透明度键），其余 1 个；槽位 64 字节对齐
    kf_begin = []          # 每个槽位的 float 偏移
    kf_acc = 0
    for me, nk in zip(meshes, n_kf_per_mesh):
        for _ in range(nk):
            kf_begin.append(kf_acc)
            kf_acc = align_kf(kf_acc + len(me['positions']))
    kps = [0.0] * kf_acc
    kf_opa = []
    kf_order = []
    slot = 0
    for me, nk in zip(meshes, n_kf_per_mesh):
        opas = (BLINK_SEQS[me['icon']][1] if me['icon'] in BLINK_SEQS
                else [me['opacity']])
        for k in range(nk):
            b = kf_begin[slot]
            kps[b:b + len(me['positions'])] = me['positions']
            kf_opa.append(opas[k])
            kf_order.append(me['draw_order'])
            slot += 1
    mesh_kf_begin = []
    cursor = 0
    for nk in n_kf_per_mesh:
        mesh_kf_begin.append(cursor)
        cursor += nk

    # 眨眼绑定接线：
    #   parameter --begin/counts--> binding --keys_begin/counts--> keys
    #   mesh --band--> band --keyform_binding_index--> binding
    blink_params = sorted({pid for pid, _seq in BLINK_SEQS.values()})
    binding_of_param = {name: j for j, name in enumerate(blink_params)}
    n_bind = len(blink_params)
    keys_values = []
    binding_keys_begin = []
    binding_keys_count = []
    for name in blink_params:
        binding_keys_begin.append(len(keys_values))
        binding_keys_count.append(len(BLINK_KEYS))
        keys_values += BLINK_KEYS
    band_of_param = {name: 1 + j for j, name in enumerate(blink_params)}
    bands_begin = [0]
    bands_count = [0]          # band0 = 空（未绑定网格）
    kbi = []
    for j, name in enumerate(blink_params):
        bands_begin.append(len(kbi))
        bands_count.append(1)
        kbi.append(j)

    counts = [0] * 23
    counts[CountIdx.PARTS] = 1
    counts[CountIdx.ART_MESHES] = n
    counts[CountIdx.PARAMETERS] = len(PARAMS)
    counts[CountIdx.PART_KEYFORMS] = 1
    counts[CountIdx.ART_MESH_KEYFORMS] = len(kf_begin)
    counts[CountIdx.KEYFORM_POSITIONS] = kf_acc
    counts[CountIdx.KEYFORM_BINDING_INDICES] = len(kbi)
    counts[CountIdx.KEYFORM_BINDING_BANDS] = len(bands_begin)
    counts[CountIdx.KEYFORM_BINDINGS] = n_bind
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
    S['art_mesh.keyform_binding_band_indices'] = [
        band_of_param[BLINK_SEQS[me['icon']][0]] if me['icon'] in BLINK_SEQS else 0
        for me in meshes]
    S['art_mesh.keyform_begin_indices'] = mesh_kf_begin
    S['art_mesh.keyform_counts'] = n_kf_per_mesh
    S['art_mesh.visibles'] = [me.get('visible', True) for me in meshes]
    S['art_mesh.enables'] = [True] * n
    S['art_mesh.parent_part_indices'] = [0] * n
    S['art_mesh.parent_deformer_indices'] = [-1] * n           # 无变形器时合法
    S['art_mesh.texture_indices'] = [me['tex'] for me in meshes]
    S['art_mesh.drawable_flags'] = [4] * n
    # 规范语义（官方真值模型实测）：vertex_counts = 顶点池大小；
    # position_index_counts = 索引表长度（3×三角形数）
    S['art_mesh.vertex_counts'] = [len(me['positions']) // 2 for me in meshes]
    S['art_mesh.uv_begin_indices'] = uv_off
    S['art_mesh.position_index_begin_indices'] = idx_off
    S['art_mesh.position_index_counts'] = [len(me['indices']) for me in meshes]
    S['art_mesh.mask_begin_indices'] = mask_begin
    S['art_mesh.mask_counts'] = mask_count

    # 参数绑定：眨眼两参数各持 1 binding，其余无绑定（begin=-1，官方惯例）
    S['parameter.ids'] = [p[0] for p in PARAMS]
    S['parameter.max_values'] = [p[1] for p in PARAMS]
    S['parameter.min_values'] = [p[2] for p in PARAMS]
    S['parameter.default_values'] = [p[3] for p in PARAMS]
    S['parameter.repeats'] = [False] * len(PARAMS)
    S['parameter.decimal_places'] = [3] * len(PARAMS)
    begin_list = []
    count_list = []
    for p in PARAMS:
        if p[0] in binding_of_param:
            begin_list.append(binding_of_param[p[0]])
            count_list.append(1)
        else:
            begin_list.append(-1)
            count_list.append(0)
    S['parameter.keyform_binding_begin_indices'] = begin_list
    S['parameter.keyform_binding_counts'] = count_list

    S['art_mesh_keyform.opacities'] = kf_opa
    S['art_mesh_keyform.draw_orders'] = kf_order
    S['art_mesh_keyform.keyform_position_begin_indices'] = kf_begin
    S['keyform_position.xys'] = kps

    uvf = []
    for me in meshes:
        uvf += me['uvs']
    S['uv.xys'] = uvf

    # 索引必须是各网格自身顶点池的局部索引（渲染器逐网格上传独立顶点缓冲，
    # 全局偏移会导致越界 → WebGL 拒绝整次绘制）。position_index_begin_indices
    # 负责在总池中定位各网格的索引段。
    idx = []
    for me in meshes:
        idx += me['indices']
    S['position_index.indices'] = idx

    # draw_order_group：1 组覆盖全部网格
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
    return m


def main():
    a = Assembler()
    meshes = collect(a)

    xs, ys = [], []
    for me in meshes:
        xs += me['positions'][0::2]
        ys += me['positions'][1::2]
    minx, miny = min(xs), min(ys)
    W = max(xs) - minx
    H = max(ys) - miny
    # moc3 顶点语义（pixi-live2d-display centeringTransform 源码确认）：
    #   x_px = v.x*ppu + cw/2,  y_px = v.y*ppu + ch/2   （无 y 翻转，+y 向下！）
    # 故：v.x = (px-cx)/W', v.y = (py-cy)/W', W' = ppu = 画布宽像素。
    W *= 1.05
    H *= 1.05
    for me in meshes:
        p = me['positions']
        norm = []
        for k in range(0, len(p), 2):
            norm += [(p[k] - minx - W / 2) / W, (p[k + 1] - miny - H / 2) / W]
        me['positions'] = norm

    # 眨眼 extra 已在 collect() 内按图标自身几何追加（像素坐标），随后一并归一化。

    os.makedirs(OUT, exist_ok=True)
    moc = build_moc3(meshes, canvas_wh=(W, H), origin_xy=(W / 2, H / 2), ppu=W)
    moc_path = os.path.join(OUT, f'{ENTRY}.moc3')
    moc.to_file(moc_path)

    # 贴图：拷贝全部图集
    atlas_dir = os.path.join(SRC, a.base + '.psb.m')
    for tex in a.d['source']:
        src = os.path.join(atlas_dir, f'{tex}-texture.png')
        if os.path.exists(src):
            shutil.copy(src, os.path.join(OUT, f'tex{int(tex.split("#")[1]):03d}.png'))

    model3 = {
        "Version": 3,
        "FileReferences": {
            "Moc": f"{ENTRY}.moc3",
            "Textures": sorted(
                f"tex{t:03d}.png" for t in
                (int(x.split('#')[1]) for x in a.d['source']
                 if os.path.exists(os.path.join(atlas_dir, f'{x}-texture.png')))),
        },
        "Groups": [
            {"Target": "Parameter", "Name": "LipSync", "Ids": ["ParamMouthOpenY"]},
            {"Target": "Parameter", "Name": "EyeBlink", "Ids": ["ParamEyeLOpen", "ParamEyeROpen"]},
        ],
        "HitAreas": [],
    }
    with open(os.path.join(OUT, f'{ENTRY}.model3.json'), 'w', encoding='utf-8') as f:
        json.dump(model3, f, ensure_ascii=False, indent=1)

    cdi3 = {
        "Version": 3,
        "Parameters": [{"Id": pid, "Name": CDI_NAMES.get(pid, pid)} for pid, *_ in PARAMS],
    }
    with open(os.path.join(OUT, f'{ENTRY}.cdi3.json'), 'w', encoding='utf-8') as f:
        json.dump(cdi3, f, ensure_ascii=False, indent=1)

    nvis = sum(1 for me in meshes if me['opacity'] > 0)
    print(f'moc3: {moc_path}')
    print(f'artmeshes={len(meshes)} visible={nvis} canvas={W:.0f}x{H:.0f}')

    try:
        from moc3lib import Moc3 as M
        chk = M.from_file(moc_path)
        print('readback ok:', chk.summary().splitlines()[0])
    except Exception as e:
        print('readback skipped:', str(e)[:60])


if __name__ == '__main__':
    main()
