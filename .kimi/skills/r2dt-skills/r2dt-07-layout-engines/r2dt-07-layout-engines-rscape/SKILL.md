---
name: r2dt-07-layout-engines-rscape
version: 1.0.0
description: "R2DT R-scape 布局：使用 R2R/R-scape 引擎绘制 RNA 二级结构。"
metadata:
  requires:
    bins: ["docker"]
    docker_image: "rnacentral/r2dt"
---

# r2dt-07-layout-engines-rscape

## 概述

使用 `r2dt.py templatefree --rscape` 调用 R2R/R-scape 布局引擎绘制 RNA 二级结构。

## 适用场景

- 需要基于共变信息（covariation）的布局。
- 适合展示家族保守的碱基配对。

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
docker run --rm   -v "$(pwd):/rna/r2dt/temp"   "$IMAGE"   r2dt.py templatefree /rna/r2dt/temp/inputs/input.fa /rna/r2dt/temp/outputs --rscape   > stdout.txt 2> stderr.txt
```

## 输出说明

- `outputs/results/svg/`：R-scape 布局的 SVG。
- `outputs/r2r/`：R2R 格式输出。

## 注意事项

- R-scape 布局强调统计显著的碱基配对。
- 对单序列可能效果有限，更适合家族比对。
