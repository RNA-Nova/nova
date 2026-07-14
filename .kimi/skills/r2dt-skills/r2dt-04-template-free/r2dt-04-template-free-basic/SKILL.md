---
name: r2dt-04-template-free-basic
version: 1.0.0
description: "R2DT 无模板基础布局：使用默认布局引擎绘制 RNA 二级结构。"
metadata:
  requires:
    bins: ["docker"]
    docker_image: "rnacentral/r2dt"
---

# r2dt-04-template-free-basic

## 概述

使用 `r2dt.py templatefree` 默认参数，在不使用模板的情况下绘制 RNA 二级结构。

## 适用场景

- 没有合适模板可用。
- 需要快速获得基础无模板结构图。

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
docker run --rm   -v "$(pwd):/rna/r2dt/temp"   "$IMAGE"   r2dt.py templatefree /rna/r2dt/temp/inputs/input.fa /rna/r2dt/temp/outputs   > stdout.txt 2> stderr.txt
```

## 输出说明

- `outputs/results/svg/`：默认布局的 SVG 结构图。
- `outputs/r2r/`：R2R 布局结果。

## 注意事项

- 默认布局通常使用 R2R 引擎。
- 结构美观度可能不如 `--auto` 选择的结果。
