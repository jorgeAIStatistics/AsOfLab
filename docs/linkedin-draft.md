# LinkedIn launch post: draft

**Status: draft. Not posted.** Posting needs Jorge's decision. Every number
below is in [results.md](results.md) and listed in
[claims-ledger.md](claims-ledger.md); recheck them against those files before
posting, because they change if the results are regenerated.

The repository link is real and public. The article link points to a file in
that repository; if the article is published elsewhere, replace it.

---

A blood sample is drawn at 08:00. The lab releases the result at 11:00. A model
makes a prediction at 09:00.

If you build the training data by asking "what was measured before 09:00?",
the model gets a number nobody had for another two hours. Add later corrections
and retractions and the question gets harder: what did this pipeline actually
know at that moment, and does getting it wrong change the evaluation?

I built AsOfLab to answer that on my own laptop. It is an open-source workbench
that:

- writes the time rules down as a versioned contract (event time, source
  availability, pipeline availability, revision order);
- implements them three independent ways (a plain reference, a DuckDB query, an
  incremental replay with checkpoints) and requires all three to agree;
- ships 22 handwritten cases with known answers, plus deliberately broken
  implementations to prove the cases can fail;
- measures the effect with a bootstrap that resamples patients, not rows.

One finding, on simulated data: across five simulated cohorts of 3,000
synthetic patients with delays, corrections and delayed outcomes, the naive
pipeline read something it could not have had in 46% of predictions. Scoring
the same frozen model both ways moved AUROC by +0.002 on average, and in all
five cohorts the 95% interval included zero.

So in that simulation, contamination was common and its effect on
discrimination was small. Label timing mattered more: recording labels the
moment the horizon ended would have recorded about 65% of the positive labels
as negatives.

The limitation that matters: this is synthetic. How large the effect is in a
real system depends on how informative the delayed data are, and a simulation
cannot tell you that. What carries over is the method for finding out. It is
also not a general leakage detector: timestamps cannot tell you whether using a
measurement is clinically appropriate.

The replay design was inspired by Stefan Vercillo's article "Parallelizing
High-Lag Backfills with Flink". AsOfLab is a local simulator and makes no
comparison with Flink.

Code, method and every result file:
https://github.com/jorgeAIStatistics/AsOfLab

Write-up:
https://github.com/jorgeAIStatistics/AsOfLab/blob/main/docs/article.md

---

## Notes for whoever posts this

- Do not add a benchmark multiple, a data volume, a production user, a patient
  benefit, or a validation claim. None exists.
- Mentioning the inspiring article is credit, not collaboration. Do not tag its
  author in a way that implies endorsement.
- If the results are regenerated and the numbers move, update the two
  paragraphs under "One finding" from `docs/results.md`.
