---
name: r2dt-10-stitch-basic
version: 1.0.0
description: "R2DT 基础图片拼接：使用 r2dt.py stitch 将多个 SVG 结构图横向拼接。"
metadata:
  requires:
    bins: ["docker"]
    docker_image: "rnacentral/r2dt"
---

# r2dt-10-stitch-basic

## 概述

使用 `r2dt.py stitch` 将多个 SVG 结构图拼接成一张组合图。

## 适用场景

- 需要将多个 RNA 结构图并排展示。
- 生成发表级组合图的基础版本。

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

多个 SVG 文件（通常来自 R2DT 可视化输出）：

```text
inputs/
├── seq1.svg
├── seq2.svg
└── seq3.svg
```

## 推荐命令

```bash
IMAGE="rnacentral/r2dt"
docker run --rm   -v "$(pwd):/rna/r2dt/temp"   "$IMAGE"   bash -c 'r2dt.py stitch /rna/r2dt/temp/inputs/*.svg -o /rna/r2dt/temp/outputs/combined.svg'   > stdout.txt 2> stderr.txt
```

## 输出说明

- `outputs/combined.svg`：横向拼接后的组合 SVG。

## 注意事项

- 输入 SVG 应由 R2DT 生成，以保证格式兼容。
- 拼接顺序默认按文件名排序。
