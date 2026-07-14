---
name: trnascan-se-07-pseudogene
version: 1.0.0
description: "tRNAscan-SE 假基因检测总览：包含禁用和启用假基因检查两种模式。"
metadata:
  requires:
    bins: ["tRNAscan-SE"]
    conda_env: "trnascan-se"
---

# trnascan-se-07-pseudogene

## 概述

本技能为 `trnascan-se-07-pseudogene` 子技能集合的总览入口，覆盖禁用和启用假基因检查两种模式。

## 子技能

| 子技能 | 目录 | 参数 | 用途 |
|--------|------|------|------|
| `trnascan-se-07-pseudogene-no-check` | `trnascan-se-07-pseudogene-no-check/` | `-D` | 禁用假基因检查，提高灵敏度 |
| `trnascan-se-07-pseudogene-with-check` | `trnascan-se-07-pseudogene-with-check/` | `-H` | 启用假基因检查，过滤假基因 |

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

根据是否需要假基因过滤选择对应子技能。
