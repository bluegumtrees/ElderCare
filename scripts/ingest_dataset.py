"""通用数据集入库脚本。

支持两种输入：
  1. 本地 JSONL / JSON 文件
  2. HuggingFace Hub 数据集（自动用 HF_ENDPOINT 走镜像，国内推荐 https://hf-mirror.com）

支持两种文本形态：
  - QA 对（默认）：从 --question-field 和 --answer-field 取，拼成 "问：...\n答：..."
  - 单文本：从 --text-field 取（适合知识百科类文档）

去重：MD5(text) 去重，避免数据集中相同条目反复入库
过滤：min/max 长度过滤掉噪声（太短无信息、太长难检索）
批处理：默认 256 条一批 embed + 入库，30k 条约 5-10 分钟（CPU）

用法示例：

# 从 HuggingFace 拉医疗数据
python scripts/ingest_dataset.py \\
    --hf-dataset FreedomIntelligence/Huatuo-26M-Lite \\
    --collection health --max-rows 10000

# 从本地 JSONL 拉心理数据
python scripts/ingest_dataset.py \\
    --input data/raw/psyqa.jsonl \\
    --collection psych --question-field question --answer-field answer

# 国内用户先开镜像（PowerShell）
$env:HF_ENDPOINT="https://hf-mirror.com"
"""
import argparse
import hashlib
import json
import os
import sys
from pathlib import Path
from typing import Iterator, Optional

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from tqdm import tqdm  # noqa: E402

from app.vector_store import add_documents  # noqa: E402


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(
        formatter_class=argparse.RawTextHelpFormatter,
        description=__doc__,
    )
    src = p.add_mutually_exclusive_group(required=True)
    src.add_argument("--input", help="本地 JSONL/JSON 文件路径")
    src.add_argument("--hf-dataset", help="HuggingFace 数据集名（如 FreedomIntelligence/Huatuo-26M-Lite）")

    p.add_argument("--hf-split", default="train", help="HF split，默认 train")
    p.add_argument("--collection", required=True, choices=["health", "psych"], help="目标 collection")
    p.add_argument("--question-field", default="question")
    p.add_argument("--answer-field", default="answer")
    p.add_argument("--text-field", default=None, help="如果只有一个文本字段（不是 QA 对），用这个")
    p.add_argument("--topic-field", default=None, help="可选，作为 metadata.topic")
    p.add_argument("--max-rows", type=int, default=None, help="最多入库多少条")
    p.add_argument("--min-len", type=int, default=20, help="过滤短文本（默认 20 字符）")
    p.add_argument("--max-len", type=int, default=2000, help="过滤过长文本（默认 2000 字符）")
    p.add_argument("--batch-size", type=int, default=256, help="批次大小")
    p.add_argument("--id-prefix", default=None, help="ID 前缀，默认取 collection 名前 3 字符")
    p.add_argument("--preview-only", action="store_true", help="只跑读取+清洗，不入库（验数）")
    p.add_argument("--peek", action="store_true", help="打印前 3 行原始字段，不做任何处理（侦察字段名用）")
    p.add_argument("--no-streaming", action="store_true", help="禁用 HF streaming（默认 streaming，避免下载整个大数据集）")
    return p.parse_args()


def iter_rows(args) -> Iterator[dict]:
    """统一的行迭代器，本地或 HF Hub。"""
    if args.input:
        path = Path(args.input)
        if not path.exists():
            raise FileNotFoundError(f"找不到文件：{path}")
        with open(path, encoding="utf-8") as f:
            if path.suffix == ".jsonl":
                for line in f:
                    line = line.strip()
                    if line:
                        try:
                            yield json.loads(line)
                        except json.JSONDecodeError:
                            continue
            else:
                data = json.load(f)
                items = data if isinstance(data, list) else data.get("data", [])
                yield from items
    else:
        try:
            from datasets import load_dataset
        except ImportError as e:
            raise SystemExit("缺少 datasets，请先 pip install datasets") from e
        streaming = not args.no_streaming
        print(f"正在从 HF 拉取 {args.hf_dataset} (split={args.hf_split}, streaming={streaming}) ...")
        ds = load_dataset(args.hf_dataset, split=args.hf_split, streaming=streaming)
        for row in ds:
            yield row


_COMMON_TEXT_KEYS = ("answer_text", "text", "content", "value", "answer", "response", "body")


def _coerce_to_str(value) -> str:
    """递归把 list/dict 规整为单个字符串。

    - 列表：取第一个元素
    - 字典：优先取 answer_text/text/content 等常见键；否则取第一个字符串值
    - 适用于 PsyQA 这种 answers=[{"answer_text": "..."}] 的嵌套格式
    """
    if value is None:
        return ""
    if isinstance(value, list):
        value = value[0] if value else ""
    if isinstance(value, dict):
        for key in _COMMON_TEXT_KEYS:
            v = value.get(key)
            if isinstance(v, str) and v.strip():
                value = v
                break
        else:
            value = next((v for v in value.values() if isinstance(v, str) and v.strip()), "")
    return str(value).strip()


def extract_text(row: dict, args) -> Optional[str]:
    """把一行 row 提炼成一段 text。返回 None 表示丢弃。"""
    if args.text_field:
        val = _coerce_to_str(row.get(args.text_field))
        return val or None

    q = _coerce_to_str(row.get(args.question_field, ""))
    a = _coerce_to_str(row.get(args.answer_field, ""))
    if not q or not a:
        return None
    return f"问：{q}\n答：{a}"


def main() -> None:
    args = parse_args()
    id_prefix = args.id_prefix or args.collection[:3]

    print("=" * 60)
    print(f"  数据集入库")
    print("=" * 60)
    print(f"  源       : {args.input or args.hf_dataset}")
    print(f"  collection: {args.collection}")
    if args.max_rows:
        print(f"  max_rows : {args.max_rows}")
    if args.hf_dataset:
        ep = os.environ.get("HF_ENDPOINT")
        print(f"  HF endpoint: {ep or '(默认 huggingface.co)'}")
    print()

    # --peek 模式：打印前 3 行原始字段就走人
    if args.peek:
        print("--peek 模式：打印前 3 行原始字段，不做任何处理。\n")
        for i, row in enumerate(iter_rows(args)):
            if i >= 3:
                break
            print(f"--- Row {i + 1} ---")
            print(f"  字段名: {list(row.keys())}")
            for k, v in row.items():
                v_str = str(v).replace("\n", " ")
                if len(v_str) > 150:
                    v_str = v_str[:150] + "..."
                print(f"  {k}: {v_str}")
            print()
        print("拿这些字段名去配 --question-field / --answer-field / --text-field。")
        return

    # 第一遍：读取 → 去重 → 长度过滤
    print("[1/2] 读取与清洗...")
    seen: set[str] = set()
    items: list[dict] = []
    skipped_short = skipped_long = skipped_dup = skipped_empty = 0

    for row in tqdm(iter_rows(args), desc="rows", unit=" rows"):
        text = extract_text(row, args)
        if not text:
            skipped_empty += 1
            continue
        if len(text) < args.min_len:
            skipped_short += 1
            continue
        if len(text) > args.max_len:
            skipped_long += 1
            continue
        h = hashlib.md5(text.encode("utf-8")).hexdigest()
        if h in seen:
            skipped_dup += 1
            continue
        seen.add(h)

        meta: dict = {"source": args.hf_dataset or Path(args.input).stem}
        if args.topic_field:
            topic = row.get(args.topic_field)
            if topic:
                meta["topic"] = str(topic)[:100]

        items.append(
            {
                "id": f"{id_prefix}_{h[:14]}",
                "text": text,
                "meta": meta,
            }
        )

        if args.max_rows and len(items) >= args.max_rows:
            break

    print(f"\n  清洗结果：")
    print(f"    保留      : {len(items)}")
    print(f"    去重      : {skipped_dup}")
    print(f"    过滤短文本 : {skipped_short}")
    print(f"    过滤长文本 : {skipped_long}")
    print(f"    字段为空  : {skipped_empty}")

    if not items:
        print("\n没有数据可入库。请检查字段名是否正确（--question-field / --answer-field / --text-field）。")
        return

    # 预览样本
    print(f"\n  样本预览：")
    print(f"    id  : {items[0]['id']}")
    print(f"    meta: {items[0]['meta']}")
    print(f"    text: {items[0]['text'][:200]}{'...' if len(items[0]['text']) > 200 else ''}")
    print()

    if args.preview_only:
        print("--preview-only 已开启，跳过入库。")
        return

    # 第二遍：批量入库
    print(f"[2/2] 批量入库（batch_size={args.batch_size}，含 embedding 计算）...")
    total = len(items)
    for i in tqdm(range(0, total, args.batch_size), desc="batches"):
        batch = items[i : i + args.batch_size]
        add_documents(
            args.collection,
            ids=[b["id"] for b in batch],
            texts=[b["text"] for b in batch],
            metadatas=[b["meta"] for b in batch],
        )

    print(f"\n✓ 完成。collection='{args.collection}' 新增 {total} 条。")


if __name__ == "__main__":
    main()
