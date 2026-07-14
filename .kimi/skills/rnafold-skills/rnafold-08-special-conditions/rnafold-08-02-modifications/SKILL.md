---
name: rnafold-08-02-modifications
version: 1.0.0
description: "RNAfold 修饰核苷酸：用 --modifications 参数处理含修饰核苷酸的序列。"
metadata:
  requires:
    bins: ["RNAfold"]
    conda_env: "rnafold"
---

# rnafold-08-02-modifications

## 概述

使用 `--modifications` 参数在能量模型中考虑化学修饰核苷酸的影响。

## 适用场景

- 序列含有 m6A、伪尿苷、硫代磷酸等修饰核苷酸。
- 需要修正热力学参数以反映修饰效应。

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

FASTA 格式，修饰位点用特殊字母编码：

```text
>modified_seq
GGG6AAACCCACCUUUGGGAAACCC
```

## 推荐命令

```bash
RNAfold --modifications=6P input.fa > stdout.txt 2> stderr.txt
```

## 输出说明

输出为考虑修饰后的 MFE 结构及自由能。

## 生成文件

- `*_ss.ps`：修饰序列的结构图。

## 注意事项

- 修饰编码和参数集取决于 ViennaRNA 版本。
- 部分修饰需要自定义能量参数。
