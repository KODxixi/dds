---
name: image-hunter
description: Archive original architecture images into D:\ArchLib with auto-classification. Mode A — single URL from Xiaohongshu (xhslink.com / xiaohongshu.com) or WeChat (mp.weixin.qq.com). Mode B — daily auto-hunt that discovers new projects from ArchDaily China, gooood, archiposition (有方), mooool and archives them automatically.
---

# Image Hunter

把好项目的**原图下载 + 自动分类打标**归档到 `D:\ArchLib\{分类}\{所在地}_{项目名}_{设计方}\`。

## 模式 A — 单条链接（小红书 / 公众号）

```powershell
$OutputEncoding = [System.Text.Encoding]::UTF8
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8
python "D:\Vault-assets\AI_Projects\DDS\skills\image-hunter\index.py" "<URL>" [项目名] [设计方] [所在地] [风格]
```

支持 `xhslink.com` / `xiaohongshu.com` / `mp.weixin.qq.com`。`index.py` 逻辑未改动，不要用 web_fetch 或自写下载器。

## 模式 B — 每日自动猎取（新增）

无需手动发链接，自动从 4 个建筑站发现最新项目 → 按质量/话题度智能筛 → 复用模式 A 的下载/分类/归档管线。小红书/公众号通过 `_inbox.txt` 队列半自动处理。

```powershell
python "D:\Vault-assets\AI_Projects\DDS\skills\image-hunter\daily_hunt.py"            # 跑全部启用来源
python "D:\Vault-assets\AI_Projects\DDS\skills\image-hunter\daily_hunt.py" --dry-run  # 只发现+打分，不下载
python "D:\Vault-assets\AI_Projects\DDS\skills\image-hunter\daily_hunt.py" --source gooood
```

**来源**（config.json 开关）：ArchDaily中国 `archdaily.cn` · 谷德 `gooood.cn` · 有方 `archiposition.com` · mooool `mooool.com/feed`。
发现层只抓文章 URL，标题与原图统一从详情页取（`<title>` + 图床正则），稳健不依赖列表页结构。

**`_inbox.txt`**：看到好的小红书/公众号帖子，把链接贴进去（每行一个），每天自动逐条调用 `index.py` 归档，处理后移入 `_inbox_done.txt`。

**智能筛选**：`interest_weights` 命中商业/综合体/文体/酒店等加分（贴合海棠湾五大IP），`noise_keywords` 命中招聘/竞赛/出版减分，顶级事务所加分；`min_score` 阈值、`daily_max_*` 配额。可选 `llm.enabled=true` 用本地 Qwen / Gemini 复筛（OpenAI 兼容接口）。

**去重** `state/seen.json`（每天只抓新项目）。**日报** `D:\ArchLib\_daily\YYYY-MM-DD.md`。**日志** `logs/`。

## 每日自动运行（一次性安装）

双击 `setup_schedule.bat` → 注册 Windows 计划任务 `ImageHunterDaily`，每天 08:30 自动跑。
- 立即真实跑一次：`run_daily.bat`
- 卸载：`schtasks /Delete /TN "ImageHunterDaily" /F`
- 改时间：`schtasks /Change /TN "ImageHunterDaily" /ST 07:00`

## 文件结构

```
image-hunter/
├─ index.py            模式A：单URL原图归档（原逻辑，未改动）
├─ daily_hunt.py       模式B：每日发现→打分→去重→复用index归档
├─ config.json         来源开关 / 智能筛选权重 / 配额 / LLM
├─ _inbox.txt          小红书·公众号链接收件箱
├─ run_daily.bat       计划任务调用入口
├─ setup_schedule.bat  一次性注册Windows计划任务
├─ state/seen.json     去重账本（运行后生成）
└─ logs/               每日运行日志（运行后生成）
```


## mooool 住宅展示全量（独立结构 → D:\ArchLib）

mooool 的「公寓住宅」「售楼中心展示区」两类按 space 分类页全量抓取，**命中即抓、不走质量阈值**，归档到独立结构：

```
D:\ArchLib\10_居住\11_公寓住宅\{地点_项目名_设计方}\{项目名}_01.jpg ...   ← apartments(公寓住宅)
D:\ArchLib\30_营销空间\31_售楼中心展示区\{地点_项目名_设计方}\...           ← sales-center(售楼展示区)
```
> 分类已对齐 `D:\ArchLib\_检索系统\taxonomy.yaml`（8大类/21业态单轴）。index.py 的 `classify_category` 与日报均按业态路径 `X0_大类/XY_业态` 归档，未知→`90_资源·非案例/98_待分类`。

- 日常：计划任务每天自动跑，翻 `pages`(默认2) 页增量，`state/seen_mooool_res.json` 去重。
- 初次全量回填：`run_daily.bat --backfill`（翻 `backfill_pages` 默认12 页；售楼区共约36页，`max_per_run` 默认40 防跑飞，可多次跑逐步补全）。
- 配置见 `config.json > mooool_residential`（archive_root / targets / pages / backfill_pages / max_per_run）。

## 注意
- 中国站点需在**本机原生网络**运行（Cowork 沙箱无法直连），故用 Windows 计划任务本机跑。
- 小红书 profile / 公众号列表无公开稳定接口，无法纯自动发现，用 `_inbox.txt` 队列承接（把“每次手动发送”变成“贴进收件箱、每天批处理”）。
- 维护脚本时改文件请用可靠写入并校验字节数（本仓库脚本曾因写入截断踩坑）。

## 图片格式：统一 PNG（2026-06-26 起）

落盘环节统一转 **PNG**：`index.py` 新增 `save_as_png()`，模式 A 的 `do_archive` 与每日 mooool 住宅的 `_archive_to` 均经它落盘（需 Pillow；缺库则回退原 .jpg 并告警）。gif 动图不处理。

存量回填（本机原生跑，比沙箱快）：
```powershell
python "D:\Vault-assets\AI_Projects\DDS\skills\image-hunter\backfill_png.py"   # 转 D:\ArchLib 下 10_~80_ 案例目录的 jpg/webp/bmp→png，删原文件
```
