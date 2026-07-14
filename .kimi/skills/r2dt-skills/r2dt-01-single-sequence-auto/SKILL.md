---
name: r2dt-01-single-sequence-auto
version: 1.0.0
description: "R2DT 单序列自动可视化：对单条 RNA 序列自动选择模板并绘制二级结构图。"
metadata:
  requires:
    bins: ["docker"]
    docker_image: "rnacentral/r2dt"
---

# r2dt-01-single-sequence-auto

## 概述

使用 `r2dt.py draw` 对单条 RNA 序列自动选择最合适的模板，生成标准二级结构图。

## 适用场景

- 快速可视化单条 RNA 序列的二级结构。
- 不确定该用哪个 RNA 家族模板时，让 R2DT 自动选择。

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
docker run --rm   -v "$(pwd):/rna/r2dt/temp"   "$IMAGE"   r2dt.py draw /rna/r2dt/temp/inputs/input.fa /rna/r2dt/temp/outputs   > stdout.txt 2> stderr.txt
```

## 输出说明

`outputs/` 目录下通常包含：

- `results/svg/`：SVG 结构图。
- `results/json/`：结果元数据。
- `results/tsv/`：比对和坐标信息。
- `results/fasta/`：序列文件。
- 按模板家族分类的子目录（如 `crw/`、`rfam/` 等）。

## 注意事项

- 首次运行会自动拉取 `rnacentral/r2dt` 镜像。
- R2DT 可能在 inputs 目录生成 `.ssi` 临时索引文件，运行后可清理。
- 输出文件默认由容器内 root 用户拥有。
