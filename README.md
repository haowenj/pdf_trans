# MinerU Content Cleaner

上传一个 PDF 到 MinerU 3.4.4 异步接口，下载解析结果，并清洗其中的
content list。

## 环境

- Python 3.11+
- MinerU 3.4.4 服务；默认地址为 `http://127.0.0.1:7100`

## 安装

```bash
python -m venv .venv
source .venv/bin/activate
python -m pip install -e .
```

## 使用

```bash
python -m mineru_cleaner /path/to/document.pdf
```

连接其他地址的 MinerU：

```bash
python -m mineru_cleaner /path/to/document.pdf \
  --svr-url http://mineru.example:7100
```

MinerU 结果解压到项目的 `data/` 目录。清洗结果保存在原始 content list
同目录的 `cleaned_content_list.json` 中。命令输出处理前数量、过滤数量、
处理后数量和结果文件路径。

程序只删除以下内容：

- `header`
- `footer`
- `page_number`
- `type` 为 `text` 且 `text.strip()` 为空的条目

其他条目按原顺序、原字段保留。

## 测试

```bash
python -m pip install -e '.[test]'
pytest -v
```
