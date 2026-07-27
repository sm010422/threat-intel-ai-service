"""RAGAS로 threat-intel-ai-service의 RAG 응답 품질을 평가한다.

정답(ground truth) 라벨링 없이 바로 돌릴 수 있는 reference-free 지표 두 개만 쓴다:
- faithfulness: 생성된 답변이 검색된 컨텍스트에서 벗어나 지어낸(hallucinated) 내용이
  있는지 (0~1, 높을수록 컨텍스트에 충실)
- answer_relevancy: 답변이 실제로 질문에 대한 답인지 (0~1, 높을수록 질문과 관련성 높음)

/chat 엔드포인트를 실제로 SSE로 호출해서 answer와 sources(=contexts)를 모으고,
그 결과를 RAGAS에 넘긴다 -- 프롬프트나 모델을 흉내내지 않고 실제 운영 중인
파이프라인을 그대로 평가 대상으로 삼는다.

사용법:
    pip install -r eval/requirements.txt
    kubectl port-forward -n c4i svc/threat-intel-ai-service 18000:8000 &
    python eval/evaluate_rag.py --base-url http://localhost:18000
"""

import argparse
import asyncio
import json
import os

import httpx
from datasets import Dataset
from langchain_google_genai import ChatGoogleGenerativeAI, GoogleGenerativeAIEmbeddings
from ragas import evaluate
from ragas.metrics import answer_relevancy, faithfulness
from ragas.run_config import RunConfig

EVAL_QUESTIONS = [
    "저고도 자폭 드론이 접근할 때 대응 절차가 어떻게 되나?",
    "군집(스웜) 형태로 드론이 접근하면 왜 위험한가?",
    "과거에 DRONE-001 표적이 탐지된 이력이 있었나?",
]

# Gemini 무료 티어는 분당 요청 수(RPM)가 낮다. RAGAS는 기본적으로 최대 16개
# worker로 병렬 호출하는데, 그러면 곧바로 429(ResourceExhausted)에 걸린다.
# max_workers=1로 완전히 직렬화하고, 재시도 간격/횟수를 넉넉히 잡아서
# rate limit을 자연스럽게 흡수하게 한다 -- 느리지만 확실하게 끝난다.
JUDGE_RUN_CONFIG = RunConfig(max_workers=1, max_wait=30, max_retries=6, timeout=300)


async def collect_sample(client: httpx.AsyncClient, base_url: str, question: str) -> dict:
    answer_parts: list[str] = []
    contexts: list[str] = []

    async with client.stream("POST", f"{base_url}/chat", json={"question": question}) as resp:
        resp.raise_for_status()
        buffer = ""
        async for chunk in resp.aiter_text():
            buffer += chunk
            while "\n\n" in buffer:
                block, buffer = buffer.split("\n\n", 1)
                event_name = "message"
                data_line = None
                for line in block.split("\n"):
                    if line.startswith("event:"):
                        event_name = line[len("event:") :].strip()
                    elif line.startswith("data:"):
                        data_line = line[len("data:") :].strip()
                if data_line is None:
                    continue
                payload = json.loads(data_line)
                if event_name == "sources":
                    contexts = [s["text"] for s in payload]
                elif isinstance(payload, dict) and "token" in payload:
                    answer_parts.append(payload["token"])

    return {
        "question": question,
        "answer": "".join(answer_parts) or "(응답 없음)",
        "contexts": contexts or ["(검색된 컨텍스트 없음)"],
    }


async def collect_all(base_url: str) -> list[dict]:
    async with httpx.AsyncClient(timeout=60) as client:
        return [await collect_sample(client, base_url, q) for q in EVAL_QUESTIONS]


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-url", default="http://localhost:18000")
    args = parser.parse_args()

    if not os.environ.get("GEMINI_API_KEY"):
        raise SystemExit(
            "GEMINI_API_KEY가 필요합니다 (judge 모델용). "
            "export GEMINI_API_KEY=<target-tracking-secrets와 동일한 키> 후 재실행하세요."
        )
    # langchain-google-genai는 GOOGLE_API_KEY 환경변수를 읽는다.
    os.environ.setdefault("GOOGLE_API_KEY", os.environ["GEMINI_API_KEY"])

    print(f"[1/3] {args.base_url}/chat 을 {len(EVAL_QUESTIONS)}개 질문으로 실제 호출 중...")
    samples = asyncio.run(collect_all(args.base_url))
    dataset = Dataset.from_list(samples)

    print("[2/3] RAGAS 평가 실행 중 (judge: gemini-flash-latest)...")
    judge_llm = ChatGoogleGenerativeAI(model="gemini-flash-latest")
    judge_embeddings = GoogleGenerativeAIEmbeddings(model="models/gemini-embedding-001")

    result = evaluate(
        dataset,
        metrics=[faithfulness, answer_relevancy],
        llm=judge_llm,
        embeddings=judge_embeddings,
        run_config=JUDGE_RUN_CONFIG,
    )

    print("[3/3] 결과")
    df = result.to_pandas()
    with __import__("pandas").option_context("display.max_colwidth", 40):
        print(df[["question", "faithfulness", "answer_relevancy"]].to_string(index=False))

    out_path = "eval/last_run.json"
    df.to_json(out_path, orient="records", force_ascii=False, indent=2)
    print(f"\n전체 결과(답변/컨텍스트 포함) → {out_path}")


if __name__ == "__main__":
    main()
