# -*- coding: utf-8 -*-
"""老世代 E-mote 条目合并：多部件（タイムラインのみ/体のみ/頭のみ/頭部差分…）
合并为单条目，供 parse_motion / emote_assemble / exact_extract 直接消费。

背景：2015 前后的 MAGES/E-mote 游戏把一套服装拆成多个归档条目，由引擎
运行时拼装；新世代是单条目。合并要点（经可移植性审查实证的配方）：
1. object = 组名并集；metadata = 取键最全的部件；
2. source 纹理改名（tag 后缀）+ icon ID 分段重排（每部件 +1000），
   帧内容 src 改写为 tex#<改名后纹理>，icon 引用同步重排
   ——否则同名 tex/相同起始 icon 会静默互相覆盖；
3. resx 的 ExtraFlattenArrays 并集（键加偏移防撞），无该键则伪造空表；
4. 多根 layer 拼接：参数的 layer 列表非单根时，其余根并入首根 children
   （空壳 dummy 根跳过）——否则 registry 只取第一根、眼/眉/嘴槽位整体丢失。

配置 JSON（必填）::

    {
      "output": "char_merged",
      "parts": [
        {"entry": "char_タイムラインのみ", "tag": ""},
        {"entry": "char_体のみ",  "tag": "b"},
        {"entry": "char_頭のみ",  "tag": "h"},
        {"entry": "char_頭部差分aのみ", "tag": "d"}
      ]
    }

tag 作为纹理名后缀（首部件可为空串）；icon 偏移按部件顺序自动 +1000 递增。
图集 PNG 从各部件的反编译目录复制为合并条目的 <新纹理名>-texture.png。

用法::

    python tools/merge_emote_parts.py --config merge.json
    EMOTE_MOTION_DIR=/path python tools/merge_emote_parts.py --config merge.json
"""
import json
import os
import re
import shutil
import sys
import argparse

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SRC = os.environ.get('EMOTE_MOTION_DIR') or os.path.join(ROOT, 'data', 'motion')

ICON4 = re.compile(r'^\d{4}$')
ICONS_PER_PART = 1000     # 每部件 icon ID 重排步长
FLATKEY_STEP = 100000     # resx flatten 数组键的部件偏移步长


def load_json(path):
    if not os.path.isfile(path):
        return None
    with open(path, 'r', encoding='utf-8-sig') as f:
        return json.load(f)


def rewrite_node(node, map_src, remap_icon, flat_shift):
    """递归改写 object 子树里的 src/icon 引用与 resx flatten 引用。"""
    if isinstance(node, dict):
        s = node.get('src')
        if isinstance(s, str):
            node['src'] = map_src(s)
        ic = node.get('icon')
        if isinstance(ic, str) and ICON4.match(ic):
            node['icon'] = remap_icon(ic)
        for key, v in list(node.items()):
            if isinstance(v, str) and v.startswith('#resource@') \
                    and v[11:].isdigit() and flat_shift:
                node[key] = f'#resource@{int(v[11:]) + flat_shift}'
        for v in node.values():
            rewrite_node(v, map_src, remap_icon, flat_shift)
    elif isinstance(node, list):
        for v in node:
            rewrite_node(v, map_src, remap_icon, flat_shift)


def concat_multi_roots(obj):
    """多根 layer 拼接：其余根并入首根 children（空壳 dummy 根跳过）。"""
    fixed = 0
    for g in obj.values():
        if not (isinstance(g, dict) and isinstance(g.get('motion'), dict)):
            continue
        for p in g['motion'].values():
            if not isinstance(p, dict):
                continue
            lay = p.get('layer')
            if not (isinstance(lay, list) and len(lay) > 1):
                continue
            first = lay[0]
            if not isinstance(first, dict):
                continue
            children = first.get('children') or []
            for extra in lay[1:]:
                if not isinstance(extra, dict):
                    continue
                if not (extra.get('frameList') or extra.get('children')
                        or extra.get('layer')):
                    continue  # 空壳 dummy 根
                children.append(extra)
                fixed += 1
            first['children'] = children
            p['layer'] = [first]
    return fixed


def main():
    ap = argparse.ArgumentParser(description='老世代 E-mote 多部件条目合并')
    ap.add_argument('--config', required=True, help='合并配置 JSON（结构见文件头）')
    ap.add_argument('--out-dir', default=None,
                    help='合并条目输出根目录（默认 EMOTE_MOTION_DIR）')
    a = ap.parse_args()

    cfg = load_json(a.config)
    if not cfg:
        raise SystemExit(f'配置文件不存在: {a.config}')
    out_name = cfg['output']
    parts = cfg['parts']
    if not parts:
        raise SystemExit('parts 为空')
    out_root = a.out_dir or SRC

    merged_obj = {}
    merged_source = {}
    merged_flat = {}
    best_meta, best_meta_n = None, -1
    jsons = []

    for k, part in enumerate(parts):
        entry, tag = part['entry'], part.get('tag', '')
        offset = k * ICONS_PER_PART
        d = load_json(os.path.join(SRC, entry + '.psb.m.json'))
        if d is None:
            raise SystemExit(f'部件 JSON 不存在: {entry}（于 {SRC}）')
        resx = load_json(os.path.join(SRC, entry + '.psb.m.resx.json')) or {}
        flat = (resx.get('ExtraFlattenArrays') or {}) if isinstance(resx, dict) else {}
        jsons.append(d)

        # ---- 纹理改名映射 + icon 重排 ----
        rename = {}
        for tex, t in d.get('source', {}).items():
            if not isinstance(t, dict):
                continue
            new_tex = tex if tag == '' else f'{tex}_{tag}'
            if new_tex in merged_source:
                raise SystemExit(f'纹理名冲突: {new_tex}（部件 {k}）')
            rename[tex] = new_tex
            new_icons = {}
            for iid, ic in (t.get('icon') or {}).items():
                nid = f'{int(iid) + offset:04d}' if ICON4.match(str(iid)) else str(iid)
                if nid in new_icons:
                    raise SystemExit(f'icon 冲突: {new_tex}/{nid}（部件 {k}）')
                new_icons[nid] = ic
            t['icon'] = new_icons
            merged_source[new_tex] = t

        def map_src(s, rename=rename):
            if s in rename:                       # 老世代裸纹理名
                return 'tex#' + rename[s]
            if s.startswith('tex#'):              # 新世代形态：改名即可
                base = s[4:]
                return 'tex#' + rename.get(base, base)
            return s                              # 组引用（motion//组名）等

        def remap_icon(ic, offset=offset):
            return f'{int(ic) + offset:04d}' if ICON4.match(ic) else ic

        # ---- object 并集 + 子树改写 ----
        for gname, g in d.get('object', {}).items():
            if gname not in merged_obj:
                merged_obj[gname] = g
            else:
                # 同名组：motion 参数按参数名拼接 layer（交给多根拼接收口）
                mg = merged_obj[gname]
                if isinstance(g, dict) and isinstance(g.get('motion'), dict):
                    mg.setdefault('motion', {})
                    for pn, p in g['motion'].items():
                        if pn in mg['motion'] and isinstance(p, dict) \
                                and isinstance(mg['motion'][pn], dict):
                            old = mg['motion'][pn].get('layer') or []
                            new = p.get('layer') or []
                            mg['motion'][pn]['layer'] = (
                                old + new if isinstance(old, list) else [new])
                        else:
                            mg['motion'][pn] = p
            rewrite_node(merged_obj[gname], map_src, remap_icon,
                         k * FLATKEY_STEP if flat else 0)

        # ---- metadata：键最全者胜 ----
        meta = d.get('metadata') or {}
        if len(meta) > best_meta_n:
            best_meta, best_meta_n = meta, len(meta)

        # ---- resx flatten 数组：键加偏移并入 ----
        if flat:
            shift = k * FLATKEY_STEP
            for key, val in flat.items():
                nk = key if not key.lstrip('@').isdigit() \
                    else f'@{int(key.lstrip("@")) + shift}'
                merged_flat[nk] = val

    # ---- 多根拼接 ----
    n_roots = concat_multi_roots(merged_obj)

    # ---- 落盘 ----
    out_json_dir = out_root
    out_atlas_dir = os.path.join(out_root, out_name + '.psb.m')
    os.makedirs(out_atlas_dir, exist_ok=True)
    merged = {'object': merged_obj,
              'source': merged_source,
              'metadata': best_meta or {}}
    # 补齐首部件有而 best_meta 缺的顶层键（保守合并浅层键）
    for d in jsons:
        for key, val in (d.get('metadata') or {}).items():
            merged['metadata'].setdefault(key, val)

    with open(os.path.join(out_json_dir, out_name + '.psb.m.json'), 'w',
              encoding='utf-8') as f:
        json.dump(merged, f, ensure_ascii=False, indent=1)
    with open(os.path.join(out_json_dir, out_name + '.psb.m.resx.json'), 'w',
              encoding='utf-8') as f:
        json.dump({'ExtraFlattenArrays': merged_flat}, f, ensure_ascii=False, indent=1)

    # ---- 图集 PNG 复制 ----
    n_png = 0
    for k, part in enumerate(parts):
        entry, tag = part['entry'], part.get('tag', '')
        src_dir = os.path.join(SRC, entry + '.psb.m')
        if not os.path.isdir(src_dir):
            print(f'[warn] 部件图集目录缺失，跳过复制: {src_dir}')
            continue
        d = jsons[k]
        for tex in (d.get('source') or {}):
            if not isinstance(d['source'][tex], dict):
                continue
            new_tex = tex if tag == '' else f'{tex}_{tag}'
            src_png = os.path.join(src_dir, tex + '-texture.png')
            dst_png = os.path.join(out_atlas_dir, new_tex + '-texture.png')
            if os.path.isfile(src_png):
                shutil.copyfile(src_png, dst_png)
                n_png += 1
            else:
                print(f'[warn] 图集缺失: {src_png}')

    n_params = sum(len(g.get('motion', {})) for g in merged_obj.values()
                   if isinstance(g, dict))
    print(f'合并完成: {out_name}  部件={len(parts)} 组={len(merged_obj)} '
          f'参数={n_params} 纹理={len(merged_source)} PNG={n_png} '
          f'多根拼接={n_roots}')
    print(f'JSON → {os.path.join(out_json_dir, out_name + ".psb.m.json")}')
    return 0


if __name__ == '__main__':
    sys.exit(main())
