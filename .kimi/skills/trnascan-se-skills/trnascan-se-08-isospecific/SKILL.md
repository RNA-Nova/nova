---
name: trnascan-se-08-isospecific
version: 1.0.0
description: "tRNAscan-SE 同工受体 tRNA 分析：使用 -s 输出同工 tRNA 统计。"
metadata:
  requires:
    bins: ["tRNAscan-SE"]
    conda_env: "trnascan-se"
---

# trnascan-se-08-isospecific

## 概述

使用 `-s` 参数输出同工受体（isospecific/isodecoder）tRNA 的统计信息。

## 适用场景

- 需要统计基因组中每种同工 tRNA 的数量和分布。
- 研究 tRNA 基因家族的拷贝数变异。

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
tRNAscan-SE -B -Q   -s isospecific.out   -o tRNA.out   -m tRNA.stats   input.fa > stdout.txt 2> stderr.txt
```

参数说明：

- `-s`：输出同工 tRNA 统计文件。

## 输出说明

- `isospecific.out`：每种氨基酸/反密码子对应的 tRNA 数量统计。
- `tRNA.out`：标准 tRNA 基因列表。
- `tRNA.stats`：扫描统计信息。

## 注意事项

- 同工 tRNA 统计对密码子使用偏性和翻译研究很有用。
- 建议结合基因组注释进行解读。
