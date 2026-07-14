---
name: rnafold-02-partition
version: 1.0.0
description: "RNAfold 配分函数与 ensemble 分析：计算碱基配对概率、ensemble 自由能及结构多样性。"
metadata:
  requires:
    bins: ["RNAfold"]
    conda_env: "rnafold"
---

# rnafold-02-partition

## 概述

使用 `-p` 参数计算配分函数，得到碱基配对概率、ensemble 自由能、centroid 结构和 ensemble diversity。

## 适用场景

- 需要碱基配对概率矩阵。
- 需要评估 MFE 结构在 ensemble 中的代表性。

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
>seq_partition
GGGAAACCCACCUUUGGGAAACCC
```

## 推荐命令

```bash
RNAfold -p input.fa > stdout.txt 2> stderr.txt
```

## 输出说明

```text
>seq_partition
GGGAAACCCACCUUUGGGAAACCC
(((...((((....))))...))) ( -7.90)
(((...((((....))))...))) [ -8.14]
(((...((((....))))...))) { -7.90 d=0.51}
 frequency of mfe structure in ensemble 0.678186; ensemble diversity 0.85
```

- `(...)`：MFE 结构及自由能。
- `[...]`：ensemble 自由能。
- `{...}`：centroid 结构及平均距离 `d`。
- 最后一行：MFE 频率与 ensemble diversity。

## 生成文件

- `*_ss.ps`：MFE 结构图。
- `*_dp.ps`：碱基配对概率点阵图。

## 注意事项

- `-p` 计算量高于纯 MFE，但仍是多项式复杂度。
- 点阵图中颜色越深表示配对概率越高。
