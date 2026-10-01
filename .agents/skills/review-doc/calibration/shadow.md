# Shadow record

One row per human finding on a slice-1 doc PR. The fields and when `in_top_k` is filled are in
[RULES.md](RULES.md#shadow-record). The switch rule that reads this table is in
[ADR-0003](../../../../docs/adr/0003-doc-contract-delivery-reviewer.md).

| PR | Doc | Head SHA | Finding | Blocking | Source | In top k |
|----|-----|----------|---------|----------|--------|----------|
