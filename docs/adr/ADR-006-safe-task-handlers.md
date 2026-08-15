# ADR-006: Fixed safe task handlers

## Decision
V1 accepts fixed, safe handlers instead of arbitrary user code: `sleep`, `csv_stats`, `image_resize`, and `http_check`.

## Rationale
Arbitrary public code can access host credentials/data, exhaust resources, escape poorly isolated execution, and create severe supply-chain and abuse risks. A fixed registry permits explicit input/output schemas, payload limits, timeout rules, logging, and authorization. Handler results will be validated before persistence.

## Future direction
Dangerous processing may later run in isolated containers with CPU, memory, network, filesystem, and time limits. Worker capability labels such as `cpu`, `gpu`, and `memory-heavy` can route compatible work. This does not authorize arbitrary code in V1.
