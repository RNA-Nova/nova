---
name: r2dt-03-family-specific-crw
version: 1.0.0
description: "R2DT CRW 模板可视化：使用 Comparative RNA Web Site 模板绘制 RNA 二级结构。"
metadata:
  requires:
    bins: ["docker"]
    docker_image: "rnacentral/r2dt"
---

# r2dt-03-family-specific-crw

## 概述

使用 `r2dt.py crw draw` 调用 CRW（Comparative RNA Web Site）模板进行 RNA 二级结构可视化。

## 适用场景

- 目标 RNA 属于 CRW 数据库覆盖的家族（如 rRNA）。
- 需要基于 CRW 比对模板绘制结构。

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
docker run --rm   -v "$(pwd):/rna/r2dt/temp"   "$IMAGE"   r2dt.py crw draw /rna/r2dt/temp/inputs/input.fa /rna/r2dt/temp/outputs   > stdout.txt 2> stderr.txt
```

## 输出说明

- `outputs/results/svg/`：基于 CRW 模板的 SVG 结构图。
- 其他元数据文件与自动模式相同。

## 注意事项

- CRW 模板适合 rRNA 等大型 RNA 家族。
- 输入序列应与 CRW 模板有较好的同源性。
