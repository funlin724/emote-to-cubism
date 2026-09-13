# -*- coding: utf-8 -*-
"""眼部件模板烘焙【退役工具】——皮套架构下不再使用。

历史：运行时遮罩曾被判"不可依赖"，以本工具把眼白覆盖度×眼裂包络乘进
瞳系件图集 alpha 作为等效 stencil。后来经探针证实 mask 数据解析/布局与
官方文件结构平行、无文件级 bug，且"眼珠 360° 转动 + 差分切换"目标要求
occlusion 走运行时 mask，烘焙焊死路线退役。本工具保留仅供历史对照，
build_moc3.py 默认不再调用。

用法：python tools/bake_stencil.py <model_dir>   # 原地修改 <model_dir>/tex000.png
"""
import os
import sys

import numpy as np
from PIL import Image

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, 'tools'))

from emote_assemble import Assembler, Affine, PIECE_NUDGES, translation, rotation

# 被裁剪件 → 该侧眼白状态件（覆盖度取并集）。【本作校准值】
STENCIL = {
    '0041': ('0043', '0044'), '0039': ('0043', '0044'), '0087': ('0043', '0044'),
    '0042': ('0045', '0046'), '0040': ('0045', '0046'), '0088': ('0045', '0046'),
}
WHITES = ('0043', '0044', '0045', '0046')


def mesh_triangles(pos, uvs, idx, atlas_wh, x0=0.0, y0=0.0):
    """生成 (画布区域三角形顶点, uv三角形texel坐标) 迭代。"""
    ah, aw = atlas_wh
    for t in range(0, len(idx), 3):
        a, b, c = idx[t], idx[t + 1], idx[t + 2]
        p = [(pos[2 * k] - x0, pos[2 * k + 1] - y0) for k in (a, b, c)]
        tuv = [(uvs[2 * k] * aw, uvs[2 * k + 1] * ah) for k in (a, b, c)]
        yield p, tuv


def rasterize(pos, uvs, idx, atlas, x0, y0, w, h):
    """网格软光栅到画布区域，返回 (h, w, 4) float32。"""
    out = np.zeros((h, w, 4), np.float32)
    for p, tuv in mesh_triangles(pos, uvs, idx, atlas.shape[:2], x0, y0):
        minx = max(0, int(min(q[0] for q in p)))
        maxx = min(w - 1, int(max(q[0] for q in p)) + 1)
        miny = max(0, int(min(q[1] for q in p)))
        maxy = min(h - 1, int(max(q[1] for q in p)) + 1)
        if minx >= maxx or miny >= maxy:
            continue
        gx, gy = np.meshgrid(np.arange(minx, maxx) + 0.5,
                             np.arange(miny, maxy) + 0.5)
        (x1, y1), (x2, y2), (x3, y3) = p
        den = (y2 - y3) * (x1 - x3) + (x3 - x2) * (y1 - y3)
        if abs(den) < 1e-9:
            continue
        w1 = ((y2 - y3) * (gx - x3) + (x3 - x2) * (gy - y3)) / den
        w2 = ((y3 - y1) * (gx - x3) + (x1 - x3) * (gy - y3)) / den
        w3 = 1.0 - w1 - w2
        m = (w1 >= 0) & (w2 >= 0) & (w3 >= 0)
        if not m.any():
            continue
        su = np.clip((w1 * tuv[0][0] + w2 * tuv[1][0] + w3 * tuv[2][0]).astype(np.int32),
                     0, atlas.shape[1] - 1)
        sv = np.clip((w1 * tuv[0][1] + w2 * tuv[1][1] + w3 * tuv[2][1]).astype(np.int32),
                     0, atlas.shape[0] - 1)
        tex = atlas[sv, su].astype(np.float32)
        al = (tex[:, :, 3:4] / 255.0) * m[:, :, None]
        dst = out[miny:maxy, minx:maxx]
        dst[:, :, :3] = np.maximum(dst[:, :, :3], tex[:, :, :3] * al)
        dst[:, :, 3:] = np.maximum(dst[:, :, 3:], al * 255.0)
    return out


def texel_map(pos, uvs, idx, atlas_wh, x0, y0, w, h):
    """画布像素 → texel (ty, tx)，无映射处为 -1。"""
    ah, aw = atlas_wh
    texels = np.full((h, w, 2), -1, np.int32)
    for p, tuv in mesh_triangles(pos, uvs, idx, atlas_wh, x0, y0):
        minx = max(0, int(min(q[0] for q in p)))
        maxx = min(w - 1, int(max(q[0] for q in p)) + 1)
        miny = max(0, int(min(q[1] for q in p)))
        maxy = min(h - 1, int(max(q[1] for q in p)) + 1)
        if minx >= maxx or miny >= maxy:
            continue
        gx, gy = np.meshgrid(np.arange(minx, maxx) + 0.5,
                             np.arange(miny, maxy) + 0.5)
        (x1, y1), (x2, y2), (x3, y3) = p
        den = (y2 - y3) * (x1 - x3) + (x3 - x2) * (y1 - y3)
        if abs(den) < 1e-9:
            continue
        w1 = ((y2 - y3) * (gx - x3) + (x3 - x2) * (gy - y3)) / den
        w2 = ((y3 - y1) * (gx - x3) + (x1 - x3) * (gy - y3)) / den
        w3 = 1.0 - w1 - w2
        m = (w1 >= 0) & (w2 >= 0) & (w3 >= 0)
        if not m.any():
            continue
        su = (w1 * tuv[0][0] + w2 * tuv[1][0] + w3 * tuv[2][0]).astype(np.int32)
        sv = (w1 * tuv[0][1] + w2 * tuv[1][1] + w3 * tuv[2][1]).astype(np.int32)
        reg = texels[miny:maxy, minx:maxx]
        rr, cc = np.nonzero(m)
        reg[rr, cc, 0] = np.clip(sv[rr, cc], 0, ah - 1)
        reg[rr, cc, 1] = np.clip(su[rr, cc], 0, aw - 1)
    return texels


def bake(model_dir):
    a = Assembler()
    insts = a.assemble(root_param='タイムライン構造')
    # 与构建管线一致：应用部件微调（否则烘焙裁剪边界与实际显示错位）
    for it in insts:
        ng = PIECE_NUDGES.get(it['icon'])
        if ng:
            sc = ng[3] if len(ng) > 3 else 1.0
            w = it['world'].compose(translation(ng[0], ng[1])).compose(
                rotation(ng[2] if len(ng) > 2 else 0.0))
            if len(ng) > 5:
                ppx, ppy = ng[4], ng[5]
                w = w.compose(translation(ppx, ppy)).compose(
                    Affine(a=sc, d=sc)).compose(translation(-ppx, -ppy))
            elif len(ng) > 3:
                w = w.compose(Affine(a=sc, d=sc))
            it['world'] = w
    by_icon = {}
    for it in insts:
        by_icon.setdefault(it['icon'], it)

    need = sorted(set(STENCIL.keys()) | set(WHITES))
    meshes = {}
    xs, ys = [], []
    for icon in need:
        if icon not in by_icon:
            print(f'  ! icon {icon} 不在装配结果，跳过')
            continue
        p, u, ix = a.mesh_data(by_icon[icon])
        meshes[icon] = (p, u, ix)
        xs += p[0::2]
        ys += p[1::2]
    x0, y0 = int(min(xs)) - 4, int(min(ys)) - 4
    x1, y1 = int(max(xs)) + 4, int(max(ys)) + 4
    w, h = x1 - x0, y1 - y0
    print(f'眼区画布范围: ({x0},{y0})-({x1},{y1}) {w}x{h}')

    atlas = np.asarray(Image.open(os.path.join(
        model_dir, 'tex000.png')).convert('RGBA')).astype(np.float32)
    atlas_wh = (atlas.shape[0], atlas.shape[1])

    # 覆盖度场：两侧眼白并集（纹理 alpha 软边）
    cov = np.zeros((h, w), np.float32)
    for icon in WHITES:
        if icon not in meshes:
            continue
        p, u, ix = meshes[icon]
        r = rasterize(p, u, ix, atlas, x0, y0, w, h)
        cov = np.maximum(cov, r[:, :, 3] / 255.0)

    # 眼裂上缘包络：睫毛线的上边界（腐蚀去尖刺后取顶行，
    # 缺口线性插值 + 滑动平均平滑）。包络之上的 texel alpha 归零（2px 羽化）。
    env = np.full(w, np.nan, np.float32)
    for icon in ('0047', '0051'):
        if icon not in by_icon:
            continue
        p, u, ix = a.mesh_data(by_icon[icon])
        r = rasterize(p, u, ix, atlas, x0, y0, w, h)[:, :, 3]
        er = r.copy()
        for sh in range(-3, 4):
            for sv in range(-3, 4):
                er = np.minimum(er, np.roll(np.roll(r, sh, axis=1), sv, axis=0))
        solid = er > 128
        cols, vals = [], []
        for col in range(w):
            rows = np.nonzero(solid[:, col])[0]
            if len(rows):
                cols.append(col)
                vals.append(y0 + rows.min())
        if not cols:
            continue
        interp = np.interp(np.arange(w), np.asarray(cols), np.asarray(vals))
        kern = np.ones(9) / 9.0
        sm = np.convolve(interp, kern, mode='same')
        valid = np.zeros(w, bool)
        valid[cols[0]:cols[-1] + 1] = True
        env[valid] = np.fmin(env[valid], sm[valid])

    def env_factor(cy, cx):
        yv = y0 + cy
        px = env[cx]
        f = np.ones(len(cy), np.float32)
        ok = ~np.isnan(px)
        if ok.any():
            t = (yv[ok] - (px[ok] - 2.0)) / 2.0
            f[ok] = np.clip(t, 0.0, 1.0)
        return f

    total = 0

    # 眼白穹顶高出眼裂的部分按包络归零（露皮肤，与游戏一致）
    for icon in WHITES:
        if icon not in meshes:
            continue
        p, u, ix = meshes[icon]
        texels = texel_map(p, u, ix, atlas_wh, x0, y0, w, h)
        vis = texels[:, :, 0] >= 0
        cy, cx = np.nonzero(vis)
        if len(cy) == 0:
            continue
        ty, tx = texels[cy, cx, 0], texels[cy, cx, 1]
        ef = env_factor(y0 + cy, cx)
        old_a = atlas[ty, tx, 3].copy()
        new_a = old_a * ef
        atlas[ty, tx, 3] = new_a
        total += int((np.abs(new_a - old_a) > 1).sum())
        print(f'  {icon}: 眼白包络裁剪 texel '
              f'{int((np.abs(new_a - old_a) > 1).sum())}')
    for icon in STENCIL:
        if icon not in meshes:
            continue
        p, u, ix = meshes[icon]
        texels = texel_map(p, u, ix, atlas_wh, x0, y0, w, h)
        vis = texels[:, :, 0] >= 0
        cy, cx = np.nonzero(vis)
        if len(cy) == 0:
            print(f'  {icon}: 无可见 texel，跳过')
            continue
        ty, tx = texels[cy, cx, 0], texels[cy, cx, 1]
        fade = cov[cy, cx] * env_factor(y0 + cy, cx)
        old_a = atlas[ty, tx, 3].copy()
        new_a = old_a * fade
        diff = int((np.abs(new_a - old_a) > 1).sum())
        atlas[ty, tx, 3] = new_a
        total += diff
        print(f'  {icon}: 修改 texel {diff}')
    Image.fromarray(atlas.astype(np.uint8), 'RGBA').save(
        os.path.join(model_dir, 'tex000.png'))
    print(f'共修改 {total} texel -> {os.path.join(model_dir, "tex000.png")}')


if __name__ == '__main__':
    if len(sys.argv) < 2:
        print(__doc__)
        sys.exit(1)
    bake(sys.argv[1])
