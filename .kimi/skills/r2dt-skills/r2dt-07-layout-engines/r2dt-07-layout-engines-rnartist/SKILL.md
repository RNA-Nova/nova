---
name: r2dt-07-layout-engines-rnartist
version: 1.0.0
description: "R2DT RNArtist 布局：使用 RNArtist 引擎绘制 RNA 二级结构。"
metadata:
  requires:
    bins: ["docker"]
    docker_image: "rnacentral/r2dt"
---

# r2dt-07-layout-engines-rnartist

## 概述

使用 `r2dt.py templatefree --rnartist` 调用 RNArtist 布局引擎绘制 RNA 二级结构。

## 适用场景

- 需要 RNArtist 风格的结构布局。
- 希望获得发表级美观度的结构图。

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
docker run --rm   -v "$(pwd):/rna/r2dt/temp"   "$IMAGE"   r2dt.py templatefree /rna/r2dt/temp/inputs/input.fa /rna/r2dt/temp/outputs --rnartist   > stdout.txt 2> stderr.txt
```

## 输出说明

- `outputs/results/svg/`：RNArtist 布局的 SVG。
- `outputs/rnartist/`：RNArtist 专用输出。

## 注意事项

- RNArtist 布局通常较美观，适合发表。
- 运行时间可能较长。
