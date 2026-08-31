# Source status ledger

Updated 2026-08-31. This ledger describes current public behavior. It does not authorize a data release.

## Shipped

| Dataset | Grain | Years | Status | Update check | Failed gate |
| --- | --- | --- | --- | --- | --- |
| PSA GDP | Province and virtual NCR | 2018 to 2025 | Shipped | Review each official release | None |
| PSA population | Province and virtual NCR | 2020 and 2024 anchors | Shipped | Review each census release | None |
| PSA poverty depth | Published source-native area | Through 2023 | Shipped | Review each official release | None |
| PhilGEPS awards | Province and virtual NCR | 2014 to 2024 | Shipped | Review each new snapshot | None |

Population estimates use the 2020 value through 2020, interpolate 2021 through 2023, and use the official 2024 POPCEN value. The product never averages rates across areas. It recomputes rates from additive components when the source contract permits that operation.

PhilGEPS values are contract awards. They do not show cash disbursement.

## Unavailable

| Dataset | Grain | Years | Status | Update check | Failed gate |
| --- | --- | --- | --- | --- | --- |
| PhilGEPS 2025 | Award record | 2025 | Unavailable | Review corrected snapshot | Date coverage, invalid and future dates, correction comparison |
| PSA 2025 poverty workbook | Source-native area | 2025 | Unavailable | Check official release | Source, precision, crosswalk, weights, revision policy |
| PSA 2025 FIES workbook | Source-native area | 2025 | Unavailable | Check official release | Source, precision, crosswalk, weights, revision policy |

If an official 2025 workbook passes its gates, label its figures preliminary until the publisher gives a final revision status.

## Adoption-gated

| Dataset | Grain | Years | Status | Update check | Failed gate |
| --- | --- | --- | --- | --- | --- |
| DBM COMPASS | Not accepted | Not accepted | Adoption-gated | Review before adoption | Credentials, grain, coverage, and reproducible source contract |
| CBMS | Not accepted | Not accepted | Adoption-gated | Review before adoption | Access, grain, coverage, suppression rules, and reproducible source contract |

## Future work stays outside the shipped product

Future work can add a source only after its contract and tests pass. The product keeps source-native grain and does not force gated data into province-year comparisons.
