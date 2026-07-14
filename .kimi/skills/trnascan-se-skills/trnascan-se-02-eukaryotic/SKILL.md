---
name: trnascan-se-02-eukaryotic
version: 1.0.0
description: "tRNAscan-SE 真核生物 tRNA 扫描：使用 -E 模式和 --detail 输出详细信息。"
metadata:
  requires:
    bins: ["tRNAscan-SE"]
    conda_env: "trnascan-se"
---

# trnascan-se-02-eukaryotic

## 概述

使用 `-E` 参数以真核生物模式运行 tRNAscan-SE，并输出详细信息。

## 适用场景

- 目标序列为真核生物（Eukaryote）基因组或基因组片段。
- 需要真核 tRNA 基因的详细注释。

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
tRNAscan-SE -E --detail -Q   -o tRNA.out   -f tRNA.ss   -m tRNA.stats   input.fa > stdout.txt 2> stderr.txt
```

参数说明：

- `-E`：真核模式。
- `--detail`：输出更详细的注释信息。
- `-Q`：安静模式。

## 输出说明

- `tRNA.out`：tRNA 基因列表及详细信息。
- `tRNA.ss`：二级结构。
- `tRNA.stats`：统计信息。

## 注意事项

- 真核模式针对真核 tRNA 特征（如内含子）优化。
- `--detail` 会增加输出信息量。
