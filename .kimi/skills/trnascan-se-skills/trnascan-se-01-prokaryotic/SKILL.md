---
name: trnascan-se-01-prokaryotic
version: 1.0.0
description: "tRNAscan-SE 原核生物 tRNA 扫描总览：包含古菌和细菌两种模式。"
metadata:
  requires:
    bins: ["tRNAscan-SE"]
    conda_env: "trnascan-se"
---

# trnascan-se-01-prokaryotic

## 概述

本技能为 `trnascan-se-01-prokaryotic` 子技能集合的总览入口，覆盖古菌（Archaea）和细菌（Bacteria）两种原核生物的 tRNA 扫描。

## 子技能

| 子技能 | 目录 | 模式参数 | 用途 |
|--------|------|----------|------|
| `trnascan-se-01-prokaryotic-archaea` | `trnascan-se-01-prokaryotic-archaea/` | `-A` | 古菌基因组 tRNA 扫描 |
| `trnascan-se-01-prokaryotic-bacteria` | `trnascan-se-01-prokaryotic-bacteria/` | `-B` | 细菌基因组 tRNA 扫描 |

## 前置条件

### Conda 环境

本技能依赖 tRNAscan-SE 2.0.12，使用本目录下的 `environment.yml`：

```yaml
# trnascan-se-skills/environment.yml
name: trnascan-se
channels:
  - bioconda
  - conda-forge
  - defaults
dependencies:
  - python=3.12
  - trnascan-se=2.0.12
```

```bash
conda env create -f trnascan-se-skills/environment.yml
conda activate trnascan-se
```

## 使用方式

根据目标生物域选择对应子技能，参考其 `SKILL.md` 中的命令与参数。
