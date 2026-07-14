---
name: rnafold-03-mea
version: 1.0.0
description: "RNAfold 最大期望精度（MEA）结构预测：基于碱基配对概率给出更稳健的代表性结构。"
metadata:
  requires:
    bins: ["RNAfold"]
    conda_env: "rnafold"
---

# rnafold-03-mea

## 概述

使用 `--MEA` 参数计算最大期望精度结构，常用于 MFE 结构在 ensemble 中占比较低的情况。

## 适用场景

- 需要比 MFE 更稳健的代表性结构。
- 希望直接优化结构准确度期望。

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
>seq_mea
GGGAAACCCACCUUUGGGAAACCC
```

## 推荐命令

```bash
RNAfold --MEA=1.0 input.fa > stdout.txt 2> stderr.txt
```

- `--MEA=1.0`：使用 gamma = 1.0。
- 也可简写为 `--MEA`。

## 输出说明

输出包含 MFE 结构与 MEA 结构。gamma 越大，预测结构倾向于包含更多碱基配对。

## 生成文件

- `*_ss.ps`：结构图。
- `*_dp.ps`：碱基配对概率点阵图（MEA 需要配分函数）。

## 注意事项

- MEA 会先计算配分函数，速度比纯 MFE 慢。
- gamma 参数控制灵敏度与特异度的权衡。
