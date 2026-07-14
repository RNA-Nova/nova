---
name: trnascan-se-01-prokaryotic-archaea
version: 1.0.0
description: "tRNAscan-SE 古菌 tRNA 扫描：使用 -A 模式识别古菌基因组中的 tRNA 基因。"
metadata:
  requires:
    bins: ["tRNAscan-SE"]
    conda_env: "trnascan-se"
---

# trnascan-se-01-prokaryotic-archaea

## 概述

使用 `-A` 参数以古菌模式运行 tRNAscan-SE，识别古菌基因组中的 tRNA 基因。

## 适用场景

- 目标序列为古菌（Archaea）基因组或基因组片段。
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
tRNAscan-SE -A -Q   -o tRNA.out   -f tRNA.ss   -m tRNA.stats   input.fa > stdout.txt 2> stderr.txt
```

参数说明：

- `-A`：古菌模式。
- `-Q`：安静模式，不显示进度。
- `-o`：标准输出文件。
- `-f`：二级结构文件。
- `-m`：统计信息文件。

## 输出说明

- `tRNA.out`：tRNA 基因列表及坐标、类型、反密码子、得分等信息。
- `tRNA.ss`：tRNA 二级结构（stockholm 格式）。
- `tRNA.stats`：扫描统计信息。

## 注意事项

- `-Q` 用于批量/自动化场景，避免交互式输出。
- 古菌模式针对古菌 tRNA 特征优化。
