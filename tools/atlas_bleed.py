# -*- coding: utf-8 -*-
"""给导出的图集做边缘外扩（bleed），消除"网格外圈采样到半透明像素 → 白边"。

背景：图集轮廓外那圈像素的 alpha 往往只有 ~55/255（抗锯齿残留），
网格外圈采样到它 → 合成到浅色底显白边。做法是把 RGB+alpha 向透明区
膨胀 N px——不是复制半透明像素（那只会把半透明圈扩大），而是让轮廓外
那圈采用内侧**最不透明邻居**的颜色与 alpha，使网格外圈采样到 ~255。

用法::

    python tools/atlas_bleed.py <texture.png> [--n 1] [--out out.png]

原地处理时会先把原图备份为 <名>.png（覆盖前的原文件），结果写 <名>_bled.png。
"""
from __future__ import annotations

import argparse
import os
import numpy as np
from PIL import Image


def bleed(arr: np.ndarray, n: int = 1) -> np.ndarray:
    """把"最不透明邻居"的 RGBA 向外传播 n 次（alpha 取最大）。"""
    out = arr.copy()
    a = out[..., 3].astype(np.int16)
    dirs = ((0, -1), (0, 1), (-1, 0), (1, 0))
    for _ in range(n):
        best = out.copy()
        best_a = a.copy()
        for dy, dx in dirs:
            src = np.roll(np.roll(out, dy, axis=0), dx, axis=1)
            sa = np.roll(np.roll(a, dy, axis=0), dx, axis=1)
            upd = sa > best_a
            best[upd] = src[upd]
            best_a[upd] = sa[upd]
        upd = (best_a > a) & (a < 255)
        out[upd] = best[upd]
        a = out[..., 3].astype(np.int16)
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('src')
    ap.add_argument('--n', type=int, default=1)
    ap.add_argument('--out', default=None)
    a = ap.parse_args()
    img = Image.open(a.src).convert('RGBA')
    arr = np.asarray(img).copy()
    before_zero = int((arr[..., 3] == 0).sum())
    res = bleed(arr, a.n)
    after_zero = int((res[..., 3] == 0).sum())
    out = a.out or a.src
    if out == a.src:
        out = os.path.splitext(a.src)[0] + '_bled.png'
    Image.fromarray(res).save(out)
    print('%s → %s  N=%d  透明像素 %d → %d（减少 %d）'
          % (os.path.basename(a.src), os.path.basename(out), a.n,
             before_zero, after_zero, before_zero - after_zero))


if __name__ == '__main__':
    main()
