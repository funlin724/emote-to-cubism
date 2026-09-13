# -*- coding: utf-8 -*-
"""解析 moc3 文件，dump 全部关键段（用于与官方工具导出的真值逐字段对照）。"""
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__))))
from moc3lib import Moc3, CountIdx

def dump(path):
    m = Moc3.from_file(path)
    print(f'===== {os.path.basename(path)} =====')
    print('header version byte =', m.header.version)
    c = m.counts
    print('counts:', {k: c[v] for k, v in [
        ('PARTS',0),('DEFORMERS',1),('WARP',2),('ROT',3),('MESH',4),('PARAM',5),
        ('PART_KF',6),('WARP_KF',7),('ROT_KF',8),('MESH_KF',9),('KF_POS',10),
        ('KF_BIND_IDX',11),('KF_BAND',12),('KF_BINDINGS',13),('KEYS',14),
        ('UVS',15),('POS_IDX',16),('MASKS',17),('DO_GROUP',18),('DO_GROUPOBJ',19),
        ('GLUE',20),('GLUE_INFO',21),('GLUE_KF',22)]})
    cv = m.canvas
    print(f'canvas: ppu={cv.pixels_per_unit} origin=({cv.origin_x},{cv.origin_y}) wh=({cv.canvas_width},{cv.canvas_height}) flag={cv.canvas_flag}')
    S = m._sections
    def sec(name):
        return S.get(name)
    for name in sorted(S.keys()):
        v = S[name]
        if not v:
            continue
        if name.endswith('ids'):
            print(f'{name}: {v}')
        elif len(v) <= 12:
            print(f'{name}: {[round(x,4) if isinstance(x,float) else x for x in v]}')
        else:
            print(f'{name}: len={len(v)} head={[round(x,3) if isinstance(x,float) else x for x in v[:8]]} tail={[round(x,3) if isinstance(x,float) else x for x in v[-4:]]}')
    # keyform 池关键区间
    kp = S.get('keyform_position.xys') or []
    if kp:
        print(f'keyform_position.xys (all, {len(kp)} floats):')
        print(' ', [round(x,4) for x in kp])
    print()

for p in sys.argv[1:]:
    try:
        dump(p)
    except Exception as e:
        print(f'{p}: PARSE FAIL: {e}')
        import traceback; traceback.print_exc()
