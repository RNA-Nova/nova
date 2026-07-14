---
name: rnafold-skills
version: 1.0.0
description: "ViennaRNA RNAfold 场景技能集合索引：覆盖除场景 10（Boltzmann 抽样）外的全部主场景与分场景。"
metadata:
  requires:
    bins: ["RNAfold"]
    conda_env: "rnafold"
---

# rnafold 场景技能集合

本目录把 RNAfold 的使用场景整理为可复用的 skill。每个子目录即一个独立 skill，目录名与 skill 名完全一致。

## 包含的技能

| 编号 | 技能名 | 目录 | 说明 |
|------|--------|------|------|
| 01 | `rnafold-01-basic-mfe` | `rnafold-01-basic-mfe/` | 基础 MFE 结构预测 |
| 02 | `rnafold-02-partition` | `rnafold-02-partition/` | 配分函数与碱基配对概率 |
| 03 | `rnafold-03-mea` | `rnafold-03-mea/` | 最大期望精度（MEA）结构 |
| 04 | `rnafold-04-circ` | `rnafold-04-circ/` | 环状 RNA 折叠 |
| 05 | `rnafold-05-constraint` | `rnafold-05-constraint/` | 硬约束折叠 |
| 06 | `rnafold-06-shape` | `rnafold-06-shape/` | SHAPE 化学探测数据整合 |
| 07 | `rnafold-07-gquad` | `rnafold-07-gquad/` | G-四链体预测 |
| 08 | `rnafold-08-special-conditions` | `rnafold-08-special-conditions/` | 特殊条件总览 |
| 08-01 | `rnafold-08-01-ligand` | `rnafold-08-special-conditions/rnafold-08-01-ligand/` | 配体/ motif 约束 |
| 08-02 | `rnafold-08-02-modifications` | `rnafold-08-special-conditions/rnafold-08-02-modifications/` | 修饰核苷酸 |
| 08-03 | `rnafold-08-03-nonstandard` | `rnafold-08-special-conditions/rnafold-08-03-nonstandard/` | 非标准温度/盐浓度 |
| 08-04 | `rnafold-08-04-noncanonical` | `rnafold-08-special-conditions/rnafold-08-04-noncanonical/` | 非标准碱基配对 |
| 09 | `rnafold-09-batch` | `rnafold-09-batch/` | 批量处理 |
| 11 | `rnafold-11-benchmark` | `rnafold-11-benchmark/` | 基准测试模式 |
| 12 | `rnafold-12-local` | `rnafold-12-local/` | 局部碱基配对距离限制 |

> 注：场景 10（Boltzmann 抽样）未包含在本次技能集合中。

## 环境安装

所有子技能共享同一 conda 环境，配置文件位于本目录：

```yaml
# rnafold_skills/environment.yml
name: rnafold
channels:
  - bioconda
  - conda-forge
  - defaults
dependencies:
  - python=3.12
  - viennarna=2.7.2
```

安装并激活：

```bash
conda env create -f rnafold_skills/environment.yml
conda activate rnafold
```

## 通用输入格式

RNAfold 默认读取 FASTA 格式：

```text
>sequence_name
GGGAAACCCACCUUUGGGAAACCC
```

- 单文件可包含多条序列。
- 场景 06 需要额外提供 SHAPE `.dat` 文件。
- 场景 11 必须在序列后紧跟参考结构行。

## 如何选择技能

| 需求 | 技能 |
|------|------|
| 单条/多条序列的 MFE 结构 | `rnafold-01-basic-mfe` |
| 碱基配对概率、ensemble 多样性 | `rnafold-02-partition` |
| 比 MFE 更稳健的结构估计 | `rnafold-03-mea` |
| circRNA | `rnafold-04-circ` |
| 强制某些碱基配对/不配对 | `rnafold-05-constraint` |
| 有 SHAPE 反应性数据 | `rnafold-06-shape` |
| G-四链体 | `rnafold-07-gquad` |
| 配体、修饰、非标准条件/配对 | `rnafold-08-special-conditions` 及其子技能 |
| 一次处理多条序列 | `rnafold-09-batch` |
| 与参考结构对比评估 | `rnafold-11-benchmark` |
| 长序列局部配对限制 | `rnafold-12-local` |
