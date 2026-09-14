# -*- coding: utf-8 -*-
"""打包器对位失配审计。

旧打包器：图层内容 = 图标**整矩形**裁剪，拉伸到**全网格**世界包围盒。
失配 = 包围盒纵横比 ÷ 矩形纵横比 − 1。口/目影/add_mask 等参数形变件的
网格只覆盖矩形一部分（默认态=闭合挤压态），张开态美术被压扁烤进 PSD。

新方案（UV 足迹静态放置，emote_assemble.Assembler.footprint）：
内容 = 网格 UV 足迹（可见三角形实际采样的矩形子区域）1:1 裁剪，
放置 = 足迹顶点（in_rect）的世界包围盒。

本脚本对一份外观清单复算两种口径的失配表（不含写 PSD），
输出：命令行汇总 + CSV 逐件表。失配 ≤5% 视为对齐。

外观清单 JSON 结构同 build_psd.py（variants 必填，silhouette/canvas 可选）。

用法::
    python tools/packer_misfit.py --variants variants.json
    python tools/packer_misfit.py --variants variants.json --csv misfit.csv
"""
import os
import sys
import csv
import hashlib
import argparse

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import emote_assemble as EA          # noqa: E402

ROOT_PARAM = 'タイムライン構造'
# 与 build_psd.py 一致的代理平面启发式（仅统计口径，不影响审计结论）
PROXY_PATH_TOKEN = '追加パーツ'
PROXY_XSPAN = 800
PROXY_YSPAN = 1500


def load_variants(path):
    import json
    with open(path, 'r', encoding='utf-8') as f:
        cfg = json.load(f)
    return [tuple(v) for v in cfg['variants']], cfg.get('silhouette') or {}


def collect_layer_geoms(entries, silhouette):
    """镜像 build_psd.collect 的保留逻辑（不裁图不写盘），
    返回去重后的逐层几何记录。"""
    per = {}
    for entry in entries:
        asm = EA.Assembler(entry, emit_frames=True)
        insts = asm.assemble(root_param=ROOT_PARAM)
        per[entry] = (asm, insts)
    seen = set()
    rows = []
    for short, entry in entries:
        asm, insts = per[entry]
        for inst in insts:
            fp = asm.footprint(inst)
            bb = fp['legacy_bbox'] if fp['skirt'] else fp['bbox']
            if not bb:
                continue
            last = inst['path'].rsplit('/', 1)[-1]
            if (PROXY_PATH_TOKEN in last
                    and ((bb[2] - bb[0]) > PROXY_XSPAN or (bb[3] - bb[1]) > PROXY_YSPAN)):
                continue
            if inst['icon'] in silhouette.get(short, set()):
                continue
            ic = inst['ic']
            w = float(ic['width'])
            h = float(ic['height'])
            # 内容指纹（无图集的近似：条目+纹理+图标）
            digest = hashlib.md5(('%s|%s|%s' % (entry, inst['tex'],
                                                inst['icon'])).encode()).hexdigest()[:12]
            key = ((inst['tex'], round(w), round(h)),
                   tuple(int(round(v)) for v in bb), digest)
            if key in seen:
                continue
            seen.add(key)
            cr = fp['crop']
            fb = fp['bbox']
            lb = fp['legacy_bbox']
            ra = w / h
            ba = (lb[2] - lb[0]) / max(1e-6, lb[3] - lb[1])
            fa = (cr[2] - cr[0]) / max(1e-6, cr[3] - cr[1])
            fba = (fb[2] - fb[0]) / max(1e-6, fb[3] - fb[1])
            rows.append({
                'short': short, 'icon': inst['icon'],
                'name': 'p_%s_%s' % (short, inst['icon']),
                'state': inst.get('state', 0),
                'group': inst.get('group'),
                'rect_wh': (round(w), round(h)),
                'crop_wh': (round(cr[2] - cr[0], 1), round(cr[3] - cr[1], 1)),
                'legacy_bbox_wh': (round(lb[2] - lb[0], 1), round(lb[3] - lb[1], 1)),
                'fp_bbox_wh': (round(fb[2] - fb[0], 1), round(fb[3] - fb[1], 1)),
                'old_misfit': abs(ba / ra - 1.0),
                'new_misfit': abs(fba / fa - 1.0),
                'crop_shrinks': (cr[2] - cr[0]) * (cr[3] - cr[1]) < w * h - 0.5,
            })
    return rows


def main():
    ap = argparse.ArgumentParser(description='打包器对位失配审计')
    ap.add_argument('--variants', required=True, help='外观清单配置 JSON')
    ap.add_argument('--csv', default=None, help='逐件表输出路径')
    ap.add_argument('--top', type=int, default=15)
    a = ap.parse_args()
    entries, silhouette = load_variants(a.variants)
    rows = collect_layer_geoms(entries, silhouette)
    n = len(rows)
    old_bad5 = [r for r in rows if r['old_misfit'] > 0.05]
    old_bad20 = [r for r in rows if r['old_misfit'] > 0.20]
    new_bad5 = [r for r in rows if r['new_misfit'] > 0.05]
    shrunk = [r for r in rows if r['crop_shrinks']]
    print('清单 %d 套：去重后 %d 层' % (len(entries), n))
    print('  旧口径（整矩形→全网格bbox）失配>5%%: %d（%.0f%%）  >20%%: %d'
          % (len(old_bad5), len(old_bad5) / n * 100, len(old_bad20)))
    print('  新口径（UV足迹裁剪→足迹bbox）失配>5%%: %d' % len(new_bad5))
    print('  足迹<整矩形（内容被收窄）的层: %d' % len(shrunk))
    print('\n旧口径最差 %d 件：' % a.top)
    for r in sorted(rows, key=lambda r: -r['old_misfit'])[:a.top]:
        print('  [%s] %s %-28s rect%s bbox%s 失配 %+.0f%%  → 新 %+.0f%% 足迹%s'
              % (r['short'], r['icon'], r['name'][:28], r['rect_wh'],
                 r['legacy_bbox_wh'], r['old_misfit'] * 100,
                 r['new_misfit'] * 100, r['crop_wh']))
    if new_bad5:
        print('\n新口径仍失配 >5%% 的件：')
        for r in sorted(new_bad5, key=lambda r: -r['new_misfit']):
            print('  [%s] %s %-28s 失配 %.0f%%' % (r['short'], r['icon'],
                                                   r['name'][:28],
                                                   r['new_misfit'] * 100))
    if a.csv:
        with open(a.csv, 'w', encoding='utf-8-sig', newline='') as f:
            wtr = csv.writer(f)
            wtr.writerow(['short', 'icon', 'name', 'state', 'group',
                          'rect_w', 'rect_h', 'crop_w', 'crop_h',
                          'legacy_bbox_w', 'legacy_bbox_h',
                          'fp_bbox_w', 'fp_bbox_h',
                          'old_misfit', 'new_misfit', 'crop_shrinks'])
            for r in rows:
                wtr.writerow([r['short'], r['icon'], r['name'], r['state'],
                              r['group'] or '',
                              r['rect_wh'][0], r['rect_wh'][1],
                              r['crop_wh'][0], r['crop_wh'][1],
                              r['legacy_bbox_wh'][0], r['legacy_bbox_wh'][1],
                              r['fp_bbox_wh'][0], r['fp_bbox_wh'][1],
                              round(r['old_misfit'], 4),
                              round(r['new_misfit'], 4),
                              r['crop_shrinks']])
        print('\nCSV → %s' % a.csv)
    return 0


if __name__ == '__main__':
    sys.exit(main())
