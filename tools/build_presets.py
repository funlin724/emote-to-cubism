# -*- coding: utf-8 -*-
"""生成待机/眨眼动作预设、表情 exp3、以及挂载它们的 model3.json。

官方公开 JSON 格式（motion3/exp3/model3），直接生成，不依赖 moc3 内部。
幅度取值是保守默认值，可按目标模型参数范围自行调整（改脚本内表即可）。

用法::

    python tools/build_presets.py <交付目录> <moc3基础名> [--copy-nod-shake <来源目录> <来源基础名>]

* blink: 1.2s 单次快眨（1→0→1），与眼皮阶梯的连续插值兼容
* idle:  6s 循环，呼吸(Breath)+身体/头部微摆
* exp3:  smile + 两套服装切换（名称按需改）
"""
from __future__ import annotations
import json, os, shutil, sys


def motion(name, duration, loop, curves):
    segs = 0; pts = 0
    for _, seg in curves:
        segs += (len(seg) - 1) // 3
        pts += (len(seg) - 1) // 3 + 1
    return {"Version": 3,
            "Meta": {"Duration": duration, "Fps": 30.0, "Loop": loop,
                     "AreBeziersRestricted": True, "CurveCount": len(curves),
                     "TotalSegmentCount": segs, "TotalPointCount": pts,
                     "UserDataCount": 0, "TotalUserDataSize": 0},
            "Curves": [{"Target": "Parameter", "Id": pid, "Segments": seg}
                       for pid, seg in curves]}


def seg_linear(points):
    out = []
    for i, (x, y) in enumerate(points):
        if i == 0:
            out += [x, y]
        else:
            out += [0, x, y]
    return out


def seg_bezier(points):
    """points: [(t,v)...] 关键帧，两两之间用贝塞尔（控制点取 1/3、2/3 线性位）"""
    out = [points[0][0], points[0][1]]
    for (t0, v0), (t1, v1) in zip(points, points[1:]):
        dt = t1 - t0
        c1 = (t0 + dt / 3, v0 + (v1 - v0) / 3)
        c2 = (t0 + dt * 2 / 3, v0 + (v1 - v0) * 2 / 3)
        out += [1, c1[0], c1[1], c2[0], c2[1], t1, v1]
    return out


def main():
    out_dir = sys.argv[1]
    base = sys.argv[2] if len(sys.argv) > 2 else 'model'
    copy_src = copy_base = None
    if '--copy-nod-shake' in sys.argv:
        i = sys.argv.index('--copy-nod-shake')
        copy_src = sys.argv[i + 1]
        copy_base = sys.argv[i + 2] if len(sys.argv) > i + 2 else None
    os.makedirs(out_dir, exist_ok=True)

    # ---- 眨眼：闭 0.10s、睁到 0.24s，末尾回 1（非循环，播放器会反复触发）----
    blink = motion('blink', 1.2, False, [
        ('ParamEyeLOpen', seg_linear([(0, 1), (0.10, 0), (0.24, 1), (1.2, 1)])),
        ('ParamEyeROpen', seg_linear([(0, 1), (0.10, 0), (0.24, 1), (1.2, 1)])),
    ])
    # ---- 待机：6s 循环，呼吸 + 微摆 ----
    idle = motion('idle', 6.0, True, [
        ('ParamBreath',     seg_bezier([(0, 0), (1.5, 0.9), (3.0, 0.1), (4.5, 0.9), (6, 0)])),
        ('ParamBodyAngleZ', seg_bezier([(0, 0.4), (3, -0.4), (6, 0.4)])),
        ('ParamAngleX',     seg_bezier([(0, -1.2), (3, 1.2), (6, -1.2)])),
        ('ParamAngleY',     seg_bezier([(0, 0.8), (3, -0.8), (6, 0.8)])),
    ])
    json.dump(blink, open(os.path.join(out_dir, f'{base}.blink.motion3.json'), 'w', encoding='utf-8'),
              ensure_ascii=False, indent=1)
    json.dump(idle, open(os.path.join(out_dir, f'{base}.idle.motion3.json'), 'w', encoding='utf-8'),
              ensure_ascii=False, indent=1)
    print('OK blink / idle 动作')

    # ---- 表情 exp3（名称按目标模型自定义；服装切换用 Appearance 门控时参考）----
    exprs = {
        'exp_smile': [('ParamEyeLSmile', 1.0), ('ParamEyeRSmile', 1.0), ('ParamMouthForm', 1.0)],
        'costume_1': [('ParamAppearance', 1.0)],
        'costume_2': [('ParamAppearance', 0.0)],
    }
    for nm, pairs in exprs.items():
        d = {"Type": "Exp3", "Parameters": [{"Id": p, "Value": v} for p, v in pairs]}
        json.dump(d, open(os.path.join(out_dir, f'{base}.{nm}.exp3.json'), 'w', encoding='utf-8'),
                  ensure_ascii=False, indent=1)
    print('OK 表情 exp3 ×', len(exprs))

    # ---- 复用已有点头/摇头动作（可选）----
    extra = []
    if copy_src:
        src_base = copy_base or 'model'
        for nm in ('nod', 'shake'):
            src = os.path.join(copy_src, f'{src_base}.{nm}.motion3.json')
            dst = os.path.join(out_dir, f'{base}.{nm}.motion3.json')
            if os.path.exists(src):
                shutil.copy(src, dst); extra.append(nm)
        print('OK 复用 nod/shake ×', len(extra))

    # ---- model3.json ----
    motions = {
        'Idle': [{"File": f'{base}.idle.motion3.json'}],
        'Blink': [{"File": f'{base}.blink.motion3.json'}],
    }
    if extra:
        for nm in extra:
            motions[nm.capitalize()] = [{"File": f'{base}.{nm}.motion3.json'}]
    m3 = {"Version": 3,
          "FileReferences": {
              "Moc": f'{base}.moc3',
              "Textures": [f'{base}.4096/texture_00.png'],
              "Physics": f'{base}.physics3.json',
              "DisplayInfo": f'{base}.cdi3.json',
              "Motions": motions,
              "Expressions": [{"Name": k.split('_', 1)[1], "File": f'{base}.{k}.exp3.json'}
                              for k in exprs],
          },
          "Groups": [
              {"Target": "Parameter", "Name": "EyeBlink", "Ids": ["ParamEyeLOpen", "ParamEyeROpen"]},
              {"Target": "Parameter", "Name": "LipSync", "Ids": ["ParamMouthOpenY"]},
          ],
          "HitAreas": [
              {"Id": "ArtMeshFace", "Name": "Head"}
          ]}
    json.dump(m3, open(os.path.join(out_dir, f'{base}.model3.json'), 'w', encoding='utf-8'),
              ensure_ascii=False, indent=1)
    print('OK model3.json（挂载动作+表情+EyeBlink/LipSync 组）')


if __name__ == '__main__':
    main()
