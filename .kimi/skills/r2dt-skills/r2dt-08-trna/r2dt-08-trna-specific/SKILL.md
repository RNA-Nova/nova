---
name: r2dt-08-trna-specific
version: 1.0.0
description: "R2DT tRNA 指定分类可视化：使用 --domain 和 --isotype 指定 tRNA 类型。"
metadata:
  requires:
    bins: ["docker"]
    docker_image: "rnacentral/r2dt"
---

# r2dt-08-trna-specific

## 概述

使用 `r2dt.py gtrnadb draw --domain <D> --isotype <T>` 指定 tRNA 的 domain 和 isotype 进行可视化。

## 适用场景

- 已知 tRNA 的 domain（如 Eukaryota `E`）和 isotype（如 Thr）。
- 需要强制使用特定 GtRNAdb 模板。

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
>trna1
GGGAAACCCACCUUUGGGAAACCC
```

## 推荐命令

```bash
IMAGE="rnacentral/r2dt"
docker run --rm   -v "$(pwd):/rna/r2dt/temp"   "$IMAGE"   r2dt.py gtrnadb draw /rna/r2dt/temp/inputs/input.fa /rna/r2dt/temp/outputs --domain E --isotype Thr   > stdout.txt 2> stderr.txt
```

参数说明：

- `--domain E`：指定 domain（E = Eukaryota，B = Bacteria，A = Archaea）。
- `--isotype Thr`：指定氨基酸类型。

## 输出说明

- `outputs/results/svg/`：基于指定分类的 tRNA SVG 结构图。

## 注意事项

- 指定的 domain/isotype 应与序列实际分类一致。
- 错误指定可能导致比对失败。
