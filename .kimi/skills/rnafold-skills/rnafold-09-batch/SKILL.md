---
name: rnafold-09-batch
version: 1.0.0
description: "RNAfold 批量处理：用 --jobs、--auto-id 等参数一次处理多条序列。"
metadata:
  requires:
    bins: ["RNAfold"]
    conda_env: "rnafold"
---

# rnafold-09-batch

## 概述

使用 `--jobs`、`--auto-id`、`--id-prefix` 和 `--id-digits` 参数对多条序列进行批量预测。

## 适用场景

- 需要一次性处理多条 RNA 序列。
- 希望自动生成结构图文件名。

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

FASTA 格式，包含多条序列：

```text
>seq_001
GGGAAACCCACCUUUGGGAAACCC
>seq_002
GGCAGAGAACAGACUGUCUGUUAU
>seq_003
CCCGGGAAACCCCUUUAAAGGG
```

## 推荐命令

```bash
RNAfold --jobs=2 --auto-id --id-prefix="batch" --id-digits=4 input.fa > stdout.txt 2> stderr.txt
```

## 输出说明

标准输出包含每条序列的 MFE 结构。自动生成命名结构图：

- `batch_0001_ss.ps`
- `batch_0002_ss.ps`
- `batch_0003_ss.ps`

## 注意事项

- `--jobs` 可加速大批量计算，但占用更多 CPU。
- ID 不唯一时建议配合 `--id-prefix` 避免覆盖。
- 可叠加 `-p` 同时计算配分函数。
