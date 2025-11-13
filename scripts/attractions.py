import pandas as pd
import re
pd.set_option('display.max_rows', None)
df = pd.read_csv("APIS/Attractions.csv")

def clean_tags(raw):
    if not isinstance(raw, str):
        return []
    if "," in raw:
        parts = [p.strip() for p in raw.split(",")]
    else:
        parts = re.split(r'\s{2,}|\s(?=[A-Z])', raw)
    parts = [p.strip() for p in parts if p.strip()]
    return parts

all_tags = []
for t in df["Tags"]:
    all_tags.extend(clean_tags(t))

tag_counts = pd.Series(all_tags).value_counts()
print(tag_counts)
