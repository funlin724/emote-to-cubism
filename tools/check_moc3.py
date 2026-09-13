# -*- coding: utf-8 -*-
"""moc3 读取/结构校验工具。

用法::

    python tools/check_moc3.py <a.moc3> [b.moc3 ...]     # 摘要
    python tools/check_moc3.py --verify <a.moc3>          # 逐段对齐校验
    python tools/check_moc3.py --compare <a.moc3> <b.moc3>  # 逐段数据比对
    python tools/check_moc3.py --glob "out/**/*.moc3"

背景（见 docs/moc3-format-semantics.md）：
* moc3 的计数/画布段位置由 SOT[0]/SOT[1] 给出，**不是固定偏移**；
  Cubism 5.3 导出（版本字节 6）SOT[0]=5824，硬编码 1984 会全读成 0。
* Cubism 各版本 → moc3 版本字节：
  3.0→1, 3.3→2, **4.0→3**, 4.2→4, 5.0→5, 5.3→6。
* 本库 SECTION_LAYOUT 正好等于 **Cubism 4.0（字节 3）** 的段集
  （102 = 2 + 100）；4.2 起每版增加段组，4.2 引入 counts[23]/[24]。
"""
import sys
import os
import glob
import struct

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from moc3lib import Moc3  # noqa: E402
from moc3lib.core import BinaryReader, _get_section_count, _read_section  # noqa: E402

CUBISM_OF_VER = {1: '3.0', 2: '3.3', 3: '4.0', 4: '4.2', 5: '5.0', 6: '5.3'}


def report(path):
    m = Moc3.from_file(path)
    print('%-30s ver=%-2d (Cubism %-4s) ArtMesh=%-4d Param=%-3d Deformer=%-3d '
          'canvas=%.0fx%.0f'
          % (os.path.basename(os.path.dirname(path)) + '/' + os.path.basename(path),
             m.header.version, CUBISM_OF_VER.get(m.header.version, '?'),
             m.counts[4], m.counts[5], m.counts[1],
             m.canvas.canvas_width, m.canvas.canvas_height))
    return m


def verify(path):
    """校验：每段数据长度不越界，且下一段起点 = 对齐后的段尾。"""
    m = Moc3.from_file(path)
    d = open(path, 'rb').read()
    sot = m._sot_offsets
    r = BinaryReader(d)
    ok = pad = bad = unknown = 0
    for i, e in enumerate(m._layout):
        if i + 2 >= len(sot):
            unknown += 1
            continue
        start, nxt = sot[i + 2], sot[i + 3] if i + 3 < len(sot) else len(d)
        if not start or start >= len(d):
            continue
        r.pos = start
        n = _get_section_count(m, e)
        if n:
            _read_section(r, e, n)
        end = r.pos
        if end > nxt:
            bad += 1
            print('  越界 idx=%d %s cnt=%d dataEnd=%d next=%d' % (i, e.name, n, end, nxt))
        elif end == nxt:
            ok += 1
        else:
            pad += 1
    n_used = len([v for v in sot if 0 < v <= len(d)])
    n_expected = 2 + len(m._layout)
    print('%s\n  版本 v%d (Cubism %s)  文件 %d 字节'
          % (path, m.header.version, CUBISM_OF_VER.get(m.header.version, '?'), len(d)))
    print('  SOT 有效条目 %d, 布局段数 %d, 期望 %d  -> %s'
          % (n_used, len(m._layout), n_expected,
             '完全吻合（零未知段）' if n_used == n_expected else '★有 %d 个未知段'
             % (n_used - n_expected)))
    print('  逐段：紧密 %d, 尾对齐 %d, 越界 %d' % (ok, pad, bad))
    return bad == 0 and n_used == n_expected


def compare(pa, pb):
    A, B = Moc3.from_file(pa), Moc3.from_file(pb)
    print('%-48s %-7s %-7s %s' % ('段', os.path.basename(pa), os.path.basename(pb), '一致?'))
    same_all = True
    for k in sorted(set(A._sections) | set(B._sections)):
        a, b = A._sections.get(k), B._sections.get(k)
        if a is None and b is None:
            continue
        same = (a == b)
        same_all &= same
        if not same or (a and len(a)):
            print('%-48s %-7d %-7d %s' % (k, len(a or []), len(b or []),
                                          '一致' if same else '★不同'))
    print('\n整体一致 =', same_all)
    return same_all


def main():
    args = sys.argv[1:]
    if not args:
        print(__doc__)
        return 1
    if args[0] == '--verify':
        return 0 if verify(args[1]) else 2
    if args[0] == '--compare':
        return 0 if compare(args[1], args[2]) else 2
    files = sorted(glob.glob(args[1])) if args[0] == '--glob' else args
    for p in files:
        try:
            report(p)
        except Exception as e:
            print('%-30s ERROR %s: %s' % (p, type(e).__name__, e))
    return 0


if __name__ == '__main__':
    sys.exit(main())
