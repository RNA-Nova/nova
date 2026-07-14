---
name: rnafold-08-special-conditions
version: 1.0.0
description: "RNAfold 特殊条件总览：配体约束、修饰核苷酸、非标准温度/盐浓度、非标准碱基配对。"
metadata:
  requires:
    bins: ["RNAfold"]
    conda_env: "rnafold"
---

# rnafold-08-special-conditions

## 概述

本技能为 `rnafold-08-special-conditions` 子技能集合的总览入口。

## 子技能

| 子技能 | 目录 | 核心参数 | 用途 |
|--------|------|----------|------|
| `rnafold-08-01-ligand` | `rnafold-08-01-ligand/` | `--motif` | 配体/ motif 约束 |
| `rnafold-08-02-modifications` | `rnafold-08-02-modifications/` | `--modifications` | 修饰核苷酸 |
| `rnafold-08-03-nonstandard` | `rnafold-08-03-nonstandard/` | `-T`, `--salt` | 非标准温度/盐浓度 |
| `rnafold-08-04-noncanonical` | `rnafold-08-04-noncanonical/` | `--nsp` | 非标准碱基配对 |

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

## 使用方式

根据具体建模需求选择对应子技能，参考其 `SKILL.md` 中的命令与参数。
