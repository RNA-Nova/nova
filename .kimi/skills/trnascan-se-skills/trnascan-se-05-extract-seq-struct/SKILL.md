---
name: trnascan-se-05-extract-seq-struct
version: 1.0.0
description: "tRNAscan-SE 序列与结构提取：输出 tRNA 序列、BED 坐标和二级结构。"
metadata:
  requires:
    bins: ["tRNAscan-SE"]
    conda_env: "trnascan-se"
---

# trnascan-se-05-extract-seq-struct

## 概述

使用 tRNAscan-SE 的 `-a` 和 `-b` 选项，同时提取预测 tRNA 的 FASTA 序列和 BED 坐标。

## 适用场景

- 需要获取预测 tRNA 的核酸序列。
- 需要生成 tRNA 位点的 BED 文件用于下游分析（如可视化、PCR 设计）。

## 前置条件

### Conda 环境

本技能依赖 tRNAscan-SE 2.0.12，使用本目录下的 `environment.yml`：

```yaml
# trnascan-se-skills/environment.yml
name: trnascan-se
channels:
  - bioconda
  - conda-forge
  - defaults
dependencies:
  - python=3.12
  - trnascan-se=2.0.12
```

```bash
conda env create -f trnascan-se-skills/environment.yml
conda activate trnascan-se
```

## 输入格式

FASTA 格式：

```text
>seq1
AGTCATCGATCGATCGTAGCTAGCTAGCTAGCTAGCTAGCTAGCTAGCTAGCTAGCTAGCTAGC
```

## 推荐命令

```bash
tRNAscan-SE -B -Q   -o tRNA.out   -f tRNA.ss   -a tRNA.fa   -b tRNA.bed   input.fa > stdout.txt 2> stderr.txt
```

参数说明：

- `-a`：输出预测 tRNA 的 FASTA 序列文件。
- `-b`：输出 BED 格式的坐标文件。

## 输出说明

- `tRNA.out`：tRNA 基因列表。
- `tRNA.ss`：二级结构。
- `tRNA.fa`：预测 tRNA 序列。
- `tRNA.bed`：tRNA 位点坐标。

## 注意事项

- `-a` 输出的 FASTA 序列可用于后续的 tRNA 结构可视化或功能分析。
- BED 文件可直接加载到 IGV 等基因组浏览器。
