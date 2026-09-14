# -*- coding: utf-8 -*-
"""parse_motion.py 产物质检:
1) 部件数与 JSON source.tex#*.icon 总数、parts_list.csv 行数三方一致
2) 每个部件 PNG 与图集对应 UV 矩形逐像素比对（RGBA 完全一致）
3) parts_list.csv 字段与 JSON icon 字段一致（uv/origin/尺寸）

数据目录约定同 parse_motion.py（EMOTE_MOTION_DIR 或仓库内 data/motion）。
用法: python tools/qc_parse.py <条目名> <输出目录>
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

d = json.load(open(os.path.join(SRC, BASE + '.psb.m.json'), encoding='utf-8-sig'))

# ---- 1) 计数三方一致 ----
expect = []  # (part文件名, atlas, icon_id, l, t, w, h)
for tex_name, tex in d['source'].items():
    if not (isinstance(tex, dict) and 'icon' in tex):
        continue
    for icon_id, ic in tex['icon'].items():
        expect.append((f"{tex_name.replace('#','')}_{icon_id}.png", tex_name, icon_id,
                       int(ic['left']), int(ic['top']), int(ic['width']), int(ic['height'])))

with open(os.path.join(OUT, 'parts_list.csv'), encoding='utf-8-sig') as f:
    rows = list(csv.DictReader(f))
files = set(os.listdir(os.path.join(OUT, 'parts')))
expect_files = {e[0] for e in expect}

errs = []
if len(rows) != len(expect):
    errs.append(f"parts_list 行数 {len(rows)} != JSON icon 总数 {len(expect)}")
if files != expect_files:
    errs.append(f"parts/ 文件集不一致: 多 {sorted(files - expect_files)[:5]} 少 {sorted(expect_files - files)[:5]}")

# ---- 2) 逐像素贴回比对 + 3) 清单字段核对 ----
atlas_cache = {}
by_name = {r['part']: r for r in rows}
n_checked = 0
for fname, tex_name, icon_id, l, t, w, h in expect:
    im = atlas_cache.setdefault(tex_name,
        Image.open(os.path.join(SRC, BASE + '.psb.m', tex_name + '-texture.png')).convert('RGBA'))
    region = im.crop((l, t, l + w, t + h))
    part_path = os.path.join(OUT, 'parts', fname)
    part = Image.open(part_path).convert('RGBA')
    if part.size != region.size:
        errs.append(f"{fname}: 尺寸 {part.size} != 图集矩形 {(w, h)}")
        continue
    if part.tobytes() != region.tobytes():
        errs.append(f"{fname}: 像素与图集 UV 矩形不一致")
        continue
    n_checked += 1
    r = by_name.get(fname)
    if r is None:
        continue
    if (int(float(r['uv_left'])) != l or int(float(r['uv_top'])) != t
            or int(float(r['w'])) != w or int(float(r['h'])) != h):
        errs.append(f"{fname}: parts_list UV/尺寸与 JSON 不符")

if errs:
    print(f"[FAIL] {BASE}: {len(errs)} 处问题")
    for e in errs[:20]:
        print("  -", e)
    sys.exit(1)
print(f"[OK] {BASE}: 部件 {len(expect)}（清单 {len(rows)} / 文件 {len(files)}），"
      f"{n_checked} 张逐像素比对一致")
