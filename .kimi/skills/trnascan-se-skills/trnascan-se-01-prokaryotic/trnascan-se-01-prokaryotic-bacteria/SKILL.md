---
name: trnascan-se-01-prokaryotic-bacteria
version: 1.0.0
description: "tRNAscan-SE 细菌 tRNA 扫描：使用 -B 模式识别细菌基因组中的 tRNA 基因。"
metadata:
  requires:
    bins: ["tRNAscan-SE"]
    conda_env: "trnascan-se"
---

# trnascan-se-01-prokaryotic-bacteria

## 概述

使用 `-B` 参数以细菌模式运行 tRNAscan-SE，识别细菌基因组中的 tRNA 基因。

## 适用场景

- 目标序列为细菌（Bacteria）基因组或基因组片段。
- 需要高置信度的 tRNA 基因注释。

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
tRNAscan-SE -B -Q   -o tRNA.out   -f tRNA.ss   -m tRNA.stats   input.fa > stdout.txt 2> stderr.txt
```

参数说明：

- `-B`：细菌模式。
- `-Q`：安静模式。
- `-o`：标准输出文件。
- `-f`：二级结构文件。
- `-m`：统计信息文件。

## 输出说明

- `tRNA.out`：tRNA 基因列表。
- `tRNA.ss`：tRNA 二级结构。
- `tRNA.stats`：扫描统计信息。

## 注意事项

- 细菌模式针对细菌 tRNA 特征优化。
- 与 `-A` 模式参数结构相同，仅训练模型不同。
