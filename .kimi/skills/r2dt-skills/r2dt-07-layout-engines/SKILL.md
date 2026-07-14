---
name: r2dt-07-layout-engines
version: 1.0.0
description: "R2DT 多布局引擎比较总览：比较 auto、RNApuzzler、RNArtist、R-scape 等布局引擎。"
metadata:
  requires:
    bins: ["docker"]
    docker_image: "rnacentral/r2dt"
---

# r2dt-07-layout-engines

## 概述

本技能为 `r2dt-07-layout-engines` 子技能集合的总览入口，覆盖多种 RNA 二级结构布局引擎的比较。

## 子技能

| 子技能 | 目录 | 参数 | 布局引擎 |
|--------|------|------|----------|
| `r2dt-07-layout-engines-auto` | `r2dt-07-layout-engines-auto/` | `--auto` | 自动选择 |
| `r2dt-07-layout-engines-rnapuzzler` | `r2dt-07-layout-engines-rnapuzzler/` | `--rnapuzzler` | RNApuzzler |
| `r2dt-07-layout-engines-rnartist` | `r2dt-07-layout-engines-rnartist/` | `--rnartist` | RNArtist |
| `r2dt-07-layout-engines-rscape` | `r2dt-07-layout-engines-rscape/` | `--rscape` | R2R/R-scape |

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

根据目标布局引擎选择对应子技能。
