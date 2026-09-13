# -*- coding: utf-8 -*-
"""多外观（差分/换装）合并 PSD 生成器。

把同一角色的多个外观条目（基型 + 换装差分）装配到统一画布，去重后生成
单个分层 PSD + 「层→外观成员」表（membership.json），供在 Cubism Editor
内以图层开关实现换装差分。

数据来源：tools/emote_assemble.py 的 Assembler（根参数 タイムライン構造，
适配层说明见 docs/emote-to-cubism-method.md）。

外观清单配置（必填，JSON 文件）::

    {
      "variants": [["a", "char_a"], ["b", "char_b"], ["ae", "char_a_military"]],
      "silhouette": {                      // 可选：每套的剪影件（不生成图层）
        "a": ["0137", "0138"], ...
      },
      "canvas": {"minx": -1112, "maxx": 927, "miny": -567, "maxy": 4433}
    }

剪影件与摆动代理平面的判别经验（实测）：
* 跨度阈值启发式（x>800 或 y>1500 判为「追加パーツ」摆动代理）会误杀
  腿部/靴子/衣摆等**可见美术**——默认只排除显式剪影清单（灰色影膜，
  渲染验证为灰白人体影）；需要复现字面阈值规则时传 --exclude-proxies。
* opa=0 的状态件照常生成图层，opacity 设为 0。

用法::

    python tools/build_psd.py --variants variants.json
    python tools/build_psd.py --variants variants.json --scale 0.5
    python tools/build_psd.py --variants variants.json --split   # 分套输出
    python tools/build_psd.py --variants variants.json --out merged.psd
"""
import os
import sys
import json
import argparse
import hashlib

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import emote_assemble as EA  # noqa: E402

from PIL import Image  # noqa: E402
from psd_tools import PSDImage  # noqa: E402

ROOT_PARAM = 'タイムライン構造'

# 摆动代理平面跨度阈值（仅 --exclude-proxies 时使用）
PROXY_XSPAN = 800
PROXY_YSPAN = 1500
PROXY_PATH_TOKEN = '追加パーツ'   # path 末段含此记号才可能被判为代理平面


def load_config(path):
    with open(path, 'r', encoding='utf-8') as f:
        cfg = json.load(f)
    variants = [tuple(v) for v in cfg['variants']]
    silhouette = {k: set(v) for k, v in (cfg.get('silhouette') or {}).items()}
    canvas = cfg.get('canvas') or {}
    return variants, silhouette, canvas


def classify_exclusion(short, inst, bb, silhouette, exclude_proxies=False):
    """返回排除原因元组 (类型, 说明) 或 None。

    显式剪影清单（灰色影膜）始终排除；
    exclude_proxies=True 时额外应用跨度阈值启发式。
    """
    icon = inst['icon']
    if icon in silhouette.get(short, set()):
        return ('silhouette', icon)
    if exclude_proxies:
        last = inst['path'].rsplit('/', 1)[-1]
        if PROXY_PATH_TOKEN in last:
            xspan = bb[2] - bb[0]
            yspan = bb[3] - bb[1]
            if xspan > PROXY_XSPAN or yspan > PROXY_YSPAN:
                return ('proxy_plane', '%s span=(%.0f,%.0f)' % (last, xspan, yspan))
    return None


def proxy_candidate(short, inst, bb, silhouette):
    """本会被阈值判为代理、但按实测判定为可见美术而保留的件。"""
    if inst['icon'] in silhouette.get(short, set()):
        return None
    last = inst['path'].rsplit('/', 1)[-1]
    if PROXY_PATH_TOKEN in last:
        xspan = bb[2] - bb[0]
        yspan = bb[3] - bb[1]
        if xspan > PROXY_XSPAN or yspan > PROXY_YSPAN:
            return '%s span=(%.0f,%.0f)' % (last, xspan, yspan)
    return None


def load_atlas(set_name, tex):
    """按条目打开正确图集 PNG。"""
    path = os.path.join(EA.SRC, set_name + '.psb.m', tex + '-texture.png')
    return Image.open(path).convert('RGBA')


def squeeze(im):
    """去重内存优化：PIL 图像转 PNG bytes（保留 alpha）。"""
    import io
    buf = io.BytesIO()
    im.save(buf, format='PNG')
    return buf.getvalue()


def unsqueeze(raw):
    import io
    return Image.open(io.BytesIO(raw)).convert('RGBA')


def collect(variants, silhouette, canvas, scale, exclude_proxies=False, verbose=True):
    """遍历各套，返回 (layers, stats, exclusions, kept_proxy)。

    layers: list of dict(img=..., name=..., top=, left=, order=, set_idx=, idx=, opa=)
    """
    cminx = canvas.get('minx', 0)
    cminy = canvas.get('miny', 0)
    exclusions = []          # (short, icon, path, reason, info)
    kept_proxy = []          # (short, icon, bb, info)  ---- 保留的大尺寸可见件
    stats = {}               # short -> dict(instances, excluded, opa0, kept)
    seen = {}                # dedup_key -> (short, icon)
    layers = []

    for set_idx, (short, name) in enumerate(variants):
        asm = EA.Assembler(name)
        insts = asm.assemble(root_param=ROOT_PARAM)
        atl_cache = {}
        n_excl = 0
        n_opa0 = 0
        n_kept = 0
        n_dedup = 0
        for idx, inst in enumerate(insts):
            positions, _uvs, _idx = asm.mesh_data(inst)
            xs = positions[0::2]
            ys = positions[1::2]
            if not xs or not ys:
                exclusions.append((short, inst['icon'], inst['path'], 'empty_mesh', ''))
                n_excl += 1
                continue
            bb = (min(xs), min(ys), max(xs), max(ys))
            pc = proxy_candidate(short, inst, bb, silhouette)
            if pc is not None:
                kept_proxy.append((short, inst['icon'], bb, pc))
            reason = classify_exclusion(short, inst, bb, silhouette, exclude_proxies)
            if reason is not None:
                exclusions.append((short, inst['icon'], inst['path'], reason[0], reason[1]))
                n_excl += 1
                continue
            if inst['opa'] == 0:
                n_opa0 += 1

            ic = inst['ic']
            left = int(round(float(ic['left'])))
            top = int(round(float(ic['top'])))
            w = int(round(float(ic['width'])))
            h = int(round(float(ic['height'])))
            ox = int(round(float(ic['originX'])))
            oy = int(round(float(ic['originY'])))

            atlas = atl_cache.get(inst['tex'])
            if atlas is None:
                atlas = load_atlas(name, inst['tex'])
                atl_cache[inst['tex']] = atlas
            # 裁剪（不用 inset）用于内容指纹
            rect = (left, top, left + max(1, w), top + max(1, h))
            fp_img = atlas.crop(rect)
            if fp_img.mode != 'RGBA':
                fp_img = fp_img.convert('RGBA')
            digest = hashlib.md5(fp_img.tobytes()).hexdigest()
            fingerprint = (inst['tex'], w, h, ox, oy, digest)
            bb_q = (int(round(bb[0])), int(round(bb[1])),
                    int(round(bb[2])), int(round(bb[3])))
            dedup_key = (fingerprint, bb_q)
            if dedup_key in seen:
                # 跨套/同性去重：记录该层还被哪些外观需要（成员表，供差分切换）
                rec_seen = seen[dedup_key]
                if short not in rec_seen['members']:
                    rec_seen['members'].append(short)
                rec_seen['aliases'].append((short, inst['icon']))
                n_dedup += 1
                continue
            seen[dedup_key] = {'owner': (short, inst['icon']),
                               'members': [short],
                               'aliases': [(short, inst['icon'])]}

            # 图层图像：UV 裁剪内缩 1px，避免相邻图块黑边
            if w > 2 and h > 2:
                box = (left + 1, top + 1, left + w - 1, top + h - 1)
            else:
                box = rect
            src = atlas.crop(box)
            tw = max(1, int(round((bb[2] - bb[0]) * scale)))
            th = max(1, int(round((bb[3] - bb[1]) * scale)))
            if src.size != (tw, th):
                src = src.resize((tw, th), Image.LANCZOS)
            px = int(round((bb[0] - cminx) * scale))
            py = int(round((bb[1] - cminy) * scale))
            layers.append({
                'raw': squeeze(src),
                'name': 'p_%s_%s' % (short, inst['icon']),
                'top': py, 'left': px,
                'opa': inst['opa'],
                'order': inst['order'],
                # zorder 主序（定版：纯树序会后发盖脸；zorder 是组内局部序，
                # 但作为全局主键 + order 次序 = 与 moc3 一致的渲染序）
                'zorder': (inst.get('ic', {}).get('metadata') or {}).get('zorder') or 0,
                'set_idx': set_idx, 'idx': idx,
                'members': seen[dedup_key]['members'],
                'aliases': seen[dedup_key]['aliases'],
            })
            n_kept += 1

        atl_cache.clear()
        stats[short] = {'instances': len(insts), 'excluded': n_excl,
                        'opa0': n_opa0, 'kept': n_kept, 'dedup': n_dedup}
        if verbose:
            print('[%s] instances=%d excluded=%d opa0=%d kept=%d dedup_skip=%d'
                  % (short, len(insts), n_excl, n_opa0, n_kept, n_dedup))

    # 图层顺序：zorder 主序 + order 次序（=moc3 渲染序；纯 order 会后发盖脸）
    layers.sort(key=lambda r: (r['zorder'], r['order'], r['set_idx'], r['idx']))
    return layers, stats, exclusions, kept_proxy


def build_psd(layers, size, out_path, verbose=True):
    width, height = size
    psd = PSDImage.new('RGBA', (width, height), color=(0, 0, 0, 0))
    for n, rec in enumerate(layers):
        img = unsqueeze(rec['raw'])
        left, top = rec['left'], rec['top']
        # 越界保护
        if left < 0:
            img = img.crop((-left, 0, img.width, img.height))
            left = 0
        if top < 0:
            img = img.crop((0, -top, img.width, img.height))
            top = 0
        if left + img.width > width:
            img = img.crop((0, 0, width - left, img.height))
        if top + img.height > height:
            img = img.crop((0, 0, img.width, height - top))
        opa = int(round(max(0.0, min(1.0, rec['opa'])) * 255))
        psd.create_pixel_layer(img, name=rec['name'], top=top, left=left,
                               opacity=opa)
        rec['raw'] = None            # 释放内存
        if verbose and (n + 1) % 25 == 0:
            print('  ... built %d/%d layers' % (n + 1, len(layers)))
    os.makedirs(os.path.dirname(os.path.abspath(out_path)), exist_ok=True)
    psd.save(out_path)
    return out_path


def report_exclusions(exclusions, kept_proxy):
    print('\n=== 排除件清单 (%d) ===' % len(exclusions))
    for short, icon, path, reason, info in exclusions:
        last = path.rsplit('/', 1)[-1]
        print('  [%s] %s  %-24s  %-12s %s' % (short, icon, last, reason, info))
    if kept_proxy:
        print('\n=== 阈值候选但经实测保留的可见件 (%d) ===' % len(kept_proxy))
        for short, icon, bb, info in kept_proxy:
            print('  [%s] %s  bbox=(%.0f,%.0f,%.0f,%.0f)  %s'
                  % (short, icon, bb[0], bb[1], bb[2], bb[3], info))


def write_membership(layers, size, scale, out_path, variants):
    """导出「层 → 外观成员」表，供 Editor/MCP 做差分切换。

    members 表示该层被哪些外观需要（去重后一层可能同时属于多个外观）；
    外观切换时必须按 members 做并集，不能按层名前缀粗暴开关，
    否则跨外观共享层会被误关掉。
    """
    data = {
        'psd_size': list(size),
        'scale': scale,
        'variants': {s: n for s, n in variants},
        'layers': [
            {'name': r['name'],
             'members': sorted(r['members']),
             'aliases': [{'variant': s, 'icon': i} for s, i in r['aliases']],
             'zorder': r['zorder'], 'order': r['order'],
             'opa': round(float(r['opa']), 4)}
            for r in layers
        ],
    }
    with open(out_path, 'w', encoding='utf-8') as f:
        json.dump(data, f, ensure_ascii=False, indent=1)
    shared = sum(1 for r in layers if len(r['members']) > 1)
    print('wrote %s  layers=%d  跨套共享层=%d' % (out_path, len(layers), shared))
    return out_path


def main():
    ap = argparse.ArgumentParser(description='多外观合并 PSD 生成器')
    ap.add_argument('--variants', required=True,
                    help='外观清单配置 JSON（结构见文件头注释）')
    ap.add_argument('--scale', type=float, default=None,
                    help='缩放（默认 min(1.0, 4096/画布高)）')
    ap.add_argument('--out', default='merged.psd', help='合并 PSD 输出路径')
    ap.add_argument('--split', action='store_true', help='改为分套输出多个 PSD')
    ap.add_argument('--split-dir', default='.', help='分套输出目录')
    ap.add_argument('--membership-out', default=None,
                    help='层→外观成员表 JSON（默认与 --out 同名 .membership.json）')
    ap.add_argument('--exclude-proxies', action='store_true',
                    help='额外按跨度阈值排除「追加パーツ」大件'
                         '（实测会丢失可见美术，非必要勿开）')
    args = ap.parse_args()

    variants, silhouette, canvas = load_config(args.variants)
    full_w = canvas.get('maxx', 0) - canvas.get('minx', 0)
    full_h = canvas.get('maxy', 0) - canvas.get('miny', 0)
    scale = args.scale if args.scale else min(1.0, 4096.0 / full_h)
    W = int(full_w * scale)
    H = int(full_h * scale)
    print('scale=%.4f  PSD size=%dx%d  exclude_proxies=%s'
          % (scale, W, H, args.exclude_proxies))

    layers, stats, exclusions, kept_proxy = collect(
        variants, silhouette, canvas, scale, exclude_proxies=args.exclude_proxies)
    report_exclusions(exclusions, kept_proxy)

    print('\n=== 统计 ===')
    total_inst = sum(s['instances'] for s in stats.values())
    total_excl = sum(s['excluded'] for s in stats.values())
    total_opa0 = sum(s['opa0'] for s in stats.values())
    total_dedup = sum(s['dedup'] for s in stats.values())
    print('%d 套实例总数 = %d' % (len(variants), total_inst))
    print('排除件总数   = %d' % total_excl)
    print('去重后图层数 = %d  (opa=0 状态件 %d, 跨套/同性去重跳过 %d)'
          % (len(layers), total_opa0, total_dedup))

    if args.split:
        # 分套：每套独立统一坐标，各自生成
        for set_idx, (short, name) in enumerate(variants):
            sub = [r for r in layers if r['set_idx'] == set_idx]
            out = os.path.join(args.split_dir, '%s_%s.psd' % ('variant', short))
            build_psd(sub, (W, H), out)
            print('wrote %s  layers=%d  size=%.1fMB'
                  % (out, len(sub), os.path.getsize(out) / 1e6))
    else:
        build_psd(layers, (W, H), args.out)
        print('wrote %s  layers=%d  size=%.1fMB'
              % (args.out, len(layers), os.path.getsize(args.out) / 1e6))

    mout = args.membership_out or (os.path.splitext(args.out)[0]
                                   + '.membership.json')
    write_membership(layers, (W, H), scale, mout, variants)


if __name__ == '__main__':
    main()
