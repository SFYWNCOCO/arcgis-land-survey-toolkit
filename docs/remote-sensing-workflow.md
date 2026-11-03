# 补充耕地验收遥感线索筛查

版本：首版（ArcGIS Pro 3.1.x）  
功能入口：功能区“遥感核查” → “Sentinel-2 目录检索”；或工具箱 `Sentinel2CatalogSearch`。已有手工影像筛查仍可从工具箱运行 `RemoteSensingScreening`。

目录检索由操作人员显式启动，工具会把评价面（曲线按10m步长加密）转为 WGS84 GeoJSON 并提交给 Earth Search 公共 STAC 目录；输出 JSON 保存完整查询记录、AOI 指纹、候选 Item、波段比例参数、同日期/平台/处理基线瓦片组及足迹几何覆盖率。若分页未完整，结果不能进入后续影像准备。景级云量用于筛选，不代表评价区实际云量或有效像元覆盖。

## 规范边界

广西《补充耕地质量验收作业指导书》v1.1 将“影像图”列为有则提供的材料（PDF第11页/印刷第5页），采样点位布置图要求以影像图为底图（PDF第44页/印刷第38页）。指导书没有规定 NDVI、自动变化检测的合格阈值或自动验收算法。因此本工具只生成时相差异线索与来源记录；现场踏勘、RTK、土壤采样、检测及验收结论仍由专业人员按适用规范完成。

## 输入与步骤

### 在线目录检索与多瓦片时相准备

1. 在工具箱运行 `Sentinel2CatalogSearch`（功能区遥感按钮直达目录检索）。工具会明确提示评价区 WGS84 几何要发送给 Earth Search；需确认后才联网。选择日期范围和景级云量上限，输出一个新的候选 JSON。
2. 确认候选清单 `search_complete=true`。候选按日期、卫星平台和处理基线分组，并列出覆盖评价区相交的 MGRS 瓦片、场景云量、B04/B08/SCL 资产与比例参数、足迹几何面积覆盖率。足迹覆盖率不证明影像有效像元或无云。
3. 运行 `PrepareSentinel2Pair`，输入同一 AOI、评价单元、编号字段和完整目录 JSON，人工指定前后期日期组。日期须前后有序；卫星平台或处理基线不同必须勾选确认。AOI 指纹必须与检索结果一致；未知/地理坐标或非米制目标 CRS、超 1500 万 10m 像元预算均停止。
4. 工具通过 GDAL `/vsicurl/` 按 AOI 裁切并拼接相邻 COG 瓦片。每个波段在单独 ArcGIS Pro Python工作进程中执行；父进程默认硬截止300秒、显示处理阶段/进度并在超时后终止子进程。工作进程向 GDAL 配置连接超时15秒、请求超时60秒、最多重试2次、间隔2秒；对原生读取卡死提供可靠截止的是父进程的300秒限制。失败会中止整组准备并清理本轮唯一输出目录。B04/B08 输出 Float32、10m和NoData=-9999；SCL输出UInt16、20m/NoData=0、最近邻。读回网格、坐标系、NoData、文件大小与SHA-256并写入`pair_manifest.json`。公网读取仍可能返回TIFF tile错误，开始大范围处理前先用小AOI验证服务与有效像元。
5. 工具把本地 B04/B08/SCL 交给既有 NDVI 核心，输出每期 NDVI、差值、评价单元统计和来源证据。检查有效覆盖率、SCL 排除数和 SCL NoData 数；足迹交叠不等于像元有效覆盖完整。

真实样区实测：以修仁乡边界在相邻 QDG/RDH 瓦片交界处截取的约3.14 km²演示区，检索到2026-10-08 S2C与2026-10-13 S2B两组、每组两块瓦片，足迹几何覆盖率均100%。历史小样六个B04/B08/SCL裁片经Pro/GDAL读回，NDVI+SCL单元有效覆盖率98.4821%，SCL NoData各120像元；结果仍是`NEEDS_FIELD_REVIEW`，演示AOI不是项目验收单元。本次新版工作进程测试：45秒截止成功由父进程执行并清理目录；按ArcPy导出的项目WKT以300秒上限重试时，Earth Search资产出现GDAL `TIFFReadEncodedTile()` 读取错误，父进程阻止了部分成果并清理目录。该复测证明失败隔离与清理路径，不证明本轮成功完成六个波段。当前尚不能声称已消除公网服务错误或大 AOI处理风险。

检索采用公开 Earth Search Sentinel-2 C1 L2A STAC 接口；只允许配置的 Earth Search 服务端与数据桶 HTTPS 地址。在线窗口读取依赖 COG 的 HTTP Range 响应和网络带宽，建议先用小 AOI 验证数据连通与时相覆盖，再处理全项目范围。

1. 准备前后期红光、近红外单波段栅格。优先选同季节、同传感器、同处理级别且已完成大气/反射率处理的产品；Sentinel-2 L2A 建议同时提供两期 SCL 场景分类栅格。
2. 在工具中指定两期各自的红光/NIR数据、影像日期、数据源/产品级别和反射率比例因子/偏移量。参数必须按产品元数据填写；工具不根据波段序号猜测传感器或比例参数。
3. 输入评价单元面和编号字段。工具会检查四个波段的坐标系、像元大小、行列数、范围及评价单元坐标系；发现不一致时停止，不替用户选择基准转换。评价单元应先按项目确定的空间参考投影；工具不会改写原始面。
4. 有 SCL 时同时指定前后期 SCL。工具使用最近邻法将分类层对齐到红光/NIR网格，并排除 NoData、饱和/缺陷、阴影、云、薄卷云和雪冰类别（0、1、2、3、7、8、9、10、11），把排除类别及每期/每评价单元计数写入证据 JSON/CSV。SCL 类2在较新处理基线中标为投射/地形阴影；旧基线语义可能不同，因此保守排除并保留处理基线复核。SCL只适用于其对应影像产品；坐标系不同应先依据项目资料处理。
5. 不提供 SCL 时，必须勾选“无SCL时确认已完成两期云/阴影掩膜”。这项仍是操作人员声明，不是软件对预处理质量的证明。
6. 检查输出的 NDVI 前期/后期栅格、差值栅格、评价单元统计 CSV 和溯源 JSON；栅格和总览统计只覆盖评价单元，SCL NoData 数与分类像元数分开列出。将明显差异区叠加 CAD/RTK、现场照片及踏勘记录复核。

## 计算与状态

`NDVI=(NIR-Red)/(NIR+Red)`；`差值=后期NDVI-前期NDVI`。统计表按评价单元给出有效像元数、有效覆盖率、前后期均值、差值均值/标准差及差值正负像元数。没有设置自动合格阈值，输出状态固定为 `NEEDS_FIELD_REVIEW`。

NDVI差异可能来自季节/物候、作物、土壤背景、云和阴影、配准偏差、处理级别或传感器差异。有效覆盖率表示评价单元中参与计算的像元比例；提供SCL时，它反映SCL排除后的可用比例，不代表SCL分类准确率。10米级等中分辨率影像只适合面状线索筛查，不能替代高精度DOM或RTK边界测量。

## 技术依据

- 广西补充耕地质量验收作业指导书 v1.1（2026-08-17），影像图材料要求及采样点位图要求。
- [Earth Search STAC API](https://earth-search.aws.element84.com/v1/api.html)：Sentinel-2 C1 L2A 目录查询与 STAC Item 元数据。
- [GDAL Virtual File Systems](https://gdal.org/en/stable/user/virtual_file_systems.html) 和 [GDAL HTTP 配置项](https://gdal.org/en/stable/user/configoptions.html)：`/vsicurl/` 远程 COG Range 读取与 HTTP 多范围行为。
- [ArcGIS Pro 3.1 NDVI function](https://pro.arcgis.com/en/pro-app/3.1/help/analysis/raster-functions/ndvi-function.htm)：NDVI使用红光和近红外波段；产品的波段含义须明确。
- [ArcGIS Pro 3.1 RasterToNumPyArray / NumpyArrayToRaster functions](https://pro.arcgis.com/en/pro-app/3.1/arcpy/functions/alphabetical-list-of-arcpy-functions.htm)：用于读取栅格像元并生成分析输出。
- [ArcGIS Pro 3.1 Add Rasters To Mosaic Dataset](https://pro.arcgis.com/en/pro-app/3.1/tool-reference/data-management/add-rasters-to-mosaic-dataset.htm)：后续可扩展为多景影像编目、足迹和时相管理。
- [Copernicus Sentinel-2 产品规格说明](https://sentinels.copernicus.eu/-/sentinel-2-product-specification-document-psd-in-version-15.0)：SCL 类2由 DARK_FEATURES 更名为 CAST_SHADOW；不同处理基线仍应按产品元数据核对。

## 后续可扩展

后续可增加按评价单元生成影像核验图、候选变化斑块人工复核层、星载云概率/云影掩膜对照与运行耗时估计。没有按传感器产品规则实现并验证前，不把云量字段或影像范围当成有效覆盖证明；需要 Image Analyst 的变化检测也必须在运行时核验许可。
