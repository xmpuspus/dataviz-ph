# Source status ledger

Updated 2026-08-31. This ledger describes current public behavior. It does not authorize a data release.

## Shipped

### PSA GDP

- The grain is province and virtual NCR.
- The years are 2018 to 2025.
- dataviz.ph ships this source.
- Check each official release.
- The build uses the committed geography contract.

### PSA population

- The grain is province and virtual NCR.
- The years use 2020 and 2024 anchors.
- dataviz.ph ships this source.
- Check each census release.
- The build estimates 2021 through 2023 between anchors.

### PSA poverty depth

- The grain is province and virtual NCR.
- The years end in 2023.
- dataviz.ph ships this source.
- Check each official release.
- The source ends in 2023.

### PhilGEPS awards

- The grain is province and virtual NCR.
- The source covers reviewed snapshot years.
- dataviz.ph ships this source.
- Review a new snapshot before use.
- Awards are not disbursements.

## Unavailable

### PhilGEPS 2025

- The grain is an award record.
- The year is 2025.
- The release status is unavailable.
- Review a complete corrected snapshot.
- The failed gate has incomplete dates, invalid dates, future dates, and correction comparison.

### PSA 2025 poverty workbook

- The grain is a source-native area.
- The year is 2025.
- The release status is unavailable.
- Check the official workbook release.
- The failed gate has source, precision, crosswalk, weight, and revision policy.

### PSA 2025 FIES workbook

- The grain is a source-native area.
- The year is 2025.
- The release status is unavailable.
- Check the official workbook release.
- The failed gate has source, precision, crosswalk, weight, and revision policy.

If an official 2025 workbook passes its gates, label its figures preliminary until the publisher gives a final revision status.

## Adoption-gated

### DBM COMPASS

- The grain is not accepted.
- The years are not accepted.
- The release status is adoption-gated.
- Review before adoption.
- The needed gate has credentials, grain, coverage, and a reproducible source contract.

### CBMS

- The grain is not accepted.
- The years are not accepted.
- The release status is adoption-gated.
- Review before adoption.
- The needed gate has access, grain, coverage, suppression rules, and a reproducible source contract.

## Future work stays outside the shipped product

Future work can add a source only after its contract and tests pass. The product keeps source-native grain and does not force gated data into province-year comparisons.
