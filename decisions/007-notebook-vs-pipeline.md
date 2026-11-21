# 007: The notebook explores; the pipeline produces every servable model

Stage: S2 · Date: 2026-11-21 · Author: Himanshu Verma · Drafted by agent: yes

## Choice

The SageMaker notebook is for looking at data and trying ideas. Its runs log to their own MLflow experiment (`trip-duration-notebook`) and never register a model. Everything that can lead to a served model lives in the Dagster pipeline: the features (`trip_features` with the blocking `feature_contract`), the training set, training, evaluation, registering a version, and the `challenger` alias behind the blocking `beats_champion` gate.

## Rejected alternative

Let the notebook register promising models directly, to shorten the loop from idea to registry.

## Why

- A registered version is something S3 can promote and serve. It has to be reproducible (which commit, which months, which features version: all version tags), and it has to have passed the same checks every other version passed. A notebook run has neither: cells run out of order, and nothing forces the feature check or the gate.
- The pipeline is tested (`make test-pipelines`) and runs the same way every time; the notebook is the place to find out what is worth putting into the pipeline.
- The cost I accept: an idea found in the notebook takes a code change and a pipeline run before it can be compared fairly. That is also what makes the comparison fair.

## What would show this was wrong

- Ideas stall because moving them into the pipeline takes longer than the experiment itself.
- A version in the registry has no `git_sha` or `training_window` tag, which would mean something other than the pipeline registered it.
