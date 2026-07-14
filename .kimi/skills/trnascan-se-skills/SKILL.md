---
name: trnascan-se-skills
version: 1.0.0
description: "tRNAscan-SE 场景技能集合索引：覆盖 01、02、05、07、08、09 六个主场景及其子场景。"
metadata:
  requires:
    bins: ["tRNAscan-SE"]
    conda_env: "trnascan-se"
---

# trnascan-se-skills 场景技能集合

本目录把 tRNAscan-SE 的使用场景整理为可复用的 skill。每个子目录即一个独立 skill，目录名与 skill 名完全一致。

## 包含的技能

| 编号 | 技能名 | 目录 | 说明 |
|------|--------|------|------|
| 01 | `trnascan-se-01-prokaryotic` | `trnascan-se-01-prokaryotic/` | 原核生物 tRNA 扫描总览 |
| 01-a | `trnascan-se-01-prokaryotic-archaea` | `trnascan-se-01-prokaryotic/trnascan-se-01-prokaryotic-archaea/` | 古菌模式 `-A` |
| 01-b | `trnascan-se-01-prokaryotic-bacteria` | `trnascan-se-01-prokaryotic/trnascan-se-01-prokaryotic-bacteria/` | 细菌模式 `-B` |
| 02 | `trnascan-se-02-eukaryotic` | `trnascan-se-02-eukaryotic/` | 真核模式 `-E --detail` |
| 05 | `trnascan-se-05-extract-seq-struct` | `trnascan-se-05-extract-seq-struct/` | 序列与结构提取 `-a -b` |
| 07 | `trnascan-se-07-pseudogene` | `trnascan-se-07-pseudogene/` | 假基因检测总览 |
| 07-nc | `trnascan-se-07-pseudogene-no-check` | `trnascan-se-07-pseudogene/trnascan-se-07-pseudogene-no-check/` | 禁用假基因检查 `-D` |
| 07-wc | `trnascan-se-07-pseudogene-with-check` | `trnascan-se-07-pseudogene/trnascan-se-07-pseudogene-with-check/` | 启用假基因检查 `-H` |
| 08 | `trnascan-se-08-isospecific` | `trnascan-se-08-isospecific/` | 同工受体统计 `-s` |
| 09 | `trnascan-se-09-visualization` | `trnascan-se-09-visualization/` | 可视化准备 `-f -a` |

## 环境安装

所有子技能共享同一 conda 环境，配置文件位于本目录：

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

安装并激活：

```bash
conda env create -f trnascan-se-skills/environment.yml
conda activate trnascan-se
```

## 通用输入格式

tRNAscan-SE 默认读取 FASTA 格式：

```text
>sequence_name
AGTCATCGATCGATCGTAGCTAGCTAGCTAGCTAGCTAGCTAGCTAGCTAGCTAGCTAGCTAGC
```

## 如何选择技能

| 需求 | 技能 |
|------|------|
| 古菌 tRNA 扫描 | `trnascan-se-01-prokaryotic-archaea` |
| 细菌 tRNA 扫描 | `trnascan-se-01-prokaryotic-bacteria` |
| 真核 tRNA 扫描 | `trnascan-se-02-eukaryotic` |
| 提取 tRNA 序列和 BED | `trnascan-se-05-extract-seq-struct` |
| 不过滤假基因 | `trnascan-se-07-pseudogene-no-check` |
| 过滤假基因 | `trnascan-se-07-pseudogene-with-check` |
| 统计同工 tRNA | `trnascan-se-08-isospecific` |
| 准备可视化输入 | `trnascan-se-09-visualization` |
