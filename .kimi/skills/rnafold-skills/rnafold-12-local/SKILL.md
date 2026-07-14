---
name: rnafold-12-local
version: 1.0.0
description: "RNAfold 局部碱基配对限制：用 --maxBPspan 限制最长碱基配对距离。"
metadata:
  requires:
    bins: ["RNAfold"]
    conda_env: "rnafold"
---

# rnafold-12-local

## 概述

使用 `--maxBPspan` 参数限制碱基配对的最大跨度，适用于长序列局部结构预测。

## 适用场景

- 序列较长，只关心局部碱基配对。
- 需要限制远程配对以减少计算量。

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
>long_seq
GGGAAACCCACCUUUGGGAAACCC...
```

## 推荐命令

```bash
RNAfold --maxBPspan=150 input.fa > stdout.txt 2> stderr.txt
```

## 输出说明

输出为仅允许跨度 ≤150 nt 的碱基配对后的 MFE 结构。

## 生成文件

- `*_ss.ps`：局部限制后的结构图。

## 注意事项

- `--maxBPspan` 可降低内存和计算时间。
- 会丢弃所有远程相互作用，可能丢失重要生物学结构。
- 应根据具体问题选择合适的 span。
