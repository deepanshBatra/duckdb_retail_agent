"""
Live accuracy check for the agent: asks real questions and checks the answers
contain the right numbers (and names). Needs GROQ_API_KEY (or OPENAI_API_KEY).

    python eval_agent.py                       # default Groq model
    python eval_agent.py --model qwen/qwen3.8-27b
    python eval_agent.py --provider openai --model gpt-4o-mini
"""

import argparse
import os
import re
import time

from dotenv import load_dotenv
from openai import OpenAI

import agent_core as core

HERE = os.path.dirname(os.path.abspath(__file__))

# (question, expected numbers, expected words). A number passes if any number in the
# answer is within the tolerance; words must appear (case-insensitive).
CASES = [
    ("What were total sales in 2024?", [(1_063_778.23, 1.0)], []),
    ("What was the margin rate in 2023 and in 2024?", [(49.41, 0.01), (49.86, 0.01)], []),
    ("How many units were sold in 2024?", [(11_063, 0.5)], []),
    ("Which division had the biggest margin increase in 2024, and by how many dollars?", [(26_577.25, 1.0)], ["Sports"]),
    ("Which brand had the lowest margin rate in 2024?", [(42.92, 0.01)], ["Fynix"]),
    ("By how many basis points did Quanta's margin rate change from 2023 to 2024?", [(24, 0.6)], []),
    ("What were Fynix's price and cost changes in 2024?", [(6, 0.01), (10, 0.01)], []),
    ("Which store had the biggest percentage drop in margin dollars from 2023 to 2024?", [(14.93, 0.01)], ["OBQH"]),
    ("Which region had the fastest margin growth in 2024?", [(12.03, 0.01)], ["West"]),
    ("How many stores are in the East region?", [(4, 0)], []),
    ("What was the 2024 margin for Team Sports?", [(103_635.07, 1.0)], []),
    ("Which product category had the highest margin rate in 2024?", [(56.15, 0.01)], ["Snacks"]),
    ("How much of the margin change came from the price effect?", [(22_340.88, 1.0)], []),
    ("What share of 2024 margin came from the East and North regions combined?", [(69.63, 0.02)], []),
]

NUM_RE = re.compile(r"-?\d[\d,]*\.?\d*")


def numbers_in(text: str):
    out = []
    for m in NUM_RE.findall(text.replace("−", "-")):
        try:
            out.append(float(m.replace(",", "")))
        except ValueError:
            pass
    return out


def check(text: str, nums, words):
    found = [abs(x) for x in numbers_in(text)]
    missing = [n for n, tol in nums if not any(abs(f - abs(n)) <= tol for f in found)]
    missing += [w for w in words if w.lower() not in text.lower()]
    return missing


def main():
    load_dotenv(os.path.join(HERE, ".env"))
    ap = argparse.ArgumentParser()
    ap.add_argument("--provider", default="groq", choices=["groq", "openai"])
    ap.add_argument("--model", default="openai/gpt-oss-120b")
    args = ap.parse_args()
    if args.provider == "groq":
        client = OpenAI(api_key=os.environ["GROQ_API_KEY"], base_url="https://api.groq.com/openai/v1")
    else:
        client = OpenAI(api_key=os.environ["OPENAI_API_KEY"])

    with open(os.path.join(HERE, "retail_data.csv"), "rb") as f:
        cur = core.build_database(core.load_dataframe(f.read(), "retail_data.csv")).cursor()
    prompt = core.build_system_prompt(core.profile_dataset(cur))

    passed = 0
    for i, (q, nums, words) in enumerate(CASES, 1):
        t0 = time.time()
        try:
            res = core.run_agent(client, args.model, prompt, [{"role": "user", "content": q}], cur)
            missing = check(res.text, nums, words)
            errs = sum(1 for s in res.steps if s.error)
            status = "PASS" if not missing else "FAIL"
            detail = "" if not missing else f"  missing {missing}"
            print(f"{status} [{i:2}] {q}  ({len(res.steps)} steps, {errs} errors, {time.time() - t0:.1f}s){detail}")
            if missing:
                print("      answer:", res.text[:300].replace("\n", " "))
            passed += not missing
        except Exception as e:
            print(f"ERROR [{i:2}] {q}: {e}")
    print(f"\n{args.model}: {passed}/{len(CASES)} correct")


if __name__ == "__main__":
    main()
