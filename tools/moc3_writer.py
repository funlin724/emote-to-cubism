# -*- coding: utf-8 -*-
"""MOC3 最小示例生成器（1-2 个四边形网格的最小可验证模型）。

基于 py-moc3 的 section 布局 + 官方样例模型逆向出的语义（详见
docs/moc3-format-semantics.md）:
- 顶点基础坐标存放在 art_mesh 的默认 keyform（单 keyform、空绑定带）中，
  模型空间为归一化 [0..1]，x 向右、y 向下。
- vertex_counts = 每网格顶点池大小（=UV 对数）; position_index_counts = 索引表长度（3×三角数）。
  （注意：早期版本此二字段语义写反，曾致"只渲染第一个网格"的碎片现象。）
- position_index.indices 为各网格**局部索引**（0 基于自身顶点池）；
  uv.xys / keyform_position.xys 均为 float 平铺数组。
- 绑定带 0 为空带（begin=0,count=0），所有对象默认引用它 → 无参数绑定的静态网格。
- 参数无绑定时 keyform_binding_begin_indices = -1（写 0 会被当 binding 0）。

示例：从 FreeMote 解包的 Emote PSB 图集取两个图标矩形，生成最小 moc3。
环境变量：EMOTE_MOTION_DIR（数据目录）、EMOTE_ENTRY（条目名，缺省 'model'）。

用法:
  python tools/moc3_writer.py            # 生成最小模型到 output/
"""
import json, os, sys, shutil

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, 'tools'))

from moc3lib import Moc3, MocVersion, CountIdx

ENTRY = os.environ.get('EMOTE_ENTRY', 'model')
OUT = os.environ.get('EMOTE_OUT') or os.path.join(ROOT, 'output')


def quad_mesh(mesh_id, tex_no, center, size, uv_rect, draw_order):
    """构造一个 2 三角形四边形网格的数据包。uv_rect=(u0,v0,u1,v1), v 向下。"""
    cx, cy = center
    w, h = size
    x0, y0, x1, y1 = cx - w / 2, cy - h / 2, cx + w / 2, cy + h / 2
    u0, v0, u1, v1 = uv_rect
    positions = [x0, y0, x1, y0, x0, y1, x1, y1]      # 4 顶点 (模型空间)
    uvs = [u0, v0, u1, v0, u0, v1, u1, v1]
    indices = [0, 1, 2, 2, 1, 3]                       # 三角扇
    return {
        'id': mesh_id, 'tex': tex_no, 'positions': positions,
        'uvs': uvs, 'indices': indices, 'draw_order': draw_order,
    }


def build_model(meshes, params, canvas_wh=(1.0, 1.0), ppu=1024.0):
    """meshes: list[dict from quad_mesh]; params: list[(id,max,min,def)]"""
    m = Moc3()
    m.header.version = MocVersion.V4_00   # 与官方样例相同的版本号
    m.header.endian = 0

    m.canvas.pixels_per_unit = ppu
    m.canvas.origin_x = canvas_wh[0] / 2
    m.canvas.origin_y = canvas_wh[1] / 2
    m.canvas.canvas_width = canvas_wh[0]
    m.canvas.canvas_height = canvas_wh[1]
    m.canvas.canvas_flag = 0

    n_mesh = len(meshes)
    n_param = len(params)

    # 顶点池/索引偏移累计
    uv_off, idx_off = [], []
    uv_acc = idx_acc = 0
    for me in meshes:
        nv = len(me['positions']) // 2          # 顶点池大小
        ni = len(me['indices'])                  # 索引表长度
        uv_off.append(uv_acc); uv_acc += nv * 2
        idx_off.append(idx_acc); idx_acc += ni

    # keyform 位置: 每网格 1 个 keyform, 位置池 = 顶点池
    kf_pos_begin, kf_pos_acc = [], 0
    for me in meshes:
        kf_pos_begin.append(kf_pos_acc)
        kf_pos_acc += len(me['positions'])

    counts = [0] * 23
    counts[CountIdx.PARTS] = 1
    counts[CountIdx.ART_MESHES] = n_mesh
    counts[CountIdx.PARAMETERS] = n_param
    counts[CountIdx.PART_KEYFORMS] = 1
    counts[CountIdx.ART_MESH_KEYFORMS] = n_mesh
    counts[CountIdx.KEYFORM_POSITIONS] = kf_pos_acc
    counts[CountIdx.KEYFORM_BINDING_BANDS] = 1   # 带 0（空带），所有对象引用它
    counts[CountIdx.UVS] = uv_acc
    counts[CountIdx.POSITION_INDICES] = idx_acc
    m.counts = counts

    S = m._sections
    S['part.ids'] = ['Root']
    S['part.keyform_binding_band_indices'] = [0]
    S['part.keyform_begin_indices'] = [0]
    S['part.keyform_counts'] = [1]
    S['part.visibles'] = [True]
    S['part.enables'] = [True]
    S['part.parent_part_indices'] = [-1]

    S['art_mesh.ids'] = [me['id'] for me in meshes]
    S['art_mesh.keyform_binding_band_indices'] = [0] * n_mesh
    S['art_mesh.keyform_begin_indices'] = list(range(n_mesh))
    S['art_mesh.keyform_counts'] = [1] * n_mesh
    S['art_mesh.visibles'] = [True] * n_mesh
    S['art_mesh.enables'] = [True] * n_mesh
    S['art_mesh.parent_part_indices'] = [0] * n_mesh
    S['art_mesh.parent_deformer_indices'] = [-1] * n_mesh
    S['art_mesh.texture_indices'] = [me['tex'] for me in meshes]
    S['art_mesh.drawable_flags'] = [4] * n_mesh
    # 规范语义：vertex_counts = 顶点池大小；position_index_counts = 索引表长度
    S['art_mesh.vertex_counts'] = [len(me['positions']) // 2 for me in meshes]
    S['art_mesh.uv_begin_indices'] = uv_off
    S['art_mesh.position_index_begin_indices'] = idx_off
    S['art_mesh.position_index_counts'] = [len(me['indices']) for me in meshes]
    S['art_mesh.mask_begin_indices'] = [0] * n_mesh
    S['art_mesh.mask_counts'] = [0] * n_mesh

    S['parameter.ids'] = [p[0] for p in params]
    S['parameter.max_values'] = [p[1] for p in params]
    S['parameter.min_values'] = [p[2] for p in params]
    S['parameter.default_values'] = [p[3] for p in params]
    S['parameter.repeats'] = [False] * n_param
    S['parameter.decimal_places'] = [3] * n_param
    # 参数全部无绑定：begin=-1 / count=0（写 0 会被当 binding 0）
    S['parameter.keyform_binding_begin_indices'] = [-1] * n_param
    S['parameter.keyform_binding_counts'] = [0] * n_param

    S['part_keyform.draw_orders'] = [0.0]

    S['art_mesh_keyform.opacities'] = [1.0] * n_mesh
    S['art_mesh_keyform.draw_orders'] = [me['draw_order'] for me in meshes]
    S['art_mesh_keyform.keyform_position_begin_indices'] = kf_pos_begin

    kps = []
    for me in meshes:
        kps += me['positions']      # 默认 keyform = 基础顶点坐标
    S['keyform_position.xys'] = kps

    uvf = []
    for me in meshes:
        uvf += me['uvs']
    S['uv.xys'] = uvf

    # 索引 = 各网格自身顶点池的局部索引（全局偏移会越界，WebGL 静默拒绘）
    idx = []
    for me in meshes:
        idx += me['indices']
    S['position_index.indices'] = idx

    # 空绑定带 0（所有对象引用）
    S['keyform_binding_band.begin_indices'] = [0]
    S['keyform_binding_band.counts'] = [0]
    S['keyform_binding_index.indices'] = []
    S['keyform_binding.keys_begin_indices'] = []
    S['keyform_binding.keys_counts'] = []
    S['keys.values'] = []

    S['drawable_mask.art_mesh_indices'] = []
    S['warp_deformer_keyform.keyform_position_begin_indices'] = []
    return m


def build_minimal():
    """从 Emote PSB 图集取两个图标，生成最小可验证模型。"""
    src = os.environ.get('EMOTE_MOTION_DIR') or os.path.join(ROOT, 'data', 'motion')
    entry = os.environ.get('EMOTE_ENTRY', 'model')
    atlas_json = json.load(open(
        os.path.join(src, entry + '.psb.m.json'), encoding='utf-8-sig'))
    # 自动取图集尺寸（避免硬编码；图集 NPOT/POT 均可）
    from PIL import Image
    atlas_png = os.path.join(src, entry + '.psb.m', 'tex#000-texture.png')
    aw, ah = Image.open(atlas_png).size
    icons = atlas_json['source']['tex#000']['icon']
    # 取前两个带 4 位数字 ID 的图标做演示
    icon_ids = [k for k in icons if str(k).isdigit() and len(str(k)) == 4][:2]
    if len(icon_ids) < 2:
        raise SystemExit('图集内数字图标不足 2 个，无法生成演示模型')

    meshes = []
    for k, icon_id in enumerate(icon_ids):
        ic = icons[icon_id]
        l, t, w, h = (ic['left'] / aw, ic['top'] / ah,
                      ic['width'] / aw, ic['height'] / ah)
        target_w = 0.25 - k * 0.05
        s = target_w / w
        meshes.append(quad_mesh(
            f'Demo{k}', 0, (0.35 + k * 0.3, 0.35),
            (target_w, h * s), (l, t, l + w, t + h), 500 + k * 100))

    params = [
        ('ParamAngleX', 30.0, -30.0, 0.0),
        ('ParamAngleY', 30.0, -30.0, 0.0),
        ('ParamAngleZ', 30.0, -30.0, 0.0),
    ]
    # 画布用像素尺度（对齐官方样例惯例），保证默认视野变换一致
    m = build_model(meshes, params, canvas_wh=(2976.0, 4175.0), ppu=2976.0)

    os.makedirs(OUT, exist_ok=True)
    moc_path = os.path.join(OUT, f'{ENTRY}.moc3')
    m.to_file(moc_path)

    shutil.copy(atlas_png, os.path.join(OUT, 'tex000.png'))

    model3 = {
        "Version": 3,
        "FileReferences": {
            "Moc": f"{ENTRY}.moc3",
            "Textures": ["tex000.png"],
        },
        "Groups": [],
        "HitAreas": [],
    }
    with open(os.path.join(OUT, f'{ENTRY}.model3.json'), 'w', encoding='utf-8') as f:
        json.dump(model3, f, ensure_ascii=False, indent=1)

    cdi3 = {
        "Version": 3,
        "Parameters": [
            {"Id": pid, "Name": nm}
            for pid, nm in zip([p[0] for p in params],
                               ["头部左右", "头部上下", "头部倾斜"])
        ],
    }
    with open(os.path.join(OUT, f'{ENTRY}.cdi3.json'), 'w', encoding='utf-8') as f:
        json.dump(cdi3, f, ensure_ascii=False, indent=1)

    print('minimal model written to', OUT)
    return moc_path


if __name__ == '__main__':
    p = build_minimal()
    # 回读自检
    from moc3lib import Moc3 as M
    chk = M.from_file(p)
    print(chk.summary())
    print('artmesh ids:', chk.art_mesh_ids)
    print('params:', chk.parameter_ids)
