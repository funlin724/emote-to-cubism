# -*- coding: utf-8 -*-
"""幅度缩放轮子：把绑在指定参数上的关键形变向中性姿态收缩（经 PSD2Live MCP）。

用途（PSD2Live 需在运行，MCP 端口 23871）::

  python tools/amp_scale.py --list  --params ParamEyeBallX,ParamEyeBallY
  python tools/amp_scale.py --run   --params ParamEyeBallX,ParamEyeBallY --factor 0.7
  python tools/amp_scale.py --run   --params ParamAngleZ --factor 0.167   # 在已缩放基础上继续缩

行为：
* 枚举 warp / rotation / mesh 三类对象，凡 axes 与 --params 有交集者为目标；
* warp：每格 control_points 向中性格的格点收缩（rest + f*(v-rest)）；
* rotation：angle 向中性角收缩；mesh：position_deltas 向中性格收缩；
* 中性格 = 目标参数全为 0 的那格（跳过不写）；
* 断点续写：进度记 _amp_progress_<params>.json（当前目录；已写格不重写，
  避免二次收缩）；
* 每格写入都有 PSD2Live 历史节点，可 history_checkout 回退；
* 每格写后回读验证（MCP 响应无 error 不代表写入生效）。
"""
from __future__ import annotations
import argparse
import json
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import psd2live_client as P


def coord_key(co, params):
    return tuple(sorted((p, float(co.get(p, 0.0))) for p in params))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--params', required=True, help='逗号分隔的参数 ID')
    ap.add_argument('--factor', type=float, default=None, help='收缩系数（--run 必填）')
    ap.add_argument('--run', action='store_true', help='执行写入')
    ap.add_argument('--list', action='store_true', help='只列出绑定对象，不写')
    a = ap.parse_args()
    params = [p.strip() for p in a.params.split(',') if p.strip()]
    if (not a.list) and (not a.run):
        ap.error('需要 --run 或 --list')
    if a.run and a.factor is None:
        ap.error('--run 需要 --factor')
    prog_name = '_amp_progress_%s.json' % '_'.join(params)

    c = P.Client(); c.initialize()
    prog = {}
    if os.path.exists(prog_name):
        raw = json.load(open(prog_name, encoding='utf-8'))
        prog = {o: {tuple((k, float(v)) for k, v in co) for co in v} for o, v in raw.items()}
        print(f'载入断点 {prog_name}：' + ', '.join(f'{o}×{len(v)}' for o, v in prog.items()))

    objs = json.loads(c.result_text(c.call('rig_list_objects', {})))['objects']
    ids = [(o['kind'], o['id']) for o in objs if o.get('kind') in ('warp', 'rotation', 'mesh')]

    targets = []
    for kind, i in ids:
        try:
            r = json.loads(c.result_text(c.call('object_get', {'target': {'kind': kind, 'id': i}})))
        except Exception as e:
            print(f'  [读取失败] {i}: {e}')
            continue
        g = r.get('geometry') or {}
        axes = [ax['parameterId'] for ax in (g.get('axes') or [])]
        hit = [p for p in params if p in axes]
        if hit:
            targets.append((kind, i, r.get('name'), g, hit))

    print(f'绑定 {"/".join(params)} 的对象 {len(targets)} 个：')
    for kind, i, nm, g, hit in targets:
        print(f'  [{kind}] {i}  name={nm}  命中={hit}  keyforms={g.get("keyformCount")}')
    if a.list:
        return 0

    f = a.factor
    done = fail = 0
    for kind, i, nm, g, hit in targets:
        cells = g.get('cells') or []
        rest = None
        for cell in cells:
            co = cell.get('coordinate') or {}
            if all(abs(co.get(p, 0.0)) < 1e-9 for p in params):
                rest = cell
                break
        if rest is None:
            print(f'  [跳过] {i}: 无中性格')
            continue
        for cell in cells:
            co = cell.get('coordinate') or {}
            key = coord_key(co, params)
            if all(abs(v) < 1e-9 for _, v in key):
                continue
            if key in prog.get(i, set()):
                continue
            if kind == 'rotation':
                ra = rest.get('angle', 0.0)
                geo = {'angle': ra + f * (cell.get('angle', 0.0) - ra),
                       'origin_x': cell.get('originX'), 'origin_y': cell.get('originY')}
            elif kind == 'warp':
                rcp = rest['controlPoints']
                geo = {'control_points': [rv + f * (v - rv)
                                          for v, rv in zip(cell['controlPoints'], rcp)]}
            else:  # mesh
                rd = rest.get('positionDeltas') or rest.get('position_deltas')
                if not rd:
                    print(f'  [跳过] {i} {dict(key)}: mesh 无 positionDeltas（键={list(cell.keys())}）')
                    continue
                geo = {'position_deltas': [rv + f * (v - rv)
                                           for v, rv in zip(cell.get('positionDeltas') or cell.get('position_deltas'), rd)]}
            hh = json.loads(c.result_text(c.call('project_get_state', {})))['historyHeadNodeId']
            rr = c.call('keyform_set', {'target': {'kind': kind, 'id': i}, 'coordinate': co,
                                        'geometry': geo, 'expected_history_head_node_id': hh})
            t = c.result_text(rr)
            if 'rror' in t:
                fail += 1
                print(f'  [失败] {i} {dict(key)}: {t[:140]}', flush=True)
                continue
            # 写后回读验证（静默失败防护：响应无 error 不代表写入成功）
            okv = False
            for attempt in range(3):
                d2 = json.loads(c.result_text(c.call('object_get', {'target': {'kind': kind, 'id': i}})))
                g2 = d2.get('geometry') or {}
                c2match = None
                for c2 in (g2.get('cells') or []):
                    if json.dumps(c2.get('coordinate') or {}, sort_keys=True) == json.dumps(co, sort_keys=True):
                        c2match = c2
                        break
                if c2match is not None:
                    if kind == 'rotation':
                        okv = abs(c2match.get('angle', 1e9) - geo['angle']) < 0.01
                    elif kind == 'warp':
                        cur = c2match.get('controlPoints') or []
                        okv = (len(cur) == len(geo['control_points'])
                               and all(abs(x - y) < 1e-4 for x, y in zip(cur, geo['control_points'])))
                    else:
                        cur = c2match.get('positionDeltas') or []
                        okv = (len(cur) == len(geo['position_deltas'])
                               and all(abs(x - y) < 1e-6 for x, y in zip(cur, geo['position_deltas'])))
                    if okv:
                        break
                time.sleep(1.0)
            if not okv:
                fail += 1
                print(f'  [回读不符] {i} {dict(key)}: 写入未生效（不标记进度，重跑会补）', flush=True)
                continue
            done += 1
            prog.setdefault(i, set()).add(key)
            json.dump({o: sorted(list(s)) for o, s in prog.items()},
                      open(prog_name, 'w', encoding='utf-8'))
            print(f'  [{done}] {i} {dict(key)} ok', flush=True)
    print(f'完成：写入 {done} 格，失败 {fail}')
    return 0


if __name__ == '__main__':
    sys.exit(main())
