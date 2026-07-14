---
name: r2dt-04-template-free
version: 1.0.0
description: "R2DT 无模板可视化总览：无需模板直接绘制 RNA 二级结构。"
metadata:
  requires:
    bins: ["docker"]
    docker_image: "rnacentral/r2dt"
---

# r2dt-04-template-free

## 概述

本技能为 `r2dt-04-template-free` 子技能集合的总览入口，覆盖不使用模板的 RNA 二级结构可视化。

## 子技能

| 子技能 | 目录 | 参数 | 用途 |
|--------|------|------|------|
| `r2dt-04-template-free-auto` | `r2dt-04-template-free-auto/` | `--auto` | 自动选择最佳无模板布局 |
| `r2dt-04-template-free-basic` | `r2dt-04-template-free-basic/` | 无 | 基础无模板布局 |

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

根据是否需要自动布局选择对应子技能。
