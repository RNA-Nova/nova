---
name: trnascan-se-09-visualization
version: 1.0.0
description: "tRNAscan-SE 可视化准备：输出 tRNA 序列和二级结构用于可视化。"
metadata:
  requires:
    bins: ["tRNAscan-SE"]
    conda_env: "trnascan-se"
---

# trnascan-se-09-visualization

## 概述

使用 tRNAscan-SE 生成 tRNA 序列和二级结构文件，作为下游可视化工具的输入。

## 适用场景

- 需要为 tRNA 二级结构可视化准备输入文件。
- 结合 tRNAviz、VARNA 等工具绘制 tRNA 结构图。

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
tRNAscan-SE -B -Q   -f tRNA.ss   -a tRNA.fa   input.fa > stdout.txt 2> stderr.txt
```

参数说明：

- `-f`：输出二级结构文件（stockholm 格式）。
- `-a`：输出预测 tRNA 的 FASTA 序列。

## 输出说明

- `tRNA.ss`：tRNA 二级结构（可用于 tRNAviz 等）。
- `tRNA.fa`：tRNA 序列。

## 注意事项

- `tRNA.ss` 是 stockholm 格式，可被多种 RNA 结构可视化工具读取。
- 如需批量可视化，可结合 r2dt 等工具。
