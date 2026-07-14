---
name: r2dt-04-template-free-auto
version: 1.0.0
description: "R2DT 无模板自动布局：使用 --auto 自动选择最佳布局引擎。"
metadata:
  requires:
    bins: ["docker"]
    docker_image: "rnacentral/r2dt"
---

# r2dt-04-template-free-auto

## 概述

使用 `r2dt.py templatefree --auto` 在不使用模板的情况下，自动选择最佳布局引擎绘制 RNA 二级结构。

## 适用场景

- 没有合适模板可用。
- 希望自动比较多种无模板布局并选择最优结果。

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

- `outputs/results/svg/`：自动选择的最优 SVG 结构图。
- `outputs/r2r/`：R2R 布局结果（如适用）。

## 注意事项

- `--auto` 会尝试多种布局并选择得分最高的结果，运行时间更长。
- 无模板模式适合序列与已知家族模板同源性低的情况。
