---
name: rnafold-01-basic-mfe
version: 1.0.0
description: "RNAfold 基础 MFE 结构预测：对单条或多条 RNA 序列计算最小自由能二级结构。"
metadata:
  requires:
    bins: ["RNAfold"]
    conda_env: "rnafold"
---

# rnafold-01-basic-mfe

## 概述

使用 RNAfold 默认模式，对输入 FASTA 序列计算最小自由能（MFE）二级结构。

## 适用场景

- 快速获得一条或多条 RNA 的最优二级结构。
- 不需要配分函数、配对概率或 ensemble 信息。

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

FASTA 格式，可包含多条序列：

```text
>seq1_basic
GGGAAACCCACCUUUGGGAAACCC
>seq2_basic
GGCAGAGAACAGACUGUCUGUUAU
```

## 推荐命令

```bash
RNAfold input.fa > stdout.txt 2> stderr.txt
```

## 输出说明

```text
>seq1_basic
GGGAAACCCACCUUUGGGAAACCC
(((...((((....))))...))) ( -7.90)
```

- 第三行：MFE 结构（dot-bracket）及自由能（kcal/mol）。
- 同时生成 `*_ss.ps` 结构图文件。

## 注意事项

- 默认仅输出 MFE 结构。
- 如需碱基配对概率，请使用 `rnafold-02-partition`。
- 如需更稳健的结构估计，请使用 `rnafold-03-mea`。
