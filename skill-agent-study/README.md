# Skill use in local coding agents

A small study of dialogue-graph auditing with Codex and local Qwen models.
The task checks edge endpoints, exports valid graphs as JSON, and renders
Graphviz SVGs. With-skill conditions receive the original `dialogue-graph`
package from SkillsBench.

[Report](paper/report.pdf) · [Source and experiment record](PROVENANCE.md)

## Results

One fresh run per condition, using the same prompt, inputs and checker:

| Model | Skill | Checks | Time (s) | Shell commands |
|---|---|---:|---:|---:|
| Qwen3.5 9B | Yes | 10/10 | 39.033 | 21 |
| Qwen3.5 27B | No | 10/10 | 126.039 | 10 |
| Qwen3.5 27B | Yes | 10/10 | 178.625 | 15 |

Both supplied-skill programs use the original validator and renderer.
The 9B program exports JSON with the standard library; the 27B program also
uses `Graph.to_json`. These are observed model outputs, not corrected answers.
There is no 9B without-skill trial or repetition, so the results establish
one selected case rather than a causal improvement from the skill.

## Recheck the recorded run

Python 3.10+; no extra packages are needed for offline rechecking:

```bash
python3 analysis/recheck.py results/raw.zip rechecked
```

`results/raw.zip` preserves the complete original export, including programs,
outputs, events, checks and the exact runner source. `results/search.csv`
summarizes earlier attempts. Rechecking produces `rechecked/summary.json`.

## Run a new experiment

The native runner targets arm64 macOS with Codex, Ollama and Graphviz installed.
The recorded versions are Codex 0.161.0, Ollama 0.40.1, Python 3.14.6 and
Graphviz 16.1.0. Keep the repository outside protected Downloads/Documents
folders. With the local Ollama server running:

```bash
ollama pull qwen3.5:27b
python3 run.py setup
python3 run.py
```

Setup creates a local environment and checks sandboxing and reference outputs.
The experiment downloads 9B if needed, then attempts all three conditions once,
with 360 seconds per model turn. New logs and exports go to `runs/`.
`harness/` retains the runtime and setup checks; `experiment/` contains the
final task, checker and controller. The source is reorganized for reproduction;
the archived source identifies the exact implementation used for the table.

The models report MLX/NVFP4 with a loaded context of 65,536. Only the 9B
configuration lists `draft_num_predict=3`; timings include loading and are not
an isolated comparison of parameter count. See [PROVENANCE.md](PROVENANCE.md)
for source, configuration and assistance details.
