---
name: r2dt-10-stitch-with-captions
version: 1.0.0
description: "R2DT 带标题样式拼接：使用 --captions、--gap 和 --glyph 参数定制发表级图片。"
metadata:
  requires:
    bins: ["docker"]
    docker_image: "rnacentral/r2dt"
---

# r2dt-10-stitch-with-captions

## 概述

使用 `r2dt.py stitch --captions --gap --glyph` 生成带标题、间距和断点样式的发表级组合图。

## 适用场景

- 需要为每个子图添加标题。
- 需要调整子图间距或添加断点标记。

## 前置条件

### 运行环境

R2DT 通过 Docker 镜像 `rnacentral/r2dt` 分发。使用前请确保系统已安装 Docker：

```bash
docker --version
```

首次运行时会自动拉取镜像，也可手动预拉取：

```bash
docker pull rnacentral/r2dt
```

## 输入格式

多个 SVG 文件：

```text
inputs/
├── seq1.svg
├── seq2.svg
└── seq3.svg
```

## 推荐命令

```bash
IMAGE="rnacentral/r2dt"
docker run --rm   -v "$(pwd):/rna/r2dt/temp"   "$IMAGE"   bash -c 'r2dt.py stitch /rna/r2dt/temp/inputs/*.svg -o /rna/r2dt/temp/outputs/publication.svg --captions "5-UTR" --captions "FSE" --captions "3-UTR" --gap 150 --glyph break'   > stdout.txt 2> stderr.txt
```

参数说明：

- `--captions`：每个子图的标题（按顺序对应输入文件）。
- `--gap`：子图之间的间距。
- `--glyph break`：添加断点/间隔标记。

## 输出说明

- `outputs/publication.svg`：带标题和样式的发表级组合 SVG。

## 注意事项

- `--captions` 的数量应与输入 SVG 数量一致。
- `--gap` 单位为像素或相对单位，视 R2DT 版本而定。
