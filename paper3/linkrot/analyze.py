import json, pandas as pd, numpy as np

recs = [json.loads(l) for l in open("results.jsonl")]
res = pd.DataFrame(recs).drop_duplicates("slug", keep="last")
s = pd.read_parquet("sample_all.parquet")
df = s.merge(res, on="slug", how="left")
df["status"] = df["status"].fillna("unresolved")

print("=" * 66)
print("SAMPLE SIZE:", len(df), " resolved records:", res["status"].notna().sum())
tot = df["status"].value_counts()
print(tot.to_string())
print()
den = (df["status"] != "unresolved").sum()
dead = (df["status"] == "dead").sum()
print(f"Dead (explicit NOT_FOUND/404): {dead}")
print(f"Alive:                         {(df['status']=='alive').sum()}")
print(f"Unresolved (excluded):         {(df['status']=='unresolved').sum()}")
print(f"DEAD RATE (of resolved {den}): {100*dead/den:.2f}%")
lo = 1.96 * np.sqrt((dead / den) * (1 - dead / den) / den) * 100
print(f"   95% CI +/- {lo:.2f} pp")
print()

# archived among alive
al = df[df["status"] == "alive"]
print(f"Of alive repos, archived (read-only): {al['archived'].sum():.0f} "
      f"({100*al['archived'].mean():.2f}%)")
print()

print("=" * 66)
print("AGE GRADIENT - cohort by year of earliest citing paper")
print(f"{'year':>6} {'n_resolved':>11} {'dead':>6} {'dead%':>7} {'95%CI':>8} {'unres':>6} {'archived%':>10}")
g = df.copy()
g["yr"] = g["year"].clip(lower=2013)
rows = []
for yr, sub in g.groupby("yr"):
    r = sub[sub["status"] != "unresolved"]
    n = len(r)
    if n == 0:
        continue
    d = (r["status"] == "dead").sum()
    p = d / n
    ci = 1.96 * np.sqrt(p * (1 - p) / n) * 100
    a = r[r["status"] == "alive"]
    arch = 100 * a["archived"].mean() if len(a) else float("nan")
    lbl = "<=2013" if yr == 2013 else str(int(yr))
    print(f"{lbl:>6} {n:>11} {d:>6} {100*p:>6.2f}% {ci:>7.2f} "
          f"{(sub['status']=='unresolved').sum():>6} {arch:>9.1f}%")
    rows.append({"year": lbl, "n": n, "dead": d, "dead_pct": 100 * p, "ci": ci, "archived_pct": arch})

pd.DataFrame(rows).to_csv("cohort_table.csv", index=False)

# monotonicity + trend test on 2014-2024 (2025 partial)
t = pd.DataFrame(rows)
t = t[t["year"].str.isdigit()]
t["yi"] = t["year"].astype(int)
tt = t[(t["yi"] >= 2014) & (t["yi"] <= 2024)]
print()
print("Spearman rho (year vs dead%), 2014-2024:",
      round(tt["yi"].corr(tt["dead_pct"], method="spearman"), 3))
print("dead% 2014:", round(tt[tt.yi == 2014]["dead_pct"].iloc[0], 2),
      "  dead% 2024:", round(tt[tt.yi == 2024]["dead_pct"].iloc[0], 2))

# reason breakdown for unresolved
print()
un = df[df["status"] == "unresolved"]["reason"].value_counts().head(10)
print("unresolved reasons:"); print(un.to_string() if len(un) else "  (none)")
