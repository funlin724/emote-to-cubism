# -*- coding: utf-8 -*-
"""按 FreeMote StaticMotionPainterCore 的权威语义做精确静态提取，再走 build_psd 产出 PSD。

与 emote_assemble.Assembler 启发式的差异（均来自 StaticMotionPainterCore.cs，
经第二个游戏的可移植性审查逐项实证）：
1. 每个参数树的求值时间 = parameter 表算出的默认时间（非固定 t=0）；
2. 帧求值 = FindFrame(time<=t 的最后一帧)；type 0 → 不可见；type 1 → 精确时刻；
3. opa 为 0-255 且父子继承相乘（非 0-1）；
4. 绘制顺序 = 沿链累积 Z（coord[2]）稳定排序；
5. 组挂载不看 layer.type，凡 src=组名 + icon=参数名即嵌套游走；
6. 仿射含 flip/zoom/slant（按 transformOrder）；
7. 变量系统：variableList 默认值 + selectorControl 首选项，变量同名层按
   值域线性映射到帧时间（只映射【内容帧】时间，type0 清理帧不参与——
   否则隐藏语义失效，变体件漏隐藏）；
8. 差分态保留：icon 切换态与变量隐藏件不丢弃，保留为 opacity=0 图层
   （差分数据供 Editor 绑定，平铺渲染不受污染）；表情模块态强制 0 透明度。
   两级去重（同位/同艺术指纹）始终取最大 opa，防不可见残影遮蔽可见件。

启发式装配器（emote_assemble.Assembler）保留为新世代单根树条目的回退；
老世代条目（多根树/变量系统）一律走本提取器。

用法（参数与 build_psd.py 完全一致，本工具做门面替换后转发）::

    EMOTE_MOTION_DIR=... python tools/exact_extract.py --variants variants.json --out merged.psd
"""
import math
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import emote_assemble as EA  # noqa: E402

HIDE = set(x.strip() for x in os.environ.get("HIDE_PIECES", "").split(",") if x.strip())
ICON_OK = re.compile(r"^\d{4}$")


def default_time(obj, group, motion):
    p = obj.get(group, {}).get("motion", {}).get(motion)
    if not isinstance(p, dict):
        return 0.0
    prm = p.get("parameter")
    if isinstance(prm, list):
        for item in prm:
            if not isinstance(item, dict):
                continue
            if int(item.get("enabled", 1) or 0) == 0:
                continue
            b = float(item.get("rangeBegin", 0) or 0)
            e = float(item.get("rangeEnd", 0) or 0)
            dv = float(item.get("division", 0) or 0)
            if abs(e - b) >= 1e-4 and dv > 0 and b <= 0 <= e:
                return (0 - b) * dv / (e - b)
    return 0.0


def find_frame_index(fl, t):
    idx = -1
    for i, fr in enumerate(fl):
        if not isinstance(fr, dict):
            continue
        ft = float(fr.get("time") or 0)
        if ft <= t + 1e-6:
            idx = i
        else:
            break
    return idx


def frame_content(fr, t):
    ftype = int(fr.get("type") or 0)
    if ftype == 0:
        return None
    ft = float(fr.get("time") or 0)
    if ftype == 1 and abs(ft - t) > 1e-3:
        return None
    c = fr.get("content")
    return c if isinstance(c, dict) else None


class M2:
    __slots__ = ("m11", "m12", "m21", "m22")

    def __init__(self, a=1.0, b=0.0, c=0.0, d=1.0):
        self.m11, self.m12, self.m21, self.m22 = a, b, c, d

    def tf(self, x, y):
        return (self.m11 * x + self.m21 * y, self.m12 * x + self.m22 * y)


def mmul(p, l):
    """parent ∘ local（先 local 后 parent），与 C# Multiply(parent, local) 一致。"""
    return M2(
        p.m11 * l.m11 + p.m21 * l.m12,
        p.m12 * l.m11 + p.m22 * l.m12,
        p.m11 * l.m21 + p.m21 * l.m22,
        p.m12 * l.m21 + p.m22 * l.m22,
    )


def calc_matrix(layer, c):
    order = layer.get("transformOrder") or [0, 3, 2, 1]
    m = M2()

    def rot(mm, ang):
        rad = ang * math.pi * 2 / 360.0
        cs, sn = math.cos(rad), math.sin(rad)
        return M2(cs * mm.m11 - sn * mm.m21, cs * mm.m12 - sn * mm.m22,
                  sn * mm.m11 + cs * mm.m21, sn * mm.m12 + cs * mm.m22)

    for k in order:
        if k == 0:
            if int(c.get("fx", 0) or 0):
                m = M2(-m.m11, -m.m12, m.m21, m.m22)
            if int(c.get("fy", 0) or 0):
                m = M2(m.m11, m.m12, -m.m21, -m.m22)
        elif k == 2:
            zx = float(c.get("zx", 1) or 1)
            zy = float(c.get("zy", 1) or 1)
            m = M2(m.m11 * zx, m.m12 * zx, m.m21 * zy, m.m22 * zy)
        elif k == 1:
            a = float(c.get("angle", 0) or 0)
            if abs(a) > 1e-4:
                m = rot(m, a)
        elif k == 3:
            sx = float(c.get("sx", 0) or 0)
            sy = float(c.get("sy", 0) or 0)
            if abs(sx) > 1e-4 or abs(sy) > 1e-4:
                m = M2(m.m11 + sx * m.m21, m.m12 + sx * m.m22,
                       sy * m.m11 + m.m21, sy * m.m12 + m.m22)
    return m


def build_ctx(layer, c, parent):
    m = calc_matrix(layer, c)
    coord = c.get("coord") or [0, 0, 0]
    px, py = parent["m"].tf(float(coord[0]), float(coord[1]))
    cz = float(coord[2]) if len(coord) > 2 else 0.0
    if "opa" in c:
        opa = int(c["opa"] or 0)
    else:
        opa = 255
    inh = int(layer.get("inheritOpacity", 1) or 1)
    if inh != 0:
        opa = int(round(parent["opa"] * opa / 255.0))
    return {"x": parent["x"] + px, "y": parent["y"] + py,
            "z": parent["z"] + cz, "opa": opa, "m": mmul(parent["m"], m)}


class ExactCollector:
    def __init__(self, asm):
        self.asm = asm            # 真 Assembler：d/resx/footprint/atlas_size
        self.obj = asm.d["object"]
        self.source = asm.d["source"]
        self.md = asm.d.get("metadata", {})
        self.insts = []
        self.counter = 0
        # 变量默认值：全部 0；selectorControl 的组 = 首选项 on，其余 off。
        self.var_default = {}
        for v in self.md.get("variableList", []):
            vals = [f.get("frame") for f in v.get("frameList", []) if f.get("frame") is not None]
            self.var_default[v.get("label")] = 0 if (not vals or 0 in vals) else vals[0]
        for s in self.md.get("selectorControl", []) or []:
            if not (isinstance(s, dict) and s.get("enabled") and s.get("optionList")):
                continue
            opts = [o for o in s["optionList"] if isinstance(o, dict)]
            if not opts:
                continue
            val = self.var_default.get(s.get("label"), 0)
            chosen = next((o for o in opts if o.get("onValue") == val), opts[0])
            for o in opts:
                self.var_default[o.get("label")] = (o.get("onValue", 0) if o is chosen
                                                    else o.get("offValue", 1))
        # 【本作校准值】fade_* 叠加/变体件默认【非表示】(=1)，仅默认选中的
        # fade_a=0——按 selectorControl 推导的值在本作实测仍需此兜底；换游戏
        # 若变体件显隐不对，先核对 selectorControl 的 on/offValue 再动这里。
        for name in list(self.var_default):
            if name.startswith("fade_"):
                self.var_default[name] = 0 if name == "fade_a" else 1
        # 变量值域（variableList 的 frame 值域）
        self.var_range = {}
        for v in self.md.get("variableList", []):
            vals = [f.get("frame") for f in v.get("frameList", []) if f.get("frame") is not None]
            if vals:
                self.var_range[v.get("label")] = (min(vals), max(vals))
        # 表情/眨眼模块（E-mote 标准参数名，两世代通用）：状态补发保留，
        # 但强制 opa=0——它们由运行时参数驱动而非图层切换，不作为可切换图层
        self.state_exclude = {"目L", "目R", "眉L", "眉R", "ハイライトL", "ハイライトR", "涙L", "涙R"}

    def var_layer_time(self, layer, fallback_t):
        """变量同名层：按变量默认值把值域映射到该层帧时间轴。
        映射只用【内容帧】时间（type0 清理帧不参与）——否则值 1 会落到
        清理帧上，opa=0 不生效，变体件漏隐藏。"""
        name = str(layer.get("label"))
        if name not in self.var_default:
            return fallback_t, None
        fl = layer.get("frameList")
        if not isinstance(fl, list) or not fl:
            return fallback_t, None
        times = [float(fr.get("time") or 0) for fr in fl
                 if isinstance(fr, dict) and fr.get("content") is not None]
        if not times:
            return fallback_t, None
        tmin, tmax = min(times), max(times)
        v = float(self.var_default.get(name, 0))
        b, e = self.var_range.get(name, (0, 0))
        if e == b:
            return fallback_t, None
        return tmin + (v - b) * (tmax - tmin) / (e - b), v

    def travel_motion(self, group, motion, ctx, stack, path):
        key = group + "/" + motion
        if key in stack:
            return
        stack.add(key)
        p = self.obj.get(group, {}).get("motion", {}).get(motion)
        lay = p.get("layer") if isinstance(p, dict) else None
        if isinstance(lay, list):
            t = default_time(self.obj, group, motion)
            for root in lay:
                self.travel(root, group, motion, t, ctx, stack, path)
        stack.discard(key)

    def emit(self, content, ctx, path):
        src = str(content.get("src", ""))
        if not src:
            return
        icon = content.get("icon")
        if src.startswith("motion/"):
            parts = [p for p in src[7:].split("/") if p]
            if len(parts) >= 2:
                self.travel_motion(parts[0], parts[1], ctx, set(), path)
            return
        tex = src[4:] if src.startswith("tex#") else src
        if tex in self.source and icon is not None and ICON_OK.match(str(icon)):
            ic = self.source[tex]["icon"].get(str(icon))
            if ic is None:
                return
            opa = ctx["opa"] / 255.0
            if str(icon) in HIDE:
                opa = 0.0
            self.counter += 1
            m = ctx["m"]
            self.insts.append({
                "icon": str(icon), "tex": tex, "ic": ic,
                "world": EA.Affine(m.m11, m.m12, m.m21, m.m22, ctx["x"], ctx["y"]),
                "opa": opa, "order": self.counter, "path": path, "_z": ctx["z"],
            })
        elif src in self.obj and icon is not None:
            self.travel_motion(src, str(icon), ctx, set(), path)

    def travel(self, layer, group, motion, t, ctx, stack, path):
        if not isinstance(layer, dict):
            return
        lbl = str(layer.get("label"))
        path2 = path + "/" + lbl if path else lbl
        fl = layer.get("frameList")
        if isinstance(fl, list):
            t_eff, _ = self.var_layer_time(layer, t)
            idx = find_frame_index(fl, t_eff)
            content = None
            if idx >= 0:
                content = frame_content(fl[idx], t_eff)
            if content is not None:
                ctx2 = build_ctx(layer, content, ctx)
                self.emit(content, ctx2, path2)
                child_ctx = ctx2
            else:
                child_ctx = ctx
            # 状态补发：icon 切换层（口型/眨眼/腮红等差分态）的其余状态，
            # 用父 ctx 发射（位置随挂载链继承）。表情/眨眼态按工具链设计
            # 以 opacity=0 图层保留（差分数据不丢，平铺渲染不受污染）。
            if content is not None:
                seen_icons = []
                state_frames = []
                for fr in fl:
                    if not isinstance(fr, dict):
                        continue
                    c = fr.get("content")
                    if not (isinstance(c, dict) and str(c.get("src", "")).startswith("tex#")):
                        continue
                    ic = str(c.get("icon"))
                    if ic not in seen_icons:
                        seen_icons.append(ic)
                        state_frames.append((ic, c))
                if len(seen_icons) >= 2:
                    for ic, c in state_frames:
                        if ic == str(content.get("icon")):
                            continue  # 静息态已发射
                        sctx = build_ctx(layer, c, ctx)
                        if motion in self.state_exclude:
                            sctx["opa"] = 0.0
                        self.emit(c, sctx, path2)
            if int(layer.get("exportSelf", 1) or 0) != 0:
                for ch in layer.get("children") or []:
                    self.travel(ch, group, motion, t_eff, child_ctx, stack, path2)
                for sub in layer.get("layer") or []:
                    self.travel(sub, group, motion, t_eff, child_ctx, stack, path2)
        else:
            if int(layer.get("exportSelf", 1) or 0) != 0:
                for ch in layer.get("children") or []:
                    self.travel(ch, group, motion, t, ctx, stack, path2)
                for sub in layer.get("layer") or []:
                    self.travel(sub, group, motion, t, ctx, stack, path2)

    def run(self):
        md = self.asm.d.get("metadata", {}).get("base", {})
        group = md.get("chara") or "all_parts"
        motion = md.get("motion") or "タイムライン構造"
        self.travel_motion(group, motion, {"x": 0.0, "y": 0.0, "z": 0.0,
                                           "opa": 255, "m": M2()}, set(), "")
        # 差分态保留策略（对齐 build_psd 设计"opa=0 的状态件照常生成图层"）：
        # opa≤0 的实例【不丢弃】，作为 opacity=0 图层保留（腕B 变体、腮红档、
        # 张口内衬、眨眼态等差分数据供 Editor 绑定）。
        # 但两级去重必须先行，且取最大 opa——否则同图件的不可见残影会
        # 占住 build_psd 指纹去重的坑，把可见件遮没（实例：D7 鼻三态同图）。
        print("[extract] after travel+opa:", len(self.insts), flush=True)
        # 同图标同位置去重：保留 opa 最大的实例。
        # （多根/拼接树会经两条路径到达同一部件；跨交淡入淡出帧的一条路径
        #   可能恰逢 opa=0，不能让它代表该件——以引擎并集语义取最上层。）
        best = {}
        for i in self.insts:
            key = (i["icon"], i["tex"],
                   round(i["world"].e), round(i["world"].f), round(i["_z"]))
            if key not in best or i["opa"] > best[key]["opa"]:
                best[key] = i
        # 艺术指纹去重（与 build_psd 同口径：裁剪 RGBA 的 SHA-256）。
        # 同位同图的交叉淡化件按最大 opa 留一份，避免下游指纹去重留下
        # 低透明度残影。
        import hashlib
        from PIL import Image as _Img
        art_digest = {}
        atlas_cache = {}
        for tex, t in self.source.items():
            if not (isinstance(t, dict) and "icon" in t):
                continue
            png = os.path.join(EA.SRC, self.asm.base + ".psb.m",
                               tex + "-texture.png")
            if tex not in atlas_cache:
                atlas_cache[tex] = _Img.open(png).convert("RGBA")
            im_atlas = atlas_cache[tex]
            for iid, ic in t["icon"].items():
                crop = im_atlas.crop((int(ic["left"]), int(ic["top"]),
                                      int(ic["left"] + ic["width"]),
                                      int(ic["top"] + ic["height"])))
                art_digest[(tex, iid)] = hashlib.sha256(crop.tobytes()).hexdigest()
        best2 = {}
        for i in self.insts:
            dg = art_digest.get((i["tex"], i["icon"]), i["icon"])
            key = (dg, round(i["world"].e), round(i["world"].f), round(i["_z"]))
            if key not in best2 or i["opa"] > best2[key]["opa"]:
                best2[key] = i
        self.insts = list(best2.values())
        print("[extract] after art dedup:", len(self.insts), flush=True)
        # 引擎序：Z 稳定排序（同 Z 保树序）
        self.insts.sort(key=lambda i: i["_z"])
        for n, i in enumerate(self.insts):
            i["order"] = n + 1
        return self.insts


def main():
    orig = EA.Assembler

    class Facade(orig):
        def assemble(self, root_param=None):
            return ExactCollector(self).run()

    EA.Assembler = Facade
    import build_psd  # noqa: E402
    sys.argv = ["build_psd.py"] + sys.argv[1:]
    build_psd.main()


if __name__ == "__main__":
    main()
