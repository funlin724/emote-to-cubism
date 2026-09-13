# -*- coding: utf-8 -*-
"""眼部叠放校正：按期望相对序重排眼部件的 draw_order 值。

背景：icon metadata 的 zorder 是组内局部序；且 extras（同宿主多状态图标）
的 draw_order 曾用固定步进生成，出现大量并列值——draw_order 相同时渲染
顺序未定义，瞳孔件会盖住睫毛/眼皮（实测反馈）。

用法（在全部 draw_order 分配完成后、写 moc3 之前）：
    from eye_stack import fix_eye_stack
    fix_eye_stack(meshes)      # meshes 元素需有 'icon' 与 'draw_order'

实现：draw_order 的**值集不变**（与眉毛/脸件的全局交错保持不变），仅把
升序排列后的值按期望相对序重新分配给各眼部件——同期望秩的状态件互斥
（如同一眼睛的 4 个状态图标不同时可见），值并列无碍；不同秩的相邻件获得
严格递增值，相对序正确。

【本作校准值】EYE_STACK_ORDER 的图标 ID 清单按具体游戏实测；
图标 ID 不跨文件稳定，换游戏需重新标定。
"""

# 底 → 顶 的期望叠放序（每个 icon 的期望秩；同秩 = 互斥状态或同层小组件）
EYE_STACK_ORDER = [
    '0043', '0045',                    # 左/右眼白·睁
    '0044', '0046',                    # 左/右眼白·半闭
    '0041', '0042',                    # 虹膜
    '0087', '0088',                    # 瞳孔附加件
    '0039', '0040',                    # 高光
    '0031', '0035',                    # 眼睑·睁
    '0032', '0036',                    # 眼睑·半闭
    '0033', '0037',                    # 眼睑·3/4 闭
    '0034', '0038',                    # 眼睑·闭
    '0047', '0048', '0049', '0050',   # 左眼影各态
    '0051', '0052', '0053', '0054',   # 右眼影各态
]

_RANK = {icon: k for k, icon in enumerate(EYE_STACK_ORDER)}


def fix_eye_stack(meshes):
    """按期望相对序重排眼部件 draw_order 值的分配。返回被修改的件数。"""
    eye = [m for m in meshes if m['icon'] in _RANK]
    if len(eye) < 2:
        return 0
    # 1) 期望秩稳定排序（同秩保持当前相对序——互斥态无碍）
    eye.sort(key=lambda m: (_RANK[m['icon']], m['draw_order']))
    # 2) 值集守恒：把升序的 draw_order 值按新顺序回填
    values = sorted(m['draw_order'] for m in eye)
    for m, v in zip(eye, values):
        m['draw_order'] = v
    return len(eye)
