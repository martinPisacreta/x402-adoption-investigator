# Attribution

`hmem-vectors.json` is a verbatim copy of `engines/hmem/conformance/vectors/vectors.json` from the Meterless
repository (reference architectures H-MEM and World Model), licensed under the Apache License, Version 2.0
(see `engines/hmem/LICENSE` in that repository). It is replayed by `tests/test_hmem_conformance.py` against the
Python port in `investigator/hmem/`, because the upstream TypeScript conformance runner cannot load Python.
The file is not modified.
