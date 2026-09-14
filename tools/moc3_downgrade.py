# -*- coding: utf-8 -*-
"""把 PSD2Live 导出的 moc3（v5/字节5）重序列化为 moc3 4.0（字节3）。

用途：PSD2Live 0.6.0 固定输出 MOC5（无开关），而旧 Cubism Core 最高只认 ver 4。
若产物未使用 MOC5 独有段（blendShapes 为空、无 offscreen 段——本管线默认如此），
"按标准布局重写 + 版本字节改 3"是无损的。**重写后务必在目标运行时加载验证**。

用法::

    python tools/moc3_downgrade.py <in.moc3> <out.moc3> [--version 3]
"""
from __future__ import annotations

import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from moc3lib import Moc3  # noqa: E402


def downgrade(src: str, dst: str, version: int = 3):
    m = Moc3.from_file(src)
    old = m.header.version
    m.header.version = version
    m.to_file(dst)
    print('%s  v%d → v%d  ArtMesh=%d Param=%d Deformer=%d'
          % (os.path.basename(dst), old, version,
             m.counts[4], m.counts[5], m.counts[1]))
    # 回读自检
    chk = Moc3.from_file(dst)
    if chk.header.version != version:
        raise RuntimeError('回读版本不符')
    if chk.counts[4] != m.counts[4]:
        raise RuntimeError('回读 ArtMesh 数不符')
    print('  回读自检 OK（v%d, ArtMesh=%d）' % (chk.header.version, chk.counts[4]))
    return chk


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('src')
    ap.add_argument('dst')
    ap.add_argument('--version', type=int, default=3,
                    help='目标 moc3 版本字节（默认 3 = Cubism 4.0）')
    a = ap.parse_args()
    downgrade(a.src, a.dst, a.version)
    return 0


if __name__ == '__main__':
    sys.exit(main())
