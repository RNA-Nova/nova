---
name: rnafold-06-shape
version: 1.0.0
description: "RNAfold SHAPE 数据整合：将化学探测反应性数据作为约束加入结构预测。"
metadata:
  requires:
    bins: ["RNAfold"]
    conda_env: "rnafold"
---

# rnafold-06-shape

## 概述

使用 `--shape`、`--shapeMethod` 和 `--shapeConversion` 参数，将 SHAPE 反应性数据整合到能量模型中。

## 适用场景

- 已有 SHAPE 化学探测数据。
- 希望用实验反应性信息修正预测结构。

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

1. FASTA 序列文件：

```text
>shape_seq
GGGAAACCCACCUUUGGGAAACCC
```

2. SHAPE 反应性 `.dat` 文件：

```text
1 G 0.05
2 G 0.12
3 G 0.08
4 A 0.85
```

三列分别为：位置（1-based）、核苷酸、反应性值。

## 推荐命令

```bash
RNAfold --shape=shape.dat --shapeMethod=D --shapeConversion=O input.fa > stdout.txt 2> stderr.txt
```

参数说明：

- `--shapeMethod=D`：Deigan 方法。
- `--shapeConversion=O`：使用原始反应性值转换。

## 输出说明

输出为受 SHAPE 约束后的 MFE 结构及自由能。

## 生成文件

- `*_ss.ps`：SHAPE 约束后的结构图。

## 注意事项

- SHAPE 数据质量直接影响结果。
- `--shapeMethod` 还可选 `Z`、`W` 等。
