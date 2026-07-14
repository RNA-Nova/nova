---
name: r2dt-10-stitch-sorted-colored
version: 1.0.0
description: "R2DT 排序并上色拼接：使用 --sort 和 --color 参数优化拼接结果。"
metadata:
  requires:
    bins: ["docker"]
    docker_image: "rnacentral/r2dt"
---

# r2dt-10-stitch-sorted-colored

## 概述

使用 `r2dt.py stitch --sort --color` 对多个 SVG 结构图排序并上色后拼接。

## 适用场景

- 需要按某种规则排序结构图。
- 希望为结构图添加颜色标注。

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

多个 SVG 文件：

```text
inputs/
├── seq1.svg
├── seq2.svg
└── seq3.svg
```

## 推荐命令

```bash
IMAGE="rnacentral/r2dt"
docker run --rm   -v "$(pwd):/rna/r2dt/temp"   "$IMAGE"   bash -c 'r2dt.py stitch /rna/r2dt/temp/inputs/*.svg -o /rna/r2dt/temp/outputs/virus_genome.svg --sort --color'   > stdout.txt 2> stderr.txt
```

## 输出说明

- `outputs/virus_genome.svg`：排序并上色后的组合 SVG。

## 注意事项

- `--sort` 的具体排序规则取决于 R2DT 版本和输入元数据。
- `--color` 会为结构添加颜色标注。
