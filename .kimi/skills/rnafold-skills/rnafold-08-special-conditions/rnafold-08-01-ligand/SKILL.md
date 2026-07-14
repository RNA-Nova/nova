---
name: rnafold-08-01-ligand
version: 1.0.0
description: "RNAfold 配体/ motif 约束：用 --motif 参数模拟配体稳定特定二级结构 motif。"
metadata:
  requires:
    bins: ["RNAfold"]
    conda_env: "rnafold"
---

# rnafold-08-01-ligand

## 概述

使用 `--motif` 参数在能量模型中加入配体结合稳定化能，预测配体存在时的 RNA 结构。

## 适用场景

- 已知小分子配体稳定某个 motif（如茶碱适配体）。
- 需要模拟配体结合后的结构变化。

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
>theophylline_aptamer
GGGAUACCAGAAACCCUUGGCAGCUUU
```

## 推荐命令

```bash
RNAfold --motif="GAUACCAG&CCCUUGGCAGC,(...((((&)...)))...),-9.22" input.fa > stdout.txt 2> stderr.txt
```

`--motif` 格式：

```text
<序列1>&<序列2>,<结构1>&<结构2>,<能量>
```

## 输出说明

输出为加入 motif 能量后的 MFE 结构。

## 生成文件

- `*_ss.ps`：配体约束后的结构图。

## 注意事项

- `--motif` 语法复杂，需确保序列、结构和能量一致。
- 配体稳定能通常来自实验测定或文献。
