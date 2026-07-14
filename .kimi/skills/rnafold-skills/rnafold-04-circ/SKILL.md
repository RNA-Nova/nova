---
name: rnafold-04-circ
version: 1.0.0
description: "RNAfold 环状 RNA 折叠：考虑序列首尾相连的拓扑约束。"
metadata:
  requires:
    bins: ["RNAfold"]
    conda_env: "rnafold"
---

# rnafold-04-circ

## 概述

使用 `--circ` 参数对环状 RNA 进行折叠，允许序列首尾之间形成碱基配对。

## 适用场景

- 输入序列为闭合环状 RNA（circRNA）。
- 需要允许跨越 5'/3' 端的配对。

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
>circRNA
GGGAAACCCGGGAAACCC
```

## 推荐命令

```bash
RNAfold --circ input.fa > stdout.txt 2> stderr.txt
```

## 输出说明

输出格式与基础 MFE 相同，但结构允许首尾配对。

## 生成文件

- `*_ss.ps`：circRNA 结构图。

## 注意事项

- `--circ` 假设 5' 与 3' 端共价连接。
- 不适用于含内部断裂的序列。
