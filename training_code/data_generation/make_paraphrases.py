"""Synthetic phrasing variants of TRAIN questions (allowed: self-generated data from train).

The model sees only the question text and a style instruction, never answers. Each
variant keeps the original shop and as_of_date, so the train reference answer still
applies; variants that change any number/date token are dropped. Prompts and raw
responses are stored for reproducibility.

Usage: python training_code/data_generation/make_paraphrases.py --per-shop 25 --out data/synthetic/paraphrases_v1
"""
import argparse
import asyncio
from collections import defaultdict
import json
from pathlib import Path
import random
import re
import sys

from pydantic import BaseModel, ConfigDict, Field

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))
from datamate.config import ModelSettings  # noqa: E402
from datamate.parsing import fold  # noqa: E402
from datamate.provider import Provider  # noqa: E402

PROMPT_VERSION = "paraphrase-v1"
STYLES = {
    "spoken": "văn nói thân mật của chủ quán, có từ đệm (nhé, nha, ha, vậy) và có thể dùng từ địa phương (nhiêu, bữa, dzậy, hông, rứa)",
    "nodiacritics": "bỏ HẾT dấu tiếng Việt và viết tắt phổ biến (dt hoặc dthu cho doanh thu, sl cho số lượng, ko, dc, bn cho bao nhiêu, sp cho sản phẩm, kh cho khách)",
    "telex": "giữ dấu phần lớn nhưng 2-3 từ bị gõ telex chưa bật dấu (vd: thangs, nawm, bao nhieeu, hangf), thiếu dấu câu",
    "reorder": "đổi trật tự câu (đưa mốc thời gian hoặc đối tượng ra trước/sau) và dùng từ đồng nghĩa (doanh thu = tiền bán được = thu về; đơn = hoá đơn = bill; khách = người mua)",
    "context": "thêm một câu ngữ cảnh ngắn phía trước không làm đổi ý (vd: 'Anh đang làm sổ sách, cho anh hỏi chút')",
}
SYSTEM = ("Bạn viết lại MỘT câu hỏi về số liệu bán hàng theo phong cách được yêu cầu. Giữ nguyên ý nghĩa nghiệp vụ và đáp án: "
          "giữ đúng mọi con số, ngày tháng, mốc thời gian (hôm nay, hôm qua, tuần trước, tháng 8...), tên hàng, tên khách, tên nhân viên, tên nhóm, mã. "
          "Không thêm hay bỏ điều kiện, không đổi kiểu đáp án (một số, phần trăm, danh sách, tên...). Doanh thu có thể gọi là tiền bán được/thu về, "
          "nhưng không được đổi thành tiền khách đã trả. Chỉ trả JSON.")


class Paraphrase(BaseModel):
    model_config = ConfigDict(extra="forbid")
    question: str = Field(max_length=400)


def numbers(text):
    return sorted(re.findall(r"\d+", fold(text)))


async def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--per-shop", type=int, default=25)
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--concurrency", type=int, default=8)
    args = parser.parse_args()
    if args.out.exists():
        raise ValueError("Choose a new output directory")
    args.out.mkdir(parents=True)
    rows = [json.loads(line) for line in open("train/questions.jsonl")]
    by_shop = defaultdict(list)
    for row in rows:
        by_shop[row["business_id"]].append(row)
    rng = random.Random(args.seed)
    chosen = [q for shop in sorted(by_shop) for q in rng.sample(by_shop[shop], min(args.per_shop, len(by_shop[shop])))]
    styles = list(STYLES)
    settings = ModelSettings.from_env(Path(".env"), temperature=0.7, max_tokens=400)
    provider = Provider(settings)
    semaphore = asyncio.Semaphore(args.concurrency)

    async def one(index, question):
        style = styles[index % len(styles)]
        messages = [{"role": "system", "content": SYSTEM},
                    {"role": "user", "content": f"Phong cách: {STYLES[style]}\nCâu gốc: {question['question']}"}]
        async with semaphore:
            parsed, call = await provider.generate(messages, Paraphrase)
        variant = parsed.question.strip() if parsed else ""
        keep = bool(variant) and numbers(variant) == numbers(question["question"]) and fold(variant) != fold(question["question"])
        return {"source_id": question["id"], "business_id": question["business_id"], "as_of_date": question["as_of_date"], "style": style,
                "original": question["question"], "variant": variant, "kept": keep,
                "prompt": messages, "response": call.get("content"), "usage": call.get("usage"), "error": call.get("error")}

    try:
        records = await asyncio.gather(*(one(i, q) for i, q in enumerate(chosen)))
    finally:
        await provider.close()
    (args.out / "generation.jsonl").write_text("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in records))
    kept = [r for r in records if r["kept"]]
    questions = [{"id": "p_" + r["source_id"], "business_id": r["business_id"], "question": r["variant"], "as_of_date": r["as_of_date"]} for r in kept]
    (args.out / "questions.jsonl").write_text("".join(json.dumps(q, ensure_ascii=False) + "\n" for q in questions))
    originals = [{"id": r["source_id"], "business_id": r["business_id"], "question": r["original"], "as_of_date": r["as_of_date"]} for r in kept]
    (args.out / "originals.jsonl").write_text("".join(json.dumps(q, ensure_ascii=False) + "\n" for q in originals))
    meta = {"prompt_version": PROMPT_VERSION, "model": settings.model, "temperature": settings.temperature, "seed": args.seed, "per_shop": args.per_shop,
            "generated": len(records), "kept": len(kept), "styles": {s: sum(r["style"] == s and r["kept"] for r in records) for s in styles},
            "tokens": sum((r["usage"] or {}).get("total_tokens", 0) for r in records)}
    (args.out / "meta.json").write_text(json.dumps(meta, ensure_ascii=False, indent=1))
    print(json.dumps(meta, ensure_ascii=False))


if __name__ == "__main__":
    asyncio.run(main())
