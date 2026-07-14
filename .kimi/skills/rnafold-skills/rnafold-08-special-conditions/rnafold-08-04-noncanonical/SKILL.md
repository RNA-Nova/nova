---
name: rnafold-08-04-noncanonical
version: 1.0.0
description: "RNAfold 非标准碱基配对：用 --nsp 参数允许特定的非 Watson-Crick 碱基配对。"
metadata:
  requires:
    bins: ["RNAfold"]
    conda_env: "rnafold"
---

# rnafold-08-04-noncanonical

## 概述

使用 `--nsp` 参数允许特定的非标准碱基配对。

## 适用场景

- 需要允许 G-A 等非 Watson-Crick 配对。
- 研究含特殊配对 motif 的 RNA。

## 前置条件

### Conda 环境

本技能依赖 ViennaRNA / RNAfold 2.7.2，使用本目录下的 `environment.yml`：

```yaml
# rnafold-skills/environment.yml
name: rnafold
channels:
  - bioconda
  - conda-forge
  - defaults
dependencies:
  - python=3.12
  - viennarna=2.7.2
```

```bash
conda env create -f rnafold-skills/environment.yml
conda activate rnafold
```

## 输入格式

FASTA 格式：

```text
>noncanonical_seq
GGGAAACCCACCUUUGGGAAACCC
```

## 推荐命令

```bash
RNAfold --nsp="-GA" input.fa > stdout.txt 2> stderr.txt
```

## 输出说明

输出为允许 G-A 等非标准配对后的 MFE 结构。

## 生成文件

- `*_ss.ps`：含非标准配对的结构图。

## 注意事项

- 非标准配对的能量参数通常不如标准配对精确。
- 过度使用可能导致不合理结构。
