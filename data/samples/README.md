# 合同样本

本目录存放测试用合同样本。三个 `.txt` 样本**故意埋入风险条款**（高押金、押金不退、
单方涨租、超长试用期、不缴社保、收取培训费等），用于：

1. 单元测试（`tests/test_rule_engine.py` 直接读取 .txt 切分为条款后跑规则）；
2. 演示（运行 `python scripts/make_sample_docx.py` 将 .txt 生成 .docx，即可在前端上传）。

## 生成可上传的 DOCX

```bash
python scripts/make_sample_docx.py
```

会在 `data/samples/` 下生成 `房屋租赁合同样本.docx` 等文件。

## 收集真实样本（20 份，验收 FR-01）

- 房屋租赁合同：租房 App 的电子合同、房东提供的模板；
- 劳动合同：入职 offer 附件、招聘平台模板；
- 实习协议：校企合作模板、招聘平台实习协议。

> 隐私说明：样本仅用于本地测试，不要提交含真实个人信息的合同到公开仓库。
> 涉及真实信息的样本放入 `data/samples/` 并确保 `.gitignore` 已排除（按需补充）。
