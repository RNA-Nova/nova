---
name: trnascan-se-07-pseudogene-with-check
version: 1.0.0
description: "tRNAscan-SE 启用假基因检查：使用 -H 参数过滤 tRNA 假基因。"
metadata:
  requires:
    bins: ["tRNAscan-SE"]
    conda_env: "trnascan-se"
---

# trnascan-se-07-pseudogene-with-check

## 概述

使用 `-H` 参数启用假基因检查（search for tRNA pseudogenes），在扫描过程中识别并标记假基因。

## 适用场景

- 需要区分功能性 tRNA 和 tRNA 假基因。
- 真核基因组中假基因较多时建议使用。

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

## 输入格式

FASTA 格式：

```text
>seq1
AGTCATCGATCGATCGTAGCTAGCTAGCTAGCTAGCTAGCTAGCTAGCTAGCTAGCTAGCTAGC
```

## 推荐命令

```bash
tRNAscan-SE -E -H -Q   -o tRNA_with_pseudo.out   -f tRNA.ss   input.fa > stdout.txt 2> stderr.txt
```

参数说明：

- `-H`：启用假基因检查。
- `-E`：真核模式。

## 输出说明

- `tRNA_with_pseudo.out`：tRNA 列表，包含假基因标记。
- `tRNA.ss`：二级结构。

## 注意事项

- 启用假基因检查会增加计算量。
- 假基因判定依赖统计阈值，建议结合其他证据验证。
