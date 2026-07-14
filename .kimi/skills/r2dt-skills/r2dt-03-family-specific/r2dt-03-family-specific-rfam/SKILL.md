---
name: r2dt-03-family-specific-rfam
version: 1.0.0
description: "R2DT Rfam 家族模板可视化：使用指定 Rfam 家族模板绘制 RNA 二级结构。"
metadata:
  requires:
    bins: ["docker"]
    docker_image: "rnacentral/r2dt"
---

# r2dt-03-family-specific-rfam

## 概述

使用 `r2dt.py rfam draw <family>` 调用指定 Rfam 家族模板进行 RNA 二级结构可视化。

## 适用场景

- 已知目标 RNA 属于某个 Rfam 家族（如 RF00162）。
- 需要基于 Rfam 种子比对模板绘制结构。

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
>seq1
GGGAAACCCACCUUUGGGAAACCC
```

## 推荐命令

```bash
IMAGE="rnacentral/r2dt"
docker run --rm   -v "$(pwd):/rna/r2dt/temp"   "$IMAGE"   r2dt.py rfam draw RF00162 /rna/r2dt/temp/inputs/input.fa /rna/r2dt/temp/outputs   > stdout.txt 2> stderr.txt
```

参数说明：

- `RF00162`：Rfam 家族 accession（示例）。

## 输出说明

- `outputs/RF00162/`：基于该 Rfam 家族的结果目录。
- `outputs/results/svg/`：SVG 结构图。

## 注意事项

- 需提前确认目标序列属于指定的 Rfam 家族。
- 错误的家族选择会导致比对失败或结构质量差。
