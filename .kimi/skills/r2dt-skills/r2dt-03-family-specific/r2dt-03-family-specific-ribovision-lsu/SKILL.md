---
name: r2dt-03-family-specific-ribovision-lsu
version: 1.0.0
description: "R2DT RiboVision LSU 模板可视化：使用 RiboVision 大亚基 rRNA 模板。"
metadata:
  requires:
    bins: ["docker"]
    docker_image: "rnacentral/r2dt"
---

# r2dt-03-family-specific-ribovision-lsu

## 概述

使用 `r2dt.py ribovision draw_lsu` 调用 RiboVision 大亚基 rRNA（LSU）模板进行结构可视化。

## 适用场景

- 目标序列为大亚基 rRNA（LSU）。
- 需要基于 RiboVision LSU 模板绘制结构。

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
>lsu_seq
GGGAAACCCACCUUUGGGAAACCC
```

## 推荐命令

```bash
IMAGE="rnacentral/r2dt"
docker run --rm   -v "$(pwd):/rna/r2dt/temp"   "$IMAGE"   r2dt.py ribovision draw_lsu /rna/r2dt/temp/inputs/input.fa /rna/r2dt/temp/outputs   > stdout.txt 2> stderr.txt
```

## 输出说明

- `outputs/results/svg/`：基于 RiboVision LSU 模板的 SVG 结构图。

## 注意事项

- LSU rRNA 序列较长，运行时间可能较长。
- 输入序列应为 LSU rRNA 片段或完整序列。
