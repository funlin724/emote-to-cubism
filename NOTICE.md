# NOTICE

## 本仓库不包含的内容

本仓库**不包含、不分发**以下内容：

1. **任何游戏的美术资产**：图集 PNG、部件图、事件 CG、立绘、音视频，
   以及 moc3 / psd / cmo3 / model3.json 等模型成品；
2. **任何游戏的解密密钥**：`tools/mzs_decrypt.py` 要求用户以
   `--key` 参数 / `MZS_BASE_KEY` 环境变量 / 本地 `mzs_key.txt`
   （已被 .gitignore 排除）自行提供密钥；
3. 任何特定游戏的名称、角色名或可识别的条目名——文档一律使用
   通用表述（"条目名""示例角色""本作校准值"）。

## 使用条款

使用本工具链即表示你理解并承诺：

- 只处理**自己合法获得**的游戏数据；提取密钥只针对自己持有的游戏副本；
- 产出的模型与素材**仅限个人研究用途**，不再分发；
- 不利用本工具规避任何技术保护措施以实施侵权；
- 遵守游戏 EULA 与所在地法律。

## 第三方组件与致谢

| 组件 | 来源 | 用途 |
|---|---|---|
| py-moc3 | PyPI `py-moc3` 0.1.0 | `tools/moc3lib/` 的基座；本仓库修复其写出器 bug（段顺序、计数字段语义、SOT 偏移），修复记录见 docs/moc3-format-semantics.md |
| FreeMote | https://github.com/uanu2002/FreeMote | 外部工具（不随本仓库分发）；解包/回封基础设施，MDF 算法参考实现 |
| Live2D Cubism | Live2D Inc. | 运行时/Editor 为商业软件，不随本仓库分发；格式逆向仅作互操作研究 |

E-mote 是 M2 Co., Ltd. 的商标；Live2D、Cubism 是 Live2D Inc. 的商标。
本项目与其无关联、未获认可。

## 文档许可

`docs/` 下的格式文档以 CC-BY-4.0 提供。许可证全文见
https://creativecommons.org/licenses/by/4.0/legalcode
（BY：署名本仓库；仓库内不随附许可证全文副本）。
