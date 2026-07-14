---
name: rnafold-07-gquad
version: 1.0.0
description: "RNAfold G-四链体预测：在二级结构预测中显式考虑 G-quadruplex 结构。"
metadata:
  requires:
    bins: ["RNAfold"]
    conda_env: "rnafold"
---

# rnafold-07-gquad

## 概述

使用 `--gquad` 参数预测序列中可能形成的 G-四链体结构。

## 适用场景

- 序列富含鸟嘌呤，可能形成 G4。
- 需要在结构预测中显式考虑 G4。

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
>gquad_seq
GGGGTTTTGGGGTTTTGGGGTTTTGGGG
```

## 推荐命令

```bash
RNAfold --gquad input.fa > stdout.txt 2> stderr.txt
```

## 输出说明

输出包含 G4 结构信息，结构图中通常有特殊标记。

## 生成文件

- `*_ss.ps`：含 G4 标注的结构图。

## 注意事项

- G4 结构高度依赖离子环境（尤其 K⁺）。
- 实验验证（CD 光谱、Tb-探针等）通常必不可少。
