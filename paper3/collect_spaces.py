import csv, time, sys
from huggingface_hub import HfApi
api = HfApi()
FIELDS = ["runtime","sdk","likes","author","createdAt","lastModified","private","tags"]
t0=time.time(); n=0
with open("spaces.csv","w",newline="",encoding="utf-8") as f:
    w=csv.writer(f)
    w.writerow(["id","author","sdk","likes","private","stage","hardware","created_at","last_modified"])
    for s in api.list_spaces(expand=FIELDS):
        rt = getattr(s,"runtime",None)
        stage = rt.stage if rt else ""
        hw = (rt.hardware if rt else "") or ""
        w.writerow([s.id, s.author or "", s.sdk or "", s.likes if s.likes is not None else "",
                    s.private, stage or "", hw,
                    s.created_at.isoformat() if s.created_at else "",
                    s.last_modified.isoformat() if s.last_modified else ""])
        n+=1
        if n % 50000 == 0:
            print(n, round(time.time()-t0,1), "s", flush=True)
print("DONE total_rows", n, round(time.time()-t0,1), flush=True)
