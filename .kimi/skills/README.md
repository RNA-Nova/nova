# RNAfold & tRNAscan-SE & R2DT 示例测试项目

本项目根据飞书文档整理了三个生物信息学工具（ViennaRNA `RNAfold`、`tRNAscan-SE` 和 `R2DT`）的常见使用场景。
目录按 **算法 → 场景 →（可选子场景）** 组织，每个叶子目录自包含，包含该示例的输入文件和可独立运行的脚本。

## 项目结构

```text
.
├── RNAfold/                    # ViennaRNA / RNAfold 示例
│   ├── 01_basic_mfe/
│   │   ├── inputs/             # 该场景的输入文件
│   │   └── run.sh              # 单场景运行脚本
│   ├── 02_partition/
│   │   ├── inputs/
│   │   └── run.sh
│   ├── 08_special_conditions/  # 某一场景下有多个子场景时，使用子目录
│   │   ├── ligand/
│   │   │   ├── inputs/
│   │   │   └── run.sh
│   │   ├── modifications/
│   │   ├── nonstandard/
│   │   └── noncanonical/
│   └── ...
├── tRNAscan-SE/                # tRNAscan-SE 示例
│   ├── 01_prokaryotic/         # 某一场景下有多种模式时，使用子目录
│   │   ├── bacteria/
│   │   │   ├── inputs/
│   │   │   └── run.sh
│   │   └── archaea/
│   │       ├── inputs/
│   │       └── run.sh
│   ├── 02_eukaryotic/
│   │   ├── inputs/
│   │   └── run.sh
│   └── ...
├── r2dt/                       # R2DT 示例（基于 Docker）
│   ├── 01_single_sequence_auto/
│   │   ├── inputs/
│   │   └── run.sh
│   ├── 02_batch_visualization/
│   │   ├── inputs/
│   │   └── run.sh
│   ├── 03_family_specific/     # 含 rfam / crw / ribovision_lsu / gtrnadb 子场景
│   │   └── rfam/
│   │       ├── inputs/
│   │       └── run.sh
│   ├── 04_template_free/
│   │   ├── basic/
│   │   │   ├── inputs/
│   │   │   └── run.sh
│   │   └── auto/
│   │       ├── inputs/
│   │       └── run.sh
│   └── ...
├── environment.yml             # conda 环境依赖（RNAfold / tRNAscan-SE）
├── run_all.sh                  # 一键运行所有场景
└── README.md                   # 本文件
```

每个场景（或子场景）运行后会在其目录下生成 `outputs/`，里面保存标准输出、标准错误以及工具产生的其他结果文件。

## R2DT 场景列表

R2DT 部分对应飞书文档《R2DT》中的 10 个功能场景：

| 场景 | 目录 | 核心命令 |
|---|---|---|
| 单序列自动可视化 | `r2dt/01_single_sequence_auto` | `r2dt.py draw` |
| 批量序列可视化 | `r2dt/02_batch_visualization` | `r2dt.py draw`（多序列 FASTA） |
| 特定 RNA 家族可视化 | `r2dt/03_family_specific/*` | `r2dt.py rfam/crw/ribovision/gtrnadb draw` |
| 无模板可视化 | `r2dt/04_template_free/*` | `r2dt.py templatefree` |
| 病毒基因组结构注释 | `r2dt/05_viral_genome` | `r2dt.py stockholm --color-by region` |
| 插入区域约束折叠 | `r2dt/06_constraint_folding/*` | `r2dt.py draw --constraint` |
| 多布局引擎比较 | `r2dt/07_layout_engines/*` | `r2dt.py templatefree --auto/--rscape/--rnartist/--rnapuzzler` |
| tRNA 结构可视化 | `r2dt/08_trna/*` | `r2dt.py gtrnadb draw` |
| 数据库标准结构生成 | `r2dt/09_batch_database` | 批量 `r2dt.py draw` 脚本 |
| 发表级图片拼接 | `r2dt/10_stitch/*` | `r2dt.py stitch` |

## 环境安装

本项目依赖通过 conda 管理，请使用以下命令创建环境：

```bash
conda env create -f environment.yml
conda activate tools
```

> 注：RNAfold 和 tRNAscan-SE 均为生物信息学命令行工具，通过 bioconda 分发，因此不使用 `requirements.txt`。

### R2DT 环境

R2DT 通过 Docker 镜像分发，每个 `r2dt/*/run.sh` 会自动拉取 `rnacentral/r2dt` 镜像（如未安装），无需在本地 conda 环境中安装 R2DT。请确保当前系统已安装并启用 Docker：

```bash
docker --version
```

## 运行全部示例

```bash
conda activate tools
./run_all.sh
```

> 注：`run_all.sh` 会依次运行 RNAfold、tRNAscan-SE 和 R2DT 的全部场景。R2DT 场景依赖 Docker，首次运行会自动拉取镜像。

## 单独运行某个场景

进入任意场景（或子场景）目录，直接执行 `run.sh` 即可：

```bash
cd RNAfold/01_basic_mfe
bash run.sh

# 或
cd tRNAscan-SE/01_prokaryotic/bacteria
bash run.sh

# 或
cd r2dt/01_basic_draw
bash run.sh
```

结果保存在该目录的 `outputs/` 下。

## 依赖版本

- ViennaRNA / RNAfold: 2.7.2
- tRNAscan-SE: 2.0.12
- R2DT: 2.2 (Docker 镜像 `rnacentral/r2dt:latest`)

## 说明

- 每个场景的 `outputs/` 由 `run.sh` 自动生成，可随时删除后重新运行。
- RNAfold 场景 11（benchmark）必须使用 FASTA 格式输入（`>name` 开头），否则 RNAfold 2.7.2 会触发 Segmentation fault。
- R2DT 场景在 Docker 容器中运行，生成的 `outputs/` 文件默认由 `root` 拥有。如需以普通用户身份清理或修改输出，可使用 `sudo` 或调整 Docker 运行用户。
- R2DT 场景 `08_pdb` 需要从 RCSB 下载 PDB 结构，首次运行需联网；`11_viral_annotate` 使用截取的病毒基因组片段以加快演示速度。
