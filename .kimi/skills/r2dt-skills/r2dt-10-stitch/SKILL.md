---
name: r2dt-10-stitch
version: 1.0.0
description: "R2DT 发表级图片拼接总览：包含基础拼接、排序上色、带标题样式三种模式。"
metadata:
  requires:
    bins: ["docker"]
    docker_image: "rnacentral/r2dt"
---

# r2dt-10-stitch

## 概述

本技能为 `r2dt-10-stitch` 子技能集合的总览入口，覆盖使用 `r2dt.py stitch` 拼接多个 SVG 结构图的场景。

## 子技能

| 子技能 | 目录 | 参数 | 用途 |
|--------|------|------|------|
| `r2dt-10-stitch-basic` | `r2dt-10-stitch-basic/` | 无 | 基础 SVG 拼接 |
| `r2dt-10-stitch-sorted-colored` | `r2dt-10-stitch-sorted-colored/` | `--sort --color` | 排序并上色 |
| `r2dt-10-stitch-with-captions` | `r2dt-10-stitch-with-captions/` | `--captions ... --gap ... --glyph ...` | 带标题与样式 |

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

根据拼接需求选择对应子技能。
