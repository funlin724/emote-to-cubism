# -*- coding: utf-8 -*-
"""Emote Motion PSB 深解析工具:
1) 按 icon UV 从图集切割分层部件 PNG
2) 导出部件清单(图集来源/UV/origin/zorder/网格概要)
3) 导出参数与关键帧曲线摘要
4) 导出层树摘要
5) 导出 metadata 概要（眨眼/眼嘴控制/物理等）

数据目录（FreeMote PsbDecompile 的产物，见 docs/emote-psb-structure.md）：
  <SRC>/<条目名>.psb.m.json        模型结构
  <SRC>/<条目名>.psb.m.resx.json   资源表（顶点数值 flatten 数组）
  <SRC>/<条目名>.psb.m/            tex#NNN-texture.png 图集

SRC 目录默认取环境变量 EMOTE_MOTION_DIR，否则为仓库内 data/motion。
条目名与输出目录为必填位置参数（缺省值会诱导用户提交他人素材，故不设）。

用法:
  python tools/parse_motion.py <条目名> <输出目录>
例:
  EMOTE_MOTION_DIR=/path/to/decompiled/motion python tools/parse_motion.py char_a out/char_a
"""
import json, os, csv, sys
from PIL import Image

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SRC = os.environ.get('EMOTE_MOTION_DIR') or os.path.join(ROOT, 'data', 'motion')
if len(sys.argv) < 3:
    print(__doc__)
    sys.exit(1)
BASE = sys.argv[1]
OUT = sys.argv[2]

os.makedirs(os.path.join(OUT, 'parts'), exist_ok=True)
os.makedirs(os.path.join(OUT, 'curves'), exist_ok=True)

d = json.load(open(os.path.join(SRC, BASE + '.psb.m.json'), encoding='utf-8-sig'))
resx = json.load(open(os.path.join(SRC, BASE + '.psb.m.resx.json'), encoding='utf-8-sig'))

# ---- flatten arrays (顶点等数值资源) ----
flat = resx.get('ExtraFlattenArrays', {})

def resolve(v):
    if isinstance(v, str) and v.startswith('#resource@'):
        return flat.get(v[len('#resource@'):])
    return v

# ---- 1) 切部件 ----
src = d['source']
atlas_imgs = {}
parts = []
for tex_name, tex in src.items():
    if not (isinstance(tex, dict) and 'icon' in tex):
        continue
    png = os.path.join(SRC, BASE + '.psb.m', tex_name + '-texture.png')
    if tex_name not in atlas_imgs:
        atlas_imgs[tex_name] = Image.open(png).convert('RGBA')
    im = atlas_imgs[tex_name]
    for icon_id, ic in sorted(tex['icon'].items()):
        l, t, w, h = ic['left'], ic['top'], ic['width'], ic['height']
        crop = im.crop((int(l), int(t), int(l + w), int(t + h)))
        name = f"{tex_name.replace('#','')}_{icon_id}.png"
        crop.save(os.path.join(OUT, 'parts', name))
        mesh = ic.get('mesh') or {}
        parts.append({
            'part': name, 'atlas': tex_name, 'icon': icon_id,
            'uv_left': l, 'uv_top': t, 'w': w, 'h': h,
            'originX': ic.get('originX'), 'originY': ic.get('originY'),
            'zorder': (ic.get('metadata') or {}).get('zorder'),
            'mesh_hulls': len(resolve(mesh.get('convexHulls')) or []) + len(resolve(mesh.get('concaveHulls')) or []),
        })

with open(os.path.join(OUT, 'parts_list.csv'), 'w', newline='', encoding='utf-8-sig') as f:
    wr = csv.DictWriter(f, fieldnames=list(parts[0].keys()))
    wr.writeheader(); wr.writerows(parts)

# ---- 2) 参数与关键帧曲线 ----
obj = d['object']
curves_summary = []
for group, g in obj.items():
    if not (isinstance(g, dict) and isinstance(g.get('motion'), dict)):
        continue
    for param, p in g['motion'].items():
        if not isinstance(p, dict):
            continue
        rows = []
        def collect(layer, path):
            if isinstance(layer, list):
                for i, sub in enumerate(layer):
                    collect(sub, f"{path}[{i}]")
                return
            if not isinstance(layer, dict):
                return
            fl = layer.get('frameList') or []
            lf = {'layer': path, 'label': layer.get('label'), 'frames': []}
            for fr in fl:
                c = fr.get('content') or {}
                lf['frames'].append({
                    'time': fr.get('time'), 'type': fr.get('type'),
                    'icon': c.get('icon'),
                    'angle': resolve(c.get('angle')) if 'angle' in c else None,
                    'opa': resolve(c.get('opa')) if 'opa' in c else None,
                    'coord': c.get('coord'), 'easing': c.get('easing'),
                })
            rows.append(lf)
            for ch in layer.get('children') or []:
                collect(ch, path + '/' + str(ch.get('label') or ch.get('uid') or ''))
        if isinstance(p, dict) and p.get('layer') is not None:
            collect(p['layer'], param)
        fn = f"{group}__{param}.json".replace('/', '_')
        with open(os.path.join(OUT, 'curves', fn), 'w', encoding='utf-8') as f:
            json.dump(rows, f, ensure_ascii=False, indent=1)
        n_frames = sum(len(r['frames']) for r in rows)
        curves_summary.append({'group': group, 'param': param, 'layers': len(rows), 'frames': n_frames})

with open(os.path.join(OUT, 'curves_summary.csv'), 'w', newline='', encoding='utf-8-sig') as f:
    wr = csv.DictWriter(f, fieldnames=['group', 'param', 'layers', 'frames'])
    wr.writeheader(); wr.writerows(curves_summary)

# ---- 3) 层树摘要 ----
def tree(layer, depth=0, lines=None):
    if lines is None: lines = []
    if isinstance(layer, list):
        for i, sub in enumerate(layer):
            tree(sub, depth, lines)
        return lines
    if not isinstance(layer, dict):
        return lines
    fl = layer.get('frameList') or []
    icons = [fr['content']['icon'] for fr in fl if isinstance(fr.get('content'), dict) and fr['content'].get('icon')]
    lines.append('  ' * depth + f"{layer.get('label')} [type={layer.get('type')}] frames={len(fl)}" + (f" icons={icons[:3]}{'...' if len(icons)>3 else ''}" if icons else ''))
    for ch in layer.get('children') or []:
        tree(ch, depth + 1, lines)
    return lines

with open(os.path.join(OUT, 'layer_tree.txt'), 'w', encoding='utf-8') as f:
    for group, g in obj.items():
        if not isinstance(g, dict): continue
        f.write(f"== {group} (type={g.get('type')}) ==\n")
        for root_l in (g.get('motion') or {}).values():
            if isinstance(root_l, dict) and root_l.get('layer'):
                for ln in tree(root_l['layer'], 1):
                    f.write(ln + '\n')

# ---- 4) metadata 概要 ----
md = d['metadata']
meta_out = {
    'blinkParameter': md.get('blinkParameter'),
    'clampControl': md.get('clampControl'),
    'eyeControl': md.get('eyeControl'),
    'mouthControl': md.get('mouthControl'),
    'charaProfile': md.get('charaProfile'),
    'velvetyKeys': sorted(k for k in md if k.startswith('velvety')),
    'velvetyPartsTree_nodes': len(md.get('velvetyPartsTree') or []),
}
with open(os.path.join(OUT, 'metadata_summary.json'), 'w', encoding='utf-8') as f:
    json.dump(meta_out, f, ensure_ascii=False, indent=1)

print(f"parts: {len(parts)} -> {OUT}/parts")
print(f"params with curves: {len(curves_summary)} -> {OUT}/curves")
print(f"atlas: {list(atlas_imgs.keys())}")
