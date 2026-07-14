---
name: r2dt-03-family-specific
version: 1.0.0
description: "R2DT 特定 RNA 家族可视化总览：使用 CRW、GtRNAdb、Rfam、RiboVision 等家族模板。"
metadata:
  requires:
    bins: ["docker"]
    docker_image: "rnacentral/r2dt"
---

# r2dt-03-family-specific

## 概述

本技能为 `r2dt-03-family-specific` 子技能集合的总览入口，覆盖使用特定 RNA 家族模板进行结构可视化的场景。

## 子技能

| 子技能 | 目录 | 命令 | 模板来源 |
|--------|------|------|----------|
| `r2dt-03-family-specific-crw` | `r2dt-03-family-specific-crw/` | `r2dt.py crw draw` | Comparative RNA Web Site (CRW) |
| `r2dt-03-family-specific-gtrnadb` | `r2dt-03-family-specific-gtrnadb/` | `r2dt.py gtrnadb draw` | GtRNAdb tRNA 数据库 |
| `r2dt-03-family-specific-rfam` | `r2dt-03-family-specific-rfam/` | `r2dt.py rfam draw <family>` | Rfam 家族 |
| `r2dt-03-family-specific-ribovision-lsu` | `r2dt-03-family-specific-ribovision-lsu/` | `r2dt.py ribovision draw_lsu` | RiboVision 大亚基 rRNA |

## 前置条件

### 运行环境

R2DT 通过 Docker 镜像 `rnacentral/r2dt` 分发。使用前请确保系统已安装 Docker：

```bash
docker --version
```

首次运行时会自动拉取镜像，也可手动预拉取：

```bash
docker pull rnacentral/r2dt
```

## 使用方式

根据目标 RNA 家族选择对应子技能。
