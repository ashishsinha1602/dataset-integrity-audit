"""Resolve sampled GitHub repo slugs via gh GraphQL, batched.

Classification:
  alive      -> repository object returned
  dead       -> GraphQL error of type NOT_FOUND for that alias (explicit 404 equivalent)
  unresolved -> anything else (rate limit, 5xx, network, timeout, unexpected error type)

Results appended to results.jsonl so the run is resumable.
"""
import json, subprocess, sys, os, time, re
import pandas as pd
from concurrent.futures import ThreadPoolExecutor

GH = r"C:\Program Files\GitHub CLI\gh.exe"
OUT = "results.jsonl"
BATCH = 50
WORKERS = 3          # modest concurrency
SLEEP = 0.35         # polite pause per request

SLUG_RE = re.compile(r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$")


def build_query(chunk):
    parts = []
    for i, slug in enumerate(chunk):
        owner, name = slug.split("/", 1)
        parts.append(
            f'  a{i}: repository(owner:"{owner}", name:"{name}") '
            f'{{ nameWithOwner isArchived isFork stargazerCount createdAt pushedAt }}'
        )
    return "{\n" + "\n".join(parts) + "\n  rateLimit { cost remaining }\n}"


def run_batch(chunk):
    q = build_query(chunk)
    try:
        p = subprocess.run([GH, "api", "graphql", "-f", f"query={q}"],
                           capture_output=True, text=True, timeout=180)
        raw = p.stdout.strip()
    except Exception as e:
        return [{"slug": s, "status": "unresolved", "reason": f"exec:{type(e).__name__}"} for s in chunk]

    if not raw:
        err = (p.stderr or "").strip()[:200]
        return [{"slug": s, "status": "unresolved", "reason": f"empty:{err}"} for s in chunk]
    try:
        j = json.loads(raw)
    except Exception:
        return [{"slug": s, "status": "unresolved", "reason": "badjson"} for s in chunk]

    data = j.get("data") or {}
    # map alias -> error type
    errs = {}
    for e in j.get("errors", []) or []:
        path = e.get("path") or []
        if path and isinstance(path[0], str):
            errs[path[0]] = e.get("type") or "UNKNOWN_ERR"
        else:
            # global error (rate limit / auth) -> whole batch unresolved
            return [{"slug": s, "status": "unresolved",
                     "reason": "global:" + str(e.get("type") or e.get("message", ""))[:120]} for s in chunk]

    out = []
    for i, slug in enumerate(chunk):
        a = f"a{i}"
        node = data.get(a)
        if node:
            out.append({"slug": slug, "status": "alive",
                        "resolved": node.get("nameWithOwner"),
                        "archived": node.get("isArchived"),
                        "fork": node.get("isFork"),
                        "stars": node.get("stargazerCount"),
                        "pushed": node.get("pushedAt")})
        elif errs.get(a) == "NOT_FOUND":
            out.append({"slug": slug, "status": "dead", "reason": "NOT_FOUND"})
        else:
            out.append({"slug": slug, "status": "unresolved",
                        "reason": errs.get(a, "null_no_error")})
    return out


def main():
    only = sys.argv[1] if len(sys.argv) > 1 else None
    s = pd.read_parquet("sample.parquet")
    todo = [x for x in s["slug"].tolist() if SLUG_RE.match(x)]
    print("sample slugs valid:", len(todo), "of", len(s))

    done = {}
    if os.path.exists(OUT):
        with open(OUT) as f:
            for line in f:
                try:
                    r = json.loads(line)
                    done[r["slug"]] = r["status"]
                except Exception:
                    pass
    if only == "retry":
        # re-run only the previously unresolved
        todo = [x for x in todo if done.get(x) in (None, "unresolved")]
    else:
        todo = [x for x in todo if x not in done]
    print("to resolve:", len(todo))
    if not todo:
        return

    chunks = [todo[i:i + BATCH] for i in range(0, len(todo), BATCH)]
    fh = open(OUT, "a", buffering=1)
    n = 0
    with ThreadPoolExecutor(max_workers=WORKERS) as ex:
        for res in ex.map(lambda c: (time.sleep(SLEEP), run_batch(c))[1], chunks):
            for r in res:
                fh.write(json.dumps(r) + "\n")
            n += len(res)
            if n % 1000 < BATCH:
                print(f"  {n}/{len(todo)}", flush=True)
    fh.close()
    print("done", n)


if __name__ == "__main__":
    main()
