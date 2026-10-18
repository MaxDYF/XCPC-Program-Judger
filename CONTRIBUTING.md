# Contributing

Open an issue describing the observed behavior and a minimal reproduction before a large change. Pull requests should explain the method, the expected effect on calibration or clarity grading, and any new data provenance. Run `pytest -q` and `python scripts/check_release.py` before submission.

Do not commit API keys, user uploads, local databases, model transcripts, or full third-party problem statements/editorials/source code without redistribution permission. Use synthetic fixtures for tests. Changes to scoring behavior should document the rubric and its limitations in both README files.
