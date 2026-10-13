# Statistical methods

Everything on this page was fixed before the full set of runs was executed.
The three changes made after seeing preliminary output are listed at the end.

All data are simulated. The methods are the ones that would be needed for real
data; the numbers they produce here describe a simulation and nothing else.

## 1. The question and the estimand

**Question.** For one prediction task, how much does measured performance
change when the features are built with timing and revision mistakes?

| Item | Definition |
| --- | --- |
| Target | A simulated deterioration event with event time in `(T, T + 12 h]`, in the same encounter as the prediction at `T` |
| Predictions | Every 4 h from hour 4 of an encounter until it ends |
| Eligible population | Predictions made while the patient is at risk: no earlier event in the encounter |
| Follow-up | Covers the full horizon for every eligible prediction in the main analysis |
| Unit of analysis | The prediction |
| Estimand | AUROC, average precision and Brier score of a fixed model's predictions, for the eligible predictions of held-out patients, under a stated feature-time policy |
| Reference policy | `pipeline_as_known` |

Differences between policies are reported as absolute paired differences with
intervals. Relative percentage changes are not reported.

## 2. Simulation

`src/asoflab/simulation/clinical.py` states the data-generating process in its
docstring and holds every parameter in one class, `ClinicalParameters`.

- A latent severity evolves hourly as a first-order autoregressive process
  around a level set by a patient effect, an age-band effect and an encounter
  effect.
- The outcome is drawn hour by hour from a hazard that rises with severity.
  No feature is a copy of the label; a test checks that no single feature
  separates the label (`test_no_feature_is_a_copy_of_the_label`).
- Four streams (two vital signs, two laboratory values) are measured more often
  when severity is higher, with noise.
- Three mechanisms can be switched on independently: delayed laboratory
  results, corrections and retractions, delayed outcome ascertainment. Two
  structural options: several encounters per patient, and site differences in
  measurement frequency, delay and pipeline cadence.

Seven named scenarios combine the switches (`SCENARIOS`). `negative_control`
has every mechanism off, so every policy must give identical features.

Each patient has separate random streams for traits, encounters, the latent
path, measurement, delays, revisions and outcome ascertainment. Switching a
mechanism on changes only what it controls: scenarios that share a measurement
process have the same patients, paths, measurement times, true values and
outcomes for the same seed.

**Seeds and sizes.** Five seeds, 20261 to 20265, chosen before any run. Full
runs use 3,000 patients. The demonstration uses seed 20261 and 600 patients.
All seven scenarios are run with all five seeds and every run is reported.

**Realism.** The simulation is a device for studying timing. It is not a
validated physiological model, the stream names are for readability, and its
output is not a clinical risk estimate.

## 3. Labels

Section 8 of the [temporal contract](temporal-contract.md) defines the label
and its three ways of being unknown. Two rules matter most.

- **Unknown is never zero.** A prediction whose horizon is not covered by
  follow-up, or whose label is not yet mature, has no label. It is excluded from
  metrics and counted in label coverage.
- **Maturity.** A negative label is known only once `T + H + L` has passed,
  where `L` is the declared maximum ascertainment lag. A positive is known
  earlier. A training set that admitted every label known at a cutoff would
  select on the outcome, so both classes must be mature.

The main analysis uses the completed study, where every label is known. The
label-timing section of each report shows label coverage at an earlier data
cut, and compares metrics computed with mature labels against the labels an
impatient pipeline would have recorded the moment each horizon ended.

## 4. Splits

Patients, never rows, are assigned to partitions.

- **Patient holdout.** 60% train, 20% validation, 20% test, by a seeded
  permutation of patient identifiers. It asks how the model performs on new
  patients from the same period.
- **Chronological.** Fit on predictions whose labels were mature at a cutoff
  date (day 120), using outcomes ascertained by then. Test on predictions after
  the cutoff from patients first seen after it. A patient seen before the cutoff
  who returns afterwards is excluded from the test set, so the design is also
  patient-disjoint. It asks how a model built on that date would have done on
  the patients who came next.

The two designs answer different questions. The simulation has no drift over
calendar time, so no systematic difference between them is expected here.

## 5. Model

Logistic regression with an L2 penalty. Median imputation with missing-value
indicators, then standardisation. Everything that learns from data is fitted
on training patients. The regularisation strength is chosen from a fixed grid
by validation log loss. Test patients are used once. No threshold is selected
and no threshold-dependent metric is reported.

## 6. Two experiments

**A. Frozen model.** One model, trained on as-known features. Its parameters,
the prediction rows, the labels and the test patients are fixed. Only the
policy that builds the test features changes. This measures how sensitive the
*evaluation* is to the data pipeline. It is not a comparison of independently
trained models.

The naive-minus-as-known difference is split in sequence:

- revision effect = `latest_revised` − `pipeline_as_known`
- availability effect = `naive_event_time` − `latest_revised`

The two sum to the total. The split depends on the order in which the two
changes are applied.

**B. Training pipelines.** Two models with the same patients, labels, split,
model family and selection rule. One is prepared from naive features, the other
from as-known features. Each is evaluated both ways.

| Cell | Meaning |
| --- | --- |
| Naive-trained, naive-evaluated | What a naive pipeline would report about itself |
| Naive-trained, as-known-evaluated | What that model would deliver in deployment |
| As-known-trained, as-known-evaluated | The faithful pipeline, evaluated faithfully |

What it can attribute: the effect of the feature-time policy on this cohort
for these two fitted models. What it cannot: that one training pipeline is
better in general. Each interval is conditional on the fitted models.

## 7. Metrics

| Metric | Convention |
| --- | --- |
| AUROC | Mann-Whitney statistic; ties count one half |
| Average precision | Sum over score thresholds of (recall step) x precision. The step-function definition, as in scikit-learn's `average_precision_score`. Not the trapezoidal area under the precision-recall curve |
| Brier score | Mean squared difference between predicted probability and outcome |
| Calibration curve | Observed rate against mean prediction in ten equal-count groups |
| Calibration intercept | Intercept of a logistic model with logit(p) as a fixed offset; 0 is ideal |
| Calibration slope | Coefficient of logit(p) in a logistic model with free intercept; 1 is ideal |

Reported alongside: prevalence, number of patients, number of predictions,
number of patients with an event, missing-feature rates and label coverage.

**Weighting.** Metrics weight predictions equally by default. A
patient-weighted view, in which each patient's predictions share a total weight
of one, is reported for experiment A.

**Not estimable.** A metric that cannot be computed returns a reason instead
of a number: a single class, an empty sample, fewer than 10 patients, or a
calibration model that does not converge. Subgroups with fewer than 20 events,
20 non-events or 30 patients are flagged as unstable. These thresholds are
conventions.

The implementations in `evaluation/metrics.py` are checked against scikit-learn
with weights, and against literally duplicating rows.

## 8. Uncertainty

**Paired, patient-clustered bootstrap** (`evaluation/bootstrap.py`).

- The unit resampled is the patient. A patient drawn `k` times contributes all
  of its predictions `k` times.
- The same resample is applied to every condition, and each difference is
  formed inside the resample. That is the pairing.
- 2,000 resamples, fixed seed, percentile intervals at the 95% level.
- A resample in which a statistic cannot be computed is counted, not dropped
  silently. If fewer than 95% of resamples complete, no interval is reported and
  the reason is given. Requested and completed counts are stored for every
  estimate.

A resample is expressed as an integer weight per row, which is exactly
equivalent to duplicating rows and avoids re-sorting scores.

**What the intervals cover.** Sampling variability in the test patients, for
fixed models. They do not include the variability of model fitting, of the
choice of training patients, or of the simulation.

**What covers the rest.** Five independently simulated cohorts per scenario.
The cross-seed summary reports the mean, standard deviation and range of each
contrast across seeds, and how many seeds' intervals lie above zero, below
zero, or include it. Five seeds describe five simulated cohorts; they are not a
confidence interval.

The interval width is checked in a test against the analytic cluster standard
error of a mean, and against the too-narrow answer obtained by resampling rows.

## 9. Subgroups

Two prespecified attributes: site and age band. Each row shows patients,
predictions and events, and an instability flag. The results are descriptive.
They support no claim about fairness, clinical safety or causal benefit, and no
subgroup comparison is tested.

## 10. Reading the results

- A lower score under the as-known policy is not evidence of a worse engine.
  It means the naive number measured something the deployed pipeline could not
  have had.
- No direction or size of effect was required or expected. A difference that
  is small, null or in the unexpected direction is a result and is reported.
- The count of predictions affected by a timing mistake and the change in a
  metric are different quantities. One can be large while the other is small.

## 11. Changes made after seeing preliminary output

1. The regularisation grid was extended one decade downward after a trial run
   selected its lowest value. The lowest value is still selected in full runs;
   validation log loss is nearly flat across the grid. It was not changed again.
2. The comparison of mature labels with labels recorded at the horizon was
   added after the count of affected predictions was seen and before its effect
   on any metric was computed.
3. The number of audit examples stored per run was reduced from 200 to 25 to
   keep committed files small. This changes no statistic.

No parameter of the data-generating process was changed after any policy
comparison was run. The hazard intercept was set earlier, by looking only at
the outcome rate.

## References

Located by an automated research pass and not re-read by hand; see
[prior-art.md](prior-art.md) for how such sources are marked.

- Field CA, Welsh AH. Bootstrapping clustered data. *J R Stat Soc B* 2007;69(3):369-390.
- Rutter CM. Bootstrap estimation of diagnostic accuracy with patient-clustered data. *Acad Radiol* 2000;7(6):413-419.
- Obuchowski NA. Nonparametric analysis of clustered ROC curve data. *Biometrics* 1997;53(2):567-578.
- Van Calster B, et al. Calibration: the Achilles heel of predictive analytics. *BMC Med* 2019;17:230.
- Collins GS, et al. TRIPOD+AI statement. *BMJ* 2024;385:e078378.
