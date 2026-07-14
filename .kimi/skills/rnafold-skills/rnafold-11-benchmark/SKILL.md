---
name: rnafold-11-benchmark
version: 1.0.0
description: "RNAfold 基准测试模式：用 --benchmark 对比预测结构与参考结构。"
metadata:
  requires:
    bins: ["RNAfold"]
    conda_env: "rnafold"
---

# rnafold-11-benchmark

## 概述

使用 `--benchmark` 和 `--bm-output` 参数将预测结果与参考结构进行对比，输出评估指标。

## 适用场景

- 有已知参考结构，需要评估 RNAfold 预测准确性。
- 批量计算 sensitivity、PPV 等指标。

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

FASTA 格式，序列后紧跟参考结构：

```text
>benchmark_seq
GGGAAACCCACCUUUGGG
(((...)))(((...)))
```

> ⚠️ 必须使用 FASTA 格式（`>name` 开头），否则 RNAfold 2.7.2 可能触发 Segmentation fault。

## 推荐命令

```bash
RNAfold --benchmark --bm-output="benchmark_results.txt" input.fa > stdout.txt 2> stderr.txt
```

## 输出说明

`benchmark_results.txt` 包含预测结构与参考结构的对比及 sensitivity、PPV、F1-score 等指标。

## 生成文件

- `benchmark_results.txt`：benchmark 统计结果。
- `*_ss.ps`：预测结构图。

## 注意事项

- 参考结构必须紧随序列之后。
- 大规模 benchmark 建议配合 `--jobs` 批量处理。
