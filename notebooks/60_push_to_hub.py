# Pulls the two gold tables out of DuckLake and pushes each one back to
# Hugging Face as its own dataset repo, closing the loop: data came in
# from HF (COCO, VisDrone), got cleaned and curated, now goes back out.

import duckdb
from datasets import Dataset

HF_USERNAME = "shalyyy"   # your Hugging Face username


def attach_lakehouse():
    con = duckdb.connect()
    con.execute(open("sql/00_attach.sql").read())
    return con


# pulls a gold table into a pandas DataFrame, then wraps it as a proper
# HF Dataset object and pushes it to a new repo under your account
def push_table_to_hub(con, table_name, repo_name):
    df = con.sql(f"SELECT * FROM {table_name}").df()
    print(f"{table_name}: {len(df)} rows, columns: {list(df.columns)}")

    ds = Dataset.from_pandas(df)
    repo_id = f"{HF_USERNAME}/{repo_name}"

    print(f"Pushing to {repo_id}...")
    ds.push_to_hub(repo_id)
    print(f"Done: https://huggingface.co/datasets/{repo_id}")


def main():
    con = attach_lakehouse()

    push_table_to_hub(con, "gold.coco_training", "ai-lakehouse-coco")
    push_table_to_hub(con, "gold.visdrone_training", "ai-lakehouse-visdrone")


if __name__ == "__main__":
    main()
