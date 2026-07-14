---
name: trnascan-se-07-pseudogene-no-check
version: 1.0.0
description: "tRNAscan-SE 禁用假基因检查：使用 -D 参数跳过假基因过滤，提高灵敏度。"
metadata:
  requires:
    bins: ["tRNAscan-SE"]
    conda_env: "trnascan-se"
---

# trnascan-se-07-pseudogene-no-check

## 概述

使用 `-D` 参数禁用假基因检查（disable pseudogene checking），适用于希望获得更高灵敏度而不过滤假基因的场景。

## 适用场景

- 希望尽可能多地发现 tRNA-like 序列。
- 不关注假基因过滤，或希望后续自行过滤。

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
tRNAscan-SE -E -D -Q   -o tRNA_no_pseudo_check.out   -f tRNA.ss   input.fa > stdout.txt 2> stderr.txt
```

参数说明：

- `-D`：禁用假基因检查。
- `-E`：真核模式（本示例中）。

## 输出说明

- `tRNA_no_pseudo_check.out`：包含更多假阳性候选的 tRNA 列表。
- `tRNA.ss`：二级结构。

## 注意事项

- 禁用假基因检查会提高灵敏度，但可能引入更多假阳性。
- 结果需要额外的人工或统计过滤。
