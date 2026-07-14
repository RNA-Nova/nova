---
name: r2dt-07-layout-engines-auto
version: 1.0.0
description: "R2DT 布局引擎自动选择：使用 --auto 在无模板模式下自动选择最佳布局。"
metadata:
  requires:
    bins: ["docker"]
    docker_image: "rnacentral/r2dt"
---

# r2dt-07-layout-engines-auto

## 概述

使用 `r2dt.py templatefree --auto` 自动比较并选择最佳布局引擎。

## 适用场景

- 不确定哪种布局引擎效果最好。
- 希望获得美观度最优的结构图。

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
docker run --rm   -v "$(pwd):/rna/r2dt/temp"   "$IMAGE"   r2dt.py templatefree /rna/r2dt/temp/inputs/input.fa /rna/r2dt/temp/outputs --auto   > stdout.txt 2> stderr.txt
```

## 输出说明

- `outputs/results/svg/`：自动选择的最优 SVG。

## 注意事项

- `--auto` 运行时间比单一引擎更长。
- 适合最终发表前选择最佳展示效果。
