# B31 Week 3 Real Data Start Artifacts

## Version

- Week 2 final commit: `fe48df1c9b92279c3e10ccd560cf506372df99b7`
- Week 2 tag: `week2_final_b31`
- Week 3 branch: `week3_real_data`
- Clean clone verified commit: `6918c023f00cfca1c5e319d89f958f083b8e68a7`
- Source reproduction commit: `6918c023f00cfca1c5e319d89f958f083b8e68a7`

## Contents

- `docs/README_real_data.md`: real data workflow README.
- `docs/B01_reproduce_from_clean_clone.md`: clean clone reproduction commands and result notes.
- `logs/pytest_week3_start.log`: full clean clone install, ingest, quality check, and pytest log.
- `../../runs/B31_source_star_export/`: tracked STAR export input used for the clean clone reproduction.
- `clean_clone_schedule_input/`: generated action schedule outputs from the clean clone.
- `clean_clone_standard_case/`: standard case produced by reorganizing existing STAR CSV outputs in the clean clone.

## Verification Summary

- Clean clone branch: `week3_real_data`
- Clean clone pytest: `80 passed in 8.68s`
- STAR output quality check: `quality_report_errors=0`, `quality_report_warnings=0`
- Generated schedule provenance: `git_commit=6918c023f00cfca1c5e319d89f958f083b8e68a7`
- Standard case provenance: `git_commit=6918c023f00cfca1c5e319d89f958f083b8e68a7`
