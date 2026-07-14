---
name: rnafold-05-constraint
version: 1.0.0
description: "RNAfold 硬约束折叠：强制某些碱基必须配对、不配对或保持单链。"
metadata:
  requires:
    bins: ["RNAfold"]
    conda_env: "rnafold"
---

# rnafold-05-constraint

## 概述

使用 `-C` 与 `--enforceConstraint` 参数，结合 constraint 字符串限制折叠搜索空间。

## 适用场景

- 有实验或进化证据要求某些位置必须配对/不配对。
- 需要限制结构搜索空间。

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

FASTA 格式，序列后附加 constraint 字符串：

```text
>constrained_seq
GGGAAACCCUUUGGGAAACCC
((((...))))..........
```

约束符号：

| 符号 | 含义 |
|------|------|
| `.` | 无约束 |
| `(` / `)` | 强制配对 |
| `x` | 强制单链 |
| `|` | 强制切断 |

## 推荐命令

```bash
RNAfold -C --enforceConstraint input.fa > stdout.txt 2> stderr.txt
```

## 输出说明

输出为满足硬约束后的 MFE 结构及自由能。

## 生成文件

- `*_ss.ps`：约束后的结构图。

## 注意事项

- 若约束冲突，RNAfold 可能报错或无解。
- 本示例为硬约束；软约束需使用其他参数。
