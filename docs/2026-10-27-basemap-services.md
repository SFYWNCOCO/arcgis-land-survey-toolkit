# 多来源底图接入

采样助手1.60沿用1.59的批量矢量接入与1.58的POI功能，扩展影像管线的授权服务格式。AI侧栏0.7另提供高德/天地图浏览预览，浏览器预览不替代原生项目DOM。

- 内置 `tianditu`（影像）、`tianditu_vector`（矢量）、`tianditu_image_labels`（影像注记）、`tianditu_vector_labels`（矢量注记）。注记仅作叠加参考，不能作为独立遥感影像。
- 授权服务：在采样助手“当前地图/单位在线影像服务”选择服务JSON，或填缓存MapServer根地址；凭证单独填写。阿里云按实际购买产品提供的服务地址配置，没有通用的“阿里云高精度影像免费地址”。
- 模板在 `templates/authorized_xyz_service.example.json` 和 `templates/authorized_wmts_service.example.json`。必须替换示例主机/图层/矩阵集/授权信息。示例保留demo_only=true，未经核实不得改为正式用途。
- 仅支持操作者确认的全球 Web Mercator、256像元瓦片、全球西北原点、标准XYZ层级；0级分辨率156543.03392804097米，逐级减半。WMTS的矩阵标识可在URL中写固定前缀（如 `EPSG:3857:{z}`）。512瓦片、局部原点、非标准LOD、经纬度矩阵与GCJ-02/BD-09均不支持直接成果转换。必须读取服务Capabilities/官方资料核对配置，配置声明本身不构成实测证据。
- 自定义JSON现在必须明确coordinate_system、tile_size、matrix_origin、level_zero_resolution。旧配置需按当前模板重建，不设历史兼容分支。凭证用{tk}占位，不能内嵌实际Key。

来源、版本、授权参考和服务坐标系写入影像清单；时相、原生GSD、定位误差未核定时仍标UNVERIFIED，输出像元不能证明地面定位精度。新地图源不会自动提升已有示例区成果精度，此轮未重出业务图件。

高德用官方JS API卫星/标准图层及坐标转换，单独申请JS API Key+安全密钥，与POI Web服务Key分开。当前无Key和阿里云实际服务，已验证本机接口/参数/隔离与拒绝路径，真实在线取图待配置。

官方说明：[高德图层](https://lbs.amap.com/api/javascript-api-v2/guide/layers/official-layers)、[坐标转换](https://lbs.amap.com/api/javascript-api-v2/guide/transform/convertfrom)、[安全密钥](https://lbs.amap.com/api/javascript-api-v2/guide/abc/jscode)、[天地图WMTS](https://lbs.tianditu.gov.cn/server/MapService.html)。
