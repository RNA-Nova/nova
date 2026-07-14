---
name: r2dt-08-trna-auto
version: 1.0.0
description: "R2DT tRNA 自动分类可视化：使用 gtrnadb draw 自动分类并绘制 tRNA 结构。"
metadata:
  requires:
    bins: ["docker"]
    docker_image: "rnacentral/r2dt"
---

# r2dt-08-trna-auto

## 概述

使用 `r2dt.py gtrnadb draw` 对 tRNA 序列自动分类并绘制二级结构。

## 适用场景

- 输入为 tRNA 序列，不确定具体分类。
- 需要自动识别 domain 和 isotype 并选择对应模板。

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

- `outputs/results/svg/`：tRNA SVG 结构图。
- `outputs/gtrnadb/`：GtRNAdb 分类结果。

## 注意事项

- 自动分类依赖序列与 GtRNAdb 模板的匹配。
- 对于非标准 tRNA 可能分类不准确。
