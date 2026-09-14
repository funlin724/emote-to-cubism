# -*- coding: utf-8 -*-
"""从 moc3 里 dump rig：变形器/ArtMesh 的参数绑定、关键帧几何、裁剪。

用法::

    python tools/dump_rig.py <a.moc3>                 # 绑定 + 裁剪
    python tools/dump_rig.py <a.moc3> --geom          # 另加关键帧几何对比
    python tools/dump_rig.py <a.moc3> --json o.json   # 另加关键帧顶点坐标
    python tools/dump_rig.py --compare <a> <b>        # 两模型同名件顶点数比对

绑定链（由实际模型导出推导并交叉验证）::

    对象.keyform_binding_band_indices[i] -> band
    band.begin_indices[band] / band.counts[band] -> 区间，索引 keyform_binding_index.indices
    keyform_binding_index.indices[k] -> binding 序号
    binding -> keyform_binding.keys_begin_indices / keys_counts -> keys.values
    参数侧：parameter.keyform_binding_begin_indices/counts -> binding 区间（反查用）

`deformer.types`：0=warp，1=rotation（导出实证）。
"""
import sys
import os
import json
import argparse

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from moc3lib import Moc3  # noqa: E402

DEFORMER_TYPE = {0: 'warp', 1: 'rotation'}


class Rig:
    def __init__(self, path):
        self.m = Moc3.from_file(path)
        S = self.S = self.m._sections
        self.idx = S.get('keyform_binding_index.indices', [])
        self.band_begin = S.get('keyform_binding_band.begin_indices', [])
        self.band_count = S.get('keyform_binding_band.counts', [])
        self.kb_begin = S.get('keyform_binding.keys_begin_indices', [])
        self.kb_count = S.get('keyform_binding.keys_counts', [])
        self.keys = S.get('keys.values', [])
        self.pids = S.get('parameter.ids', [])
        p_begin = S.get('parameter.keyform_binding_begin_indices', [])
        p_cnt = S.get('parameter.keyform_binding_counts', [])
        self.binding2param = {}
        for i, pid in enumerate(self.pids):
            if i >= len(p_begin) or i >= len(p_cnt):
                break
            for b in range(p_begin[i], p_begin[i] + p_cnt[i]):
                self.binding2param[b] = pid

    def bindings_of(self, band):
        """band -> {参数id: [关键值...]}"""
        out = {}
        if band is None or band < 0 or band >= len(self.band_begin):
            return out
        for k in range(self.band_begin[band],
                       self.band_begin[band] + self.band_count[band]):
            if k >= len(self.idx):
                continue
            b = self.idx[k]
            pid = self.binding2param.get(b, 'binding%d' % b)
            if b < len(self.kb_begin) and b < len(self.kb_count):
                vals = self.keys[self.kb_begin[b]:self.kb_begin[b] + self.kb_count[b]]
            else:
                vals = []
            out.setdefault(pid, []).extend(vals)
        return out

    def obj_params(self, prefix, i):
        bands = self.S.get(prefix + '.keyform_binding_band_indices', [])
        return self.bindings_of(bands[i] if i < len(bands) else None)

    def rot_keyform(self, i):
        kf0 = self.S.get('rotation_deformer.keyform_begin_indices', [])
        kfc = self.S.get('rotation_deformer.keyform_counts', [])
        if i >= len(kf0) or i >= len(kfc):
            return None
        out = {}
        for nm in ('opacities', 'angles', 'origin_xs', 'origin_ys', 'scales'):
            arr = self.S.get('rotation_deformer_keyform.' + nm, [])
            out[nm] = arr[kf0[i]:kf0[i] + kfc[i]]
        return out

    def vertices(self, prefix, i):
        """该对象各关键帧的顶点坐标 [[x,y,...], ...]"""
        kf0 = self.S.get(prefix + '.keyform_begin_indices', [])
        kfc = self.S.get(prefix + '.keyform_counts', [])
        vc = self.S.get(prefix + '.vertex_counts', [])
        if i >= len(kf0) or i >= len(kfc):
            return []
        n = vc[i] if i < len(vc) else 0
        kfp = self.S.get(prefix + '_keyform.keyform_position_begin_indices', [])
        K = self.S.get('keyform_position.xys', [])
        res = []
        for k in range(kfc[i]):
            j = kf0[i] + k
            if j >= len(kfp):
                break
            o = kfp[j]
            res.append(K[o:o + 2 * n])
        return res


def dump(path, geom=False, json_path=None):
    r = Rig(path)
    m = r.m
    print('=== %s ===' % path)
    print('版本 v%d  canvas %.0fx%.0f  ppu=%.1f  ArtMesh=%d 参数=%d'
          % (m.header.version, m.canvas.canvas_width, m.canvas.canvas_height,
             m.canvas.pixels_per_unit, m.counts[4], m.counts[5]))
    print('计数: 变形器=%d(warp %d, rotation %d) warp关键帧=%d rot关键帧=%d '
          'ArtMesh关键帧=%d 顶点池=%d floats'
          % (m.counts[1], m.counts[2], m.counts[3], m.counts[7], m.counts[8],
             m.counts[9], m.counts[10]))

    d_ids = r.S.get('deformer.ids', [])
    d_types = r.S.get('deformer.types', [])
    d_parent = r.S.get('deformer.parent_deformer_indices', [])
    print('\n--- 变形器 (%d) ---' % len(d_ids))
    for i, did in enumerate(d_ids):
        t = d_types[i] if i < len(d_types) else -1
        ps = r.obj_params('deformer', i)
        txt = ', '.join('%s=%s' % (k, [round(x, 3) for x in v]) for k, v in ps.items())
        print('  [%d] %-10s %-8s parent=%-4s %s'
              % (i, did, DEFORMER_TYPE.get(t, t),
                 d_parent[i] if i < len(d_parent) else '?', txt or '(无绑定)'))

    am_ids = r.S.get('art_mesh.ids', [])
    print('\n--- ArtMesh 有绑定的 ---')
    nb = 0
    for i, aid in enumerate(am_ids):
        ps = r.obj_params('art_mesh', i)
        if ps:
            nb += 1
            print('  %-12s %s' % (aid, ', '.join(
                '%s=%s' % (k, [round(x, 3) for x in v]) for k, v in ps.items())))
    print('  共 %d / %d 条有参数绑定' % (nb, len(am_ids)))

    print('\n--- 裁剪 ---')
    mb = r.S.get('art_mesh.mask_begin_indices', [])
    mc = r.S.get('art_mesh.mask_counts', [])
    dm = r.S.get('drawable_mask.art_mesh_indices', [])
    any_c = False
    for i, aid in enumerate(am_ids):
        b = mb[i] if i < len(mb) else 0
        c = mc[i] if i < len(mc) else 0
        if c:
            any_c = True
            print('  %-12s <- %s' % (aid, ', '.join(
                am_ids[x] for x in dm[b:b + c] if x < len(am_ids))))
    if not any_c:
        print('  （无）')

    if geom:
        print('\n--- 关键帧几何（判断是否真变形）---')
        for i, did in enumerate(d_ids):
            t = d_types[i] if i < len(d_types) else -1
            if t == 1:
                g = r.rot_keyform(i)
                if g:
                    print('  %-10s rotation keyforms=%d angles=%s originX=%s'
                          % (did, len(g['angles']), [round(x, 2) for x in g['angles']],
                             [round(x, 1) for x in g['origin_xs']]))
                continue
            g = r.vertices('warp_deformer', i)
            if len(g) > 1:
                base = g[0]
                dmax = max((max(abs(a - b) for a, b in zip(kf, base)) for kf in g[1:]),
                           default=0.0)
                print('  %-10s warp    关键帧=%d 顶点=%d 相对首帧最大位移=%.5f'
                      % (did, len(g), len(base) // 2, dmax))
        for i, aid in enumerate(am_ids):
            g = r.vertices('art_mesh', i)
            if len(g) > 1:
                base = g[0]
                dmax = max((max(abs(a - b) for a, b in zip(kf, base)) for kf in g[1:]),
                           default=0.0)
                print('  %-12s ArtMesh 关键帧=%d 顶点=%d 相对首帧最大位移=%.5f'
                      % (aid, len(g), len(base) // 2, dmax))
        tot = 0
        for src, ids in (('warp_deformer', d_ids), ('art_mesh', am_ids)):
            vc = r.S.get(src + '.vertex_counts', [])
            kfc = r.S.get(src + '.keyform_counts', [])
            for i in range(len(ids)):
                tot += (vc[i] if i < len(vc) else 0) * (kfc[i] if i < len(kfc) else 0)
        pool = len(r.S.get('keyform_position.xys', [])) // 2
        print('  顶点池：按(顶点数×关键帧数)需 %d 对，实际 %d 对 → %s'
              % (tot, pool, '吻合' if tot == pool else '★差 %d 对' % (pool - tot)))

    if json_path:
        data = {'file': path, 'deformers': [], 'art_meshes': {}}
        for i, did in enumerate(d_ids):
            data['deformers'].append({'id': did,
                                      'type': DEFORMER_TYPE.get(d_types[i], '?'),
                                      'params': r.obj_params('deformer', i)})
        for i, aid in enumerate(am_ids):
            data['art_meshes'][aid] = {'params': r.obj_params('art_mesh', i),
                                       'vertices': r.vertices('art_mesh', i)}
        with open(json_path, 'w', encoding='utf-8') as f:
            json.dump(data, f, ensure_ascii=False)
        print('\n已写入 %s' % json_path)


def compare(pa, pb):
    A, B = Rig(pa), Rig(pb)
    ia, ib = A.S.get('art_mesh.ids', []), B.S.get('art_mesh.ids', [])
    va, vb = A.S.get('art_mesh.vertex_counts', []), B.S.get('art_mesh.vertex_counts', [])
    ma, mb = dict(zip(ia, va)), dict(zip(ib, vb))
    same = diff = 0
    for i in ia:
        if i in mb:
            if ma[i] == mb[i]:
                same += 1
            else:
                diff += 1
                if diff <= 15:
                    print('  %-14s A=%s  B=%s  ★不同' % (i, ma[i], mb[i]))
    onlya = sum(1 for i in ia if i not in mb)
    onlyb = sum(1 for i in ib if i not in ma)
    print('同名件顶点数一致=%d 不同=%d 仅A有=%d 仅B有=%d' % (same, diff, onlya, onlyb))
    return diff == 0


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('args', nargs='+')
    ap.add_argument('--geom', action='store_true')
    ap.add_argument('--json', default=None)
    ap.add_argument('--compare', action='store_true')
    a = ap.parse_args()
    if a.compare:
        return 0 if compare(a.args[0], a.args[1]) else 2
    dump(a.args[0], geom=a.geom, json_path=a.json)
    return 0


if __name__ == '__main__':
    sys.exit(main())
