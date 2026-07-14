---
name: rnafold-08-03-nonstandard
version: 1.0.0
description: "RNAfold 非标准温度与盐浓度：在指定温度、盐浓度条件下进行折叠。"
metadata:
  requires:
    bins: ["RNAfold"]
    conda_env: "rnafold"
---

# rnafold-08-03-nonstandard

## 概述

使用 `-T` 和 `--salt` 参数改变折叠温度和离子强度。

## 适用场景

- 实验条件非标准 37°C。
- 需要评估不同盐浓度对结构的影响。

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
>nonstandard_seq
GGGAAACCCACCUUUGGGAAACCC
```

## 推荐命令

```bash
RNAfold -T 25.0 --salt=0.1 input.fa > stdout.txt 2> stderr.txt
```

## 输出说明

输出为非标准条件下的 MFE 结构及自由能。自由能会随温度和盐浓度变化。

## 生成文件

- `*_ss.ps`：非标准条件下的结构图。

## 注意事项

- 温度越低通常稳定结构越多。
- `--salt` 单位和支持范围取决于 ViennaRNA 版本。
