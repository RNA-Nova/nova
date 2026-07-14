---
name: r2dt-skills
version: 1.0.0
description: "R2DT 场景技能集合索引：覆盖 01、02、03、04、07、08、10 七个主场景及其子场景。"
metadata:
  requires:
    bins: ["docker"]
    docker_image: "rnacentral/r2dt"
---

# r2dt-skills 场景技能集合

本目录把 R2DT 的使用场景整理为可复用的 skill。每个子目录即一个独立 skill，目录名与 skill 名完全一致。

## 包含的技能

| 编号 | 技能名 | 目录 | 说明 |
|------|--------|------|------|
| 01 | `r2dt-01-single-sequence-auto` | `r2dt-01-single-sequence-auto/` | 单序列自动可视化 |
| 02 | `r2dt-02-batch-visualization` | `r2dt-02-batch-visualization/` | 批量序列可视化 |
| 03 | `r2dt-03-family-specific` | `r2dt-03-family-specific/` | 特定 RNA 家族可视化总览 |
| 03-crw | `r2dt-03-family-specific-crw` | `r2dt-03-family-specific/r2dt-03-family-specific-crw/` | CRW 模板 |
| 03-gtrnadb | `r2dt-03-family-specific-gtrnadb` | `r2dt-03-family-specific/r2dt-03-family-specific-gtrnadb/` | GtRNAdb 模板 |
| 03-rfam | `r2dt-03-family-specific-rfam` | `r2dt-03-family-specific/r2dt-03-family-specific-rfam/` | Rfam 模板 |
| 03-lsu | `r2dt-03-family-specific-ribovision-lsu` | `r2dt-03-family-specific/r2dt-03-family-specific-ribovision-lsu/` | RiboVision LSU 模板 |
| 04 | `r2dt-04-template-free` | `r2dt-04-template-free/` | 无模板可视化总览 |
| 04-auto | `r2dt-04-template-free-auto` | `r2dt-04-template-free/r2dt-04-template-free-auto/` | 自动布局 |
| 04-basic | `r2dt-04-template-free-basic` | `r2dt-04-template-free/r2dt-04-template-free-basic/` | 基础布局 |
| 07 | `r2dt-07-layout-engines` | `r2dt-07-layout-engines/` | 多布局引擎比较总览 |
| 07-auto | `r2dt-07-layout-engines-auto` | `r2dt-07-layout-engines/r2dt-07-layout-engines-auto/` | 自动选择 |
| 07-rnapuzzler | `r2dt-07-layout-engines-rnapuzzler` | `r2dt-07-layout-engines/r2dt-07-layout-engines-rnapuzzler/` | RNApuzzler |
| 07-rnartist | `r2dt-07-layout-engines-rnartist` | `r2dt-07-layout-engines/r2dt-07-layout-engines-rnartist/` | RNArtist |
| 07-rscape | `r2dt-07-layout-engines-rscape` | `r2dt-07-layout-engines/r2dt-07-layout-engines-rscape/` | R-scape |
| 08 | `r2dt-08-trna` | `r2dt-08-trna/` | tRNA 可视化总览 |
| 08-auto | `r2dt-08-trna-auto` | `r2dt-08-trna/r2dt-08-trna-auto/` | 自动分类 |
| 08-specific | `r2dt-08-trna-specific` | `r2dt-08-trna/r2dt-08-trna-specific/` | 指定 domain/isotype |
| 10 | `r2dt-10-stitch` | `r2dt-10-stitch/` | 发表级图片拼接总览 |
| 10-basic | `r2dt-10-stitch-basic` | `r2dt-10-stitch/r2dt-10-stitch-basic/` | 基础拼接 |
| 10-sorted | `r2dt-10-stitch-sorted-colored` | `r2dt-10-stitch/r2dt-10-stitch-sorted-colored/` | 排序并上色 |
| 10-captions | `r2dt-10-stitch-with-captions` | `r2dt-10-stitch/r2dt-10-stitch-with-captions/` | 带标题与样式 |

## 运行环境

R2DT 通过 Docker 镜像分发，请确保系统已安装 Docker：

```bash
docker --version
```

首次运行时会自动拉取镜像，也可手动预拉取：

```bash
docker pull rnacentral/r2dt
```

## 通用输入格式

R2DT 主要接受 FASTA 格式：

```text
>sequence_name
GGGAAACCCACCUUUGGGAAACCC
```

场景 05（病毒基因组注释）使用 Stockholm 格式（`.stk`）。

## 如何选择技能

| 需求 | 技能 |
|------|------|
| 单序列自动可视化 | `r2dt-01-single-sequence-auto` |
| 批量序列可视化 | `r2dt-02-batch-visualization` |
| 指定 RNA 家族模板 | `r2dt-03-family-specific-*` |
| 无模板可视化 | `r2dt-04-template-free-*` |
| 比较不同布局引擎 | `r2dt-07-layout-engines-*` |
| tRNA 可视化 | `r2dt-08-trna-*` |
| 拼接多个 SVG | `r2dt-10-stitch-*` |
