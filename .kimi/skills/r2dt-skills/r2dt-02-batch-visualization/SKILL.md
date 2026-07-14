---
name: r2dt-02-batch-visualization
version: 1.0.0
description: "R2DT 批量序列可视化：对多序列 FASTA 批量生成二级结构图。"
metadata:
  requires:
    bins: ["docker"]
    docker_image: "rnacentral/r2dt"
---

# r2dt-02-batch-visualization

## 概述

使用 `r2dt.py draw` 对多序列 FASTA 文件批量自动可视化。

## 适用场景

- 需要一次性可视化多条 RNA 序列。
- 批量生成发表级结构图。

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

多序列 FASTA 格式：

```text
>seq1
GGGAAACCCACCUUUGGGAAACCC
>seq2
GGCAGAGAACAGACUGUCUGUUAU
```

## 推荐命令

```bash
IMAGE="rnacentral/r2dt"
docker run --rm   -v "$(pwd):/rna/r2dt/temp"   "$IMAGE"   r2dt.py draw /rna/r2dt/temp/inputs/input.fa /rna/r2dt/temp/outputs   > stdout.txt 2> stderr.txt
```

## 输出说明

与单序列自动可视化相同，但输出目录中包含所有输入序列的结果子目录。

## 注意事项

- 批量输入时确保 FASTA 序列 ID 唯一。
- 大量序列会显著增加运行时间。
