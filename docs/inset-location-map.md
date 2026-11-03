# 区位图与遥感影像图如何结合进插件

> 结论先行：**不是把县界叠到影像上，而是一张布局里放两幅地图、两个图框**。影像图是主图框，
> 区位图是第二个图框，各自绑自己的地图和相机。以下做法在 ArcGIS Pro 3.1.5 上实测跑通。

## 1 分工

| 成果 | 数据 | 承载方式 | 比例尺量级 |
|---|---|---|---|
| 采样点位布置图（影像图） | 影像底图 + 图斑 + 评价单元 + 采样点 | 主图框 `WEBMAP_MAP_FRAME` → 地图「采样点位图」 | 1:2000～1:3000 |
| 区位示意图 | 县界 + 项目所在县高亮 + 项目点 | 区位图框 `区位图框` → 地图「区位图」 | 1:1500 万 |
| 内外图廓 | 无图层 | 外图框 `外图廓` → 空地图「描边」 | — |

两级比例尺差三个数量级，所以**必须分图框**：把县界当图层叠进 1:3000 的主图里，既看不见也没有意义。

影像侧数据来源在 `acceptance/imagery.py`：项目自有 DOM 优先 → 天地图 → 公开演示影像（仅自查）。
任何来源都先落成本地 GeoTIFF（目标坐标系）再入图，不直接挂在线 MapServer。

## 2 三步实现

```bash
cd D:/work/

# 1) 模板：复制主图框 → 区位图框 + 图名（纯 Python，不需要 arcpy）
python scripts/make_inset_template.py --out temp/inset_demo_20261023/template_inset.pagx

# 2) 建数据 + 建工程 + 绑三个图框（ArcGIS Pro 解释器）
"D:/Program Files/ArcGIS/Pro/bin/Python/envs/arcgispro-py3/python.exe" \
    temp/inset_demo_20261023/build_inset_demo.py

# 3) 新进程导出并读回绑定关系
"D:/Program Files/ArcGIS/Pro/bin/Python/envs/arcgispro-py3/python.exe" \
    temp/inset_demo_20261023/export_inset_demo.py
```

关键 API 只有三行：

```python
inset = aprx.createMap("区位图", "MAP")          # 第二幅地图
frames["区位图框"].map = inset                    # 图框绑地图（不是绑图层）
frames["区位图框"].camera = camera               # 相机独立：x/y 用经纬度、scale 1:1500万
```

主图相机按图斑范围给 `scale`；区位图按县界 extent 反算（`fit_scale()`，中纬度换算出地面米）。
主图比例尺不得细于影像 GSD 允许的最细比例尺（`imagery.usable_map_scale`）——这条规则不因加了区位小图而放宽。

## 3 实测证据（2026-10-23）

- 模板生成 6 项 pytest 全过：`tests/test_inset_template.py`（追加/重复/压指北针/越界/过小/注记不误报）。
- 负向对照：`--rect 15.2,23.0,19.1,26.6` 必报 `与整饰元素重叠：North Arrow [17.9, 25.1, 19.5, 26.7]`。
- 导出读回（`export_result.json`）：

  | 图框 | 绑定地图 | 相机 | 纸面尺寸 |
  |---|---|---|---|
  | 区位图框 | 区位图 | 108.2564E / 23.642N / 1:15,000,000 | 6.2×4.6 cm |
  | WEBMAP_MAP_FRAME | 采样点位图 | 811843.23E / 2574138.45N / 1:3,000 | 18.6×16.4 cm |
  | 外图廓 | 描边 | — | 19.0×16.8 cm |

- 成图 `temp/inset_demo_20261023/inset_demo.png`（1654×2339 @200dpi，1.72 MB）：左上角区位小图为
  111 个县级单元 + 武鸣区橙色高亮 + 红色项目点；主图为影像底图 + 黄色图斑 + 6 个设计采样点；
  图例只剩「设计采样点/补充耕地图斑」两项。
- 项目所在县由项目点空间选择自动得出（武鸣区），无需人工填。

## 4 坑（本轮新踩 + 复用既有结论）

| 现象 | 根因 | 做法 |
|---|---|---|
| 区位框压在指北针上，箭头画在区块里 | 模板指北针占 x17.9–19.5 / y25.1–26.7，右上角并不空 | 生成模板时做重叠检查，默认位改到左上 |
| `AttributeError: 'Layer' object has no attribute 'isBasemap'` | 3.1.5 的 Layer 没这个属性 | `createMap` 后直接清空新地图图层列表，再挂自己的数据 |
| `'Symbology' object has no attribute 'renderer'` | 栅格图层的 symbology 结构与矢量不同 | 只对矢量设符号，影像只改名 |
| 图例冒出 `Band_1/2/3`、红绿蓝三条 | 栅格自动生成图例项 | 复用 `acceptance.map_worker._freeze_legend`（本项目生产链已验证） |
| 坐标注记被误判为障碍，区位框无处可放 | `N左` 注记是框外右对齐单行文字，按宽度外推会越界误报 | 文本障碍按锚点取 ±0.3 cm 小框判定 |
| 加整饰元素时进程静默硬崩 | `Layout.setDefinition()` 混入新元素 | 全部走 `.pagx` JSON 预生成 + `importDocument` |
| `aprx.save()` 后再动布局 API 会挂死 | 进程生命周期边界 | 建工程与导出拆两个进程 |

## 5 接进插件的落点

现状：`acceptance/oneclick.py` 只产出「采样点位图」（影像 + 图斑 + 采样点），没有区位图；
区位图另在 `ARCGIS/location_map/` 单独跑。合并的最小改法：

1. 模板改用 `scripts/make_inset_template.py` 的产物（多一个 `区位图框`，主图框尺寸不变）。
2. `oneclick.run()` 的 job 增加 `inset`：`{counties_fc, point_fc, hit_fc, rect_cm}`；
   县界与项目点来源沿用项目资料，缺县界时整块跳过并在报告里写「未出区位小图」，不静默出空图。
3. `map_worker.build()` 里建第二幅地图、绑 `区位图框`、按县界 extent 设相机；主图逻辑不动。
4. `map_export.export_all()` 逐幅出图时区位图框保持同一个相机（不随主图 target 变化）。

界线合规仍是上游约束：`location_map/data/gx_county.geojson` 来自阿里云 DataV，基准未公开、
不能排除 GCJ-02，正式报件须换三调/年度变更调查或 CGCS2000 官方界线。加小图不会让这条放宽。

## 6 未验证

- 只跑了脚本链，未在 ArcGIS Pro 桌面 Ribbon 点击验证；未接 `OneClickButton` / GP 工具参数。
- 主图影像用的是本地演示影像（UTM 48N、0.6 m），未用天地图或项目 DOM 重跑区位小图链路。
- 区位图框与主图框的出图分辨率/线宽在不同 DPI 下的一致性未测。
- 模板既有的版式缺陷仍在：面积统计行与「坐标注记说明」在行数多时会重叠（与本主题无关）。
- OneClick 走影像 GSD 门控时，区位小图是否需要在报告中同步记录县界来源与时相，尚未定义。
