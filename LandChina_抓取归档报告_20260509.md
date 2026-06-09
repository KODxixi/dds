# LandChina 抓取归档报告

日期：2026-05-09
项目：DDS / hyp_project 数据湖

## 目标

抓取 LandChina 近 1 年内的土地出让公告数据，并归档到 Cloud Storage：

```text
gs://hyp_project/real_estate_data/landchina/
```

目标归档结构：

```text
raw/          原始 API 响应
parsed/       当前解析结果
normalized/   标准化字段
logs/         抓取日志
shared/manifests/ 每次抓取索引清单
```

## 已完成事项

### 1. Cloud Storage 数据湖结构已建立

已创建并管理以下文件：

```text
gs://hyp_project/CATALOG.md
gs://hyp_project/INDEX.json
gs://hyp_project/real_estate_data/README.md
gs://hyp_project/real_estate_data/shared/schemas/land_parcel_normalized.schema.json
```

### 2. 旧数据已归档

原始旧文件保留：

```text
gs://hyp_project/real_estate_data/auto_scraped_2026-05-08.json
```

归档副本：

```text
gs://hyp_project/real_estate_data/archive/legacy/2026/05/08/legacy_auto_scraped_20260508_raw.json
gs://hyp_project/real_estate_data/archive/legacy/2026/05/08/legacy_auto_scraped_20260508_normalized.json
gs://hyp_project/real_estate_data/shared/manifests/legacy_auto_scraped_20260508_manifest.json
```

### 3. Cloud Run 写入权限已开通

Cloud Run 服务：

```text
dds-landchina-scraper
```

服务账号：

```text
858943527635-compute@developer.gserviceaccount.com
```

已授权：

```text
bucket: gs://hyp_project
role: roles/storage.objectAdmin
```

## LandChina API 探测结果

从 LandChina 前端 JS 中确认真实 API：

```text
https://api.landchina.com/tGygg/transfer/list
```

对应页面：

```text
https://www.landchina.com/#/givingNotice
```

接口字段：

```json
{
  "pageNum": 1,
  "pageSize": 10,
  "startDate": "2025-05-09 00:00:00",
  "endDate": "2026-05-09 23:59:59"
}
```

最初 API 成功返回，确认近一年出让公告总量曾返回约：

```text
42,496 条
```

样本字段包括：

```text
gyggGuid      公告 ID
gyggBt        公告标题
xzqDm         行政区代码
fbSj          发布时间
ggLx          公告类型
xzqFullName   行政区全称
```

## 本地脚本

已创建本地脚本：

```text
C:\Users\shiguanyu\DDS\scripts\scrape_landchina_year.py
```

脚本设计目标：

```text
1. 抓取近一年 LandChina 出让公告
2. 生成 raw / parsed / normalized / log / manifest 五类文件
3. 上传到 gs://hyp_project 的标准数据湖结构
4. 支持 workers、sleep、max-pages、no-upload 参数
```

## 执行记录

### 第一次执行

命令：

```powershell
python .\scripts\scrape_landchina_year.py --days 365 --page-size 100 --max-pages 0 --sleep 0.35
```

结果：

```text
任务卡在 Fetched 25/282 pages
items = 250
errors = 0
```

判断：

```text
任务未完成，可能因为请求阻塞或 WAF 响应异常导致长时间停滞。
```

### 第二次执行：并发抓取

命令：

```powershell
python .\scripts\scrape_landchina_year.py --days 365 --page-size 10 --workers 8 --sleep 0.03
```

结果：

```text
失败，HTTP 301 / 302 跳转到 https://api.landchina.com/#/404
```

### 第三次执行：禁止自动跳转

修复内容：

```text
禁止 urllib 自动跟随 301 / 302，尝试读取响应体。
```

结果：

```text
失败，302 响应体为 nginx HTML，不是 JSON。
```

### 第四次执行：保守单线程测试

命令：

```powershell
python .\scripts\scrape_landchina_year.py --days 365 --page-size 10 --workers 1 --sleep 1.0 --max-pages 30
```

结果：

```text
仍失败，继续返回 302 到 #/404。
```

## 当前判断

LandChina API 已触发 WAF / 反爬拦截。

观察到的状态：

```text
初始阶段：API 可访问，能返回 total 和样本数据
并发后：开始返回 301 / 302 / 418
当前阶段：即使保守单线程也返回 301 / 302 到 #/404
```

这说明当前本机 IP / 请求特征已被 LandChina 风控系统识别，继续高频重试不利于恢复。

## 风险

```text
1. 继续硬抓可能延长 WAF 冷却时间
2. 一次性内存聚合抓取不稳，失败前数据不会落盘
3. LandChina 前端依赖 Hash 校验和 WAF Cookie，请求头稍有差异就可能被重定向
4. 近一年数据量较大，应采用断点续抓策略
```

## 下一步建议

### 优先级 1：改造为断点式稳态采集

脚本应调整为：

```text
1. 每页请求后立即写入本地 raw page 文件
2. 每页成功后立即上传 Cloud Storage
3. 保存 checkpoint.json
4. 支持从上次成功页继续
5. 默认单线程或低并发
6. 随机 sleep，避免固定频率
7. 失败时写 log，不丢已抓数据
```

建议参数：

```text
workers = 1
sleep = 3–8 秒随机
page_size = 10
batch = 每 20 页生成一个 manifest
```

### 优先级 2：等 WAF 冷却后低频重试

建议等待一段时间后再试：

```text
至少 1–3 小时后再进行低频请求
```

### 优先级 3：并行接入省市级公共资源平台

LandChina 是全国总库，反爬强。DDS 数据湖可以先从更稳定的数据源滚动积累：

```text
广东省公共资源交易平台
江苏省土地市场网
浙江省自然资源网上交易系统
各市公共资源交易中心
```

这些平台通常反爬弱、字段更细，适合作为 DDS 的早期数据底座。

## 当前状态

```text
Cloud Storage 数据结构：已完成
旧数据归档：已完成
Cloud Run 写入权限：已完成
LandChina API 定位：已完成
近一年全量抓取：未完成，受 WAF 拦截
本地抓取脚本：已创建，但需要进一步改造成断点续抓版本
```

## 关键路径

```text
下一步不是继续强刷 LandChina，而是先把 scraper 改造成 checkpoint + page-level archive，再低频恢复抓取。
```
