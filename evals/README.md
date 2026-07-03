# Evals — Skill Quality Measurement

Lightweight eval suite based on the Karpathy AutoResearch pattern.
Measures whether self-improve changes help or hurt skill quality.

## How it works

Each skill has an eval file (`evals/<skill-name>.yaml`) with test cases.
Each test case defines:
- **input**: what triggers the skill
- **checks**: pass/fail criteria (presence of outputs, memory writes, etc.)

## Running evals

```bash
# Run all evals
python evals/run_evals.py

# Run single skill eval
python evals/run_evals.py --skill nightly-research

# Filter checks to a specific project
python evals/run_evals.py --project jarvis
```

Results are printed to stdout. Future: save to Supabase for trend tracking.

## Adding evals

1. Create `evals/<skill-name>.yaml`
2. Define 5-10 representative test cases
3. Run baseline before any changes
4. After changes: re-run, compare

Score trend matters more than absolute value.
