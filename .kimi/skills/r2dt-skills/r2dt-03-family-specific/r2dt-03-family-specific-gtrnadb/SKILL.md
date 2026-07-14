---
name: r2dt-03-family-specific-gtrnadb
version: 1.0.0
description: "R2DT GtRNAdb 模板可视化：使用 GtRNAdb tRNA 数据库模板绘制 tRNA 二级结构。"
metadata:
  requires:
    bins: ["docker"]
    docker_image: "rnacentral/r2dt"
---

# r2dt-03-family-specific-gtrnadb

## 概述

使用 `r2dt.py gtrnadb draw` 调用 GtRNAdb 数据库模板进行 tRNA 二级结构可视化。

## 适用场景

- 目标序列为 tRNA。
- 需要基于 GtRNAdb 的 tRNA 比对模板绘制结构。

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

## 输入格式

FASTA 格式：

```text
>trna1
GGGAAACCCACCUUUGGGAAACCC
```

## 推荐命令

```bash
IMAGE="rnacentral/r2dt"
docker run --rm   -v "$(pwd):/rna/r2dt/temp"   "$IMAGE"   r2dt.py gtrnadb draw /rna/r2dt/temp/inputs/input.fa /rna/r2dt/temp/outputs   > stdout.txt 2> stderr.txt
```

## 输出说明

- `outputs/results/svg/`：基于 GtRNAdb 模板的 tRNA SVG 结构图。

## 注意事项

- GtRNAdb 模板针对 tRNA 优化。
- 也可指定 `--domain` 和 `--isotype` 参数进行更精细的分类。
