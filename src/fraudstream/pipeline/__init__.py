"""fraudstream.pipeline - the steps of the SageMaker training pipeline.

What: the code each pipeline step runs:
        prepare   (our image)            dataset version -> features -> train/valid/test
        train     (SageMaker XGBoost)    train_entry.py: fit the model, save xgboost-model
        evaluate  (our image)            score challenger AND champion on the same days,
                                         apply the gate, write metrics + model card
        register  (our image)            add the challenger to the model registry
      gate.py holds the promotion rule itself.
Why:  a model is produced by a recorded, repeatable run - not on a laptop - and is
      registered only if it isn't worse than the model it would replace. Every step
      reuses the package's own tested code (FeatureBuilder, train, evaluate, registry),
      so the pipeline can't drift from what the tests check.
Concept: CHAMPION vs CHALLENGER. The newest Approved model is the champion; a newly
      trained model is the challenger and must prove itself on the same test days
      before a person is even asked to review it.
"""
