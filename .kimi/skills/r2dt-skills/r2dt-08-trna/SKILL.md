---
name: r2dt-08-trna
version: 1.0.0
description: "R2DT tRNA 结构可视化总览：包含自动分类和指定 domain/isotype 两种模式。"
metadata:
  requires:
    bins: ["docker"]
    docker_image: "rnacentral/r2dt"
---

# r2dt-08-trna

## 概述

本技能为 `r2dt-08-trna` 子技能集合的总览入口，覆盖 tRNA 结构可视化的两种模式。

## 子技能

| 子技能 | 目录 | 参数 | 用途 |
|--------|------|------|------|
| `r2dt-08-trna-auto` | `r2dt-08-trna-auto/` | 无 | 自动分类并可视化 tRNA |
| `r2dt-08-trna-specific` | `r2dt-08-trna-specific/` | `--domain E --isotype Thr` | 指定 domain 和 isotype |

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

根据是否需要指定 tRNA 分类参数选择对应子技能。
