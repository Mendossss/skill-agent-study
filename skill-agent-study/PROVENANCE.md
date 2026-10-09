# Source and experiment record

The unmodified `dialogue-graph` skill and reference setup fixtures come from
[SkillsBench](https://github.com/benchflow-ai/skillsbench), commit
`55bfe693f2a19f6b2f29aca3f54fe98b9d994668`. The original skill author is
scriptwonder; the Apache-2.0 license is included. The graph-audit task and its
checks are an adaptation, not an original SkillsBench benchmark score.

The recorded batch is `20261009-183344-d622`. Native runs were performed on
Jiacheng Chen's Mac. Tooling, analysis and documentation received AI assistance.
The three programs inside the result archive are outputs of the tested models.
They are preserved without repairs.

The compact source removes unused pilot scheduling and uses repository-relative
paths. Task, graph input, expected results, functional checker and helper observer
remain byte-identical to the final recorded experiment. The original complete
controller remains in `results/raw.zip`; its source hashes and metadata identify
the recorded run independently of this source reorganization.

SHA-256 of `results/raw.zip`:

```text
64176ad887c1bc4fbade851fc7e11f2db2bb0e2326b9663aede316cf90eee6ab
```

Model digests:

- 9B: `d63f830f28d515fefea546f213fca7a75e9dd5176ef51a6deabc952d1c99a911`
- 27B: `2d2e4b8fc7c0479b70f8cba9fc8bdb49a3c9a43a8a6da3873b724c9fca4672ef`

No seed was fixed. Model-show reports sizes 9.7B and 27.4B, MLX/NVFP4;
loaded snapshots show context 65,536. Defaults include temperature 1,
top-p .95 and top-k 20. 9B also lists `draft_num_predict=3`.
Each condition has one observation. Earlier task/candidate searches remain
separate history, summarized in `results/search.csv`; full earlier exports
are retained in the research submission archive.
